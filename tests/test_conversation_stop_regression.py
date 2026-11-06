"""Real pinned SDK and a stalled localhost stream; no paid model requests."""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from narrative.conversation_runtime import close_project_runtime
from narrative.conversation_session import open_conversation_session
from narrative.harness import HarnessRunner, HarnessSettings
from narrative.leases import ProjectLease
from narrative.operations import Operations


@contextmanager
def stalled_model():
    entered, release = threading.Event(), threading.Event()
    requests = []
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            if len(requests) == 1:
                self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n')
                self.wfile.flush()
                entered.set()
                release.wait(10)
                return
            body = {'choices': [{'index': 0, 'delta': {'content': 'next task completed'}, 'finish_reason': 'stop'}]}
            try:
                self.wfile.write(('data: '+json.dumps(body)+'\n\ndata: [DONE]\n\n').encode())
            except OSError:
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_port, entered, release, requests
    finally:
        release.set()
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize('control_fault', [False, True])
def test_cancel_stalled_real_sdk_settles_and_exact_close_releases_owner(tmp_path, monkeypatch, control_fault):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    ops = Operations.create_graph_project(tmp_path / 'stop.nproj')
    with stalled_model() as (port, entered, release, requests):
        settings = HarnessSettings(provider='custom', model='offline', base_url=f'http://127.0.0.1:{port}/v1')
        wrapper, session = open_conversation_session(ops, settings, 'local-only', HarnessRunner, task_id='first-task')
        runtime, runner, old_nonce = wrapper.manager, wrapper.runner, wrapper.binding_nonce
        process = runner._harness.client._proc
        pid = process.pid
        result = {}
        def run():
            try:
                result['reply'] = wrapper.agent_turn('initial task with blocked upstream', session)
            except Exception as exc:
                result['error'] = exc
        worker = threading.Thread(target=run)
        worker.start()
        receipts = []
        try:
            assert entered.wait(8)
            if not control_fault:
                runtime.steer(wrapper.chat_id, 'first-task', old_nonce, 'pending-steer-code', message_id='steer-before-cancel', on_status=receipts.append)
            runtime.signal_stop(old_nonce)
            if control_fault:
                def broken(*args, **kwargs):
                    raise TimeoutError('isolated control-channel failure')
                monkeypatch.setattr(runtime.host, 'interrupt', broken)
                with pytest.raises(TimeoutError, match='control-channel'):
                    runtime.interrupt(old_nonce)
            else:
                runtime.interrupt(old_nonce)
            worker.join(6)
            assert not worker.is_alive(), 'cancel must abort the real stalled gateway socket'
            assert 'error' in result and result['error'].metadata['status'] == 'cancelled'
            wrapper.close(completed=False)
            assert not runtime.closed and process.poll() is None
            if not control_fault:
                assert receipts[-1]['status'] == 'not_delivered'
            following, same_session = open_conversation_session(ops, settings, 'local-only', HarnessRunner, task_id='next-task')
            try:
                assert following.manager is runtime and following.runner is runner and same_session == session
                runtime.interrupt(old_nonce)
                assert not runtime._binding['cancelled']
                answer = following.agent_turn('new task only', same_session)
                assert answer.raw == 'next task completed'
                assert 'pending-steer-code' not in json.dumps(requests[-1])
                assert runner._harness.client._proc.pid == pid
            finally:
                following.close()
        finally:
            release.set()
            worker.join(3)
            wrapper.close(completed=False)
            close_project_runtime(ops)
        assert process.poll() is not None
        lease = ProjectLease(runtime.sdk_directory / 'runtime-owner.lock')
        lease.close()
