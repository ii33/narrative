"""Task snapshots and per-provider tool visibility, with no real model requests."""
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from narrative import assistant, tool_api
from narrative.chat_store import ChatStore
from narrative.conversation_runtime import close_project_runtime
from narrative.conversation_session import current_access_mode, open_conversation_session
from narrative.harness import HarnessReply, HarnessSettings
from narrative.operations import Operations


class ResidentStub:
    instances = []
    def __init__(self, *args, **kwargs):
        self._closed = False
        self.active_session = None
        self.instances.append(self)
    def resume(self, session):
        return True
    def agent_turn(self, instruction, session, **kwargs):
        self.active_session = None
        return HarnessReply('offline response', {'finish_reason': 'completed'})
    def close(self, **kwargs):
        self._closed = True
    def set_tool_allowlist(self, provider):
        self.allowed = provider


def test_full_snapshot_restricts_next_task_without_restart_or_nonce_leak(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    entry = tmp_path / 'access.nproj'
    ops = Operations.create_graph_project(entry)
    chat = ChatStore(ops.project_info().path).create()
    ops.set_assistant_access(chat['id'], 'full')
    settings = HarnessSettings(provider='custom', model='offline', base_url='http://127.0.0.1:1/v1')
    all_tools = assistant.tool_catalog(access_mode='full')
    wrapper = None
    try:
        wrapper, session = open_conversation_session(ops, settings, 'offline-secret', ResidentStub, chat_id=chat['id'], task_id='full-task', task_tools=all_tools, tool_handler=lambda name,args: tool_api.call(name,args,project=entry))
        manager, runner, nonce = wrapper.manager, wrapper.runner, wrapper.binding_nonce
        assert current_access_mode(ops) == 'full'
        ops.set_assistant_access(chat['id'], 'standard')
        assert current_access_mode(ops) == 'full'
        outside = tmp_path / 'outside-project.txt'
        manager._dispatch('write_host_file', {'path': str(outside), 'content': 'hello offline-secret'}, session, nonce)
        assert outside.read_text() == 'hello offline-secret'
        assert manager._dispatch('read_host_file', {'path': str(outside)}, session, nonce)['content'] == 'hello [REDACTED]'
        with pytest.raises(ValueError, match='未授权'):
            manager._dispatch('set_assistant_access', {'chat_id': chat['id'], 'access_mode': 'full'}, session, nonce)
        assert wrapper.agent_turn('only respond', session).metadata['access_mode'] == 'full'
        wrapper.close()
        wrapper, next_session = open_conversation_session(ops, settings, 'offline-secret', ResidentStub, chat_id=chat['id'], task_id='standard-task', task_tools=all_tools, tool_handler=lambda name,args: tool_api.call(name,args,project=entry))
        assert wrapper.manager is manager and wrapper.runner is runner and next_session == session
        assert current_access_mode(ops) == 'standard' and 'read_host_file' not in manager._model_tool_allowlist()
        with pytest.raises(ValueError, match='未授权'):
            manager._dispatch('read_host_file', {'path': str(outside)}, session, wrapper.binding_nonce)
        with pytest.raises(ValueError, match='过期'):
            manager._dispatch('read_host_file', {'path': str(outside)}, session, nonce)
        with pytest.raises(ValueError, match='过期'):
            manager._dispatch('read_host_file', {'path': str(outside)}, 'another-chat', wrapper.binding_nonce)
        manager.signal_stop(wrapper.binding_nonce)
        with pytest.raises(ValueError, match='过期'):
            manager._dispatch('read_source', {}, session, wrapper.binding_nonce)
    finally:
        if wrapper is not None:
            wrapper.close()
        close_project_runtime(ops)
    assert current_access_mode(ops) == 'standard'


def test_gateway_filters_actual_forwarded_tool_schemas_without_rewriting_history():
    from narrative.model_gateway import ModelGateway
    seen = []
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 1, 'total_tokens': 11}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = HarnessSettings(provider='custom', model='offline', base_url=f'http://127.0.0.1:{server.server_port}/v1')
    gateway = ModelGateway(settings, 'offline-key')
    allowed = {'read_source'}
    gateway.tool_allowlist_provider = lambda: allowed
    history = [{'role': 'user', 'content': 'ordinary question'}]
    tools = [{'type': 'function', 'function': {'name': name, 'parameters': {'type': 'object'}}} for name in ['read_source', 'read_host_file', 'set_assistant_access']]
    try:
        address = urlsplit(gateway.base_url)
        for expected in [{'read_source'}, {'read_source', 'read_host_file'}, {'read_source'}]:
            allowed = expected
            with gateway.session('same-sdk-session'):
                conn = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
                conn.request('POST', address.path + '/chat/completions', json.dumps({'model': 'offline', 'messages': history, 'tools': tools}), {'Authorization': 'Bearer ' + gateway.token, 'Content-Type': 'application/json'})
                response = conn.getresponse()
                assert response.status == 200
                response.read()
                conn.close()
            assert {t['function']['name'] for t in seen[-1]['tools']} == expected
            assert seen[-1]['messages'] == history
    finally:
        gateway.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_native_full_focus_is_not_frozen_and_async_prepare_captures_send_mode(tmp_path, monkeypatch):
    from test_chat_scroll import transcript_shell
    from test_screenplay_ui import create_fixture

    from narrative import native_bridge
    from narrative.conversation_runtime import get_existing_runtime
    attempts = []
    class NativeRunner(ResidentStub):
        def agent_turn(self, instruction, session, **kwargs):
            runtime = get_existing_runtime(ops)
            try:
                runtime._dispatch('save_asset', {'payload': {'category': 'material', 'title': 'focus-independent'}}, session, runtime._binding['nonce'])
                attempts.append('written')
            except ValueError:
                attempts.append('blocked')
            return HarnessReply('offline response', {'finish_reason': 'completed'})
    monkeypatch.setattr(native_bridge, 'HarnessRunner', NativeRunner)
    monkeypatch.setenv('NARRATIVE_ACCESS_TEST_KEY', 'offline-only')
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        ops.save_provider_profile(HarnessSettings(provider='custom', model='offline', base_url='http://127.0.0.1:1/v1', key_env='NARRATIVE_ACCESS_TEST_KEY').model_dump())
        script = create_fixture(ops)
        segment = ops.read_screenplay(script)['segments'][0]
        table = ops.apply_scene_table_changes(segment['id'], [{'op': 'insert', 'unit': {'plan': {'title': '焦点场'}}}], segment['outline_revision'], reason='offline focus fixture')
        target = {'kind': 'unit', 'ids': [table['units'][0]['id']], 'screenplay_id': script}
        task = ops.prepare_script_writing_task(target)
        chat_id = ui.a['id']
        for mode in ['standard', 'full']:
            ops.set_assistant_access(chat_id, mode)
            before = len([row for kind,row in ui.results if kind == 'assistant'])
            ui.bridge._assistant_turn('创建材质，页面场次仅为焦点', '', chat_id, writing_task=task)
            ui.wait(lambda before=before: len([row for kind,row in ui.results if kind == 'assistant']) > before)
        assert attempts == ['blocked', 'written']
        assert len(ops.list_assets('material')['assets']) == 1
        runtime = ui.bridge._assistant_runtime
        assert runtime is not None and not runtime.closed
        captured = []
        def queued(kind, work, apply=None):
            captured.append((work,apply))
        monkeypatch.setattr(ui.bridge, '_project_task', queued)
        calls = []
        monkeypatch.setattr(ui.bridge, '_assistant_turn', lambda *args,**kwargs: calls.append(kwargs))
        ops.set_assistant_access(chat_id, 'full')
        ui.bridge.screenplayWritingTurn('完整模式原始发送', target)
        ops.set_assistant_access(chat_id, 'standard')
        work, apply = captured[0]
        apply(work(ops))
        assert calls[0]['access_mode_override'] == 'full'


def test_host_write_audit_omits_reversible_payload_without_changing_execution():
    import base64

    from narrative.assistant_audit import sanitize_host_audit
    content = base64.b64encode(b'actual-sensitive-value').decode()
    args = {'path': 'C:/external.txt', 'content': content, 'encoding': 'base64'}
    record = {'tool_calls': [{'name': 'write_host_file', 'arguments': json.dumps(args), 'argument_fragments': [content[:4], content[4:]]}], 'raw': 'raw provider response ' + content}
    safe = sanitize_host_audit(record)
    assert content not in json.dumps(safe) and 'actual-sensitive-value' not in json.dumps(safe)
    assert args['content'] == content
    structured = json.loads(safe['tool_calls'][0]['arguments'])
    assert structured['content_omitted'] and structured['content_bytes'] == len(content)
    assert sanitize_host_audit(safe) == safe


def test_implicit_headless_host_owner_background_transfer_and_cancel_cleanup(tmp_path, monkeypatch):
    import subprocess
    import sys

    from narrative.assistant_host import host_access_context
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    ops = Operations.create_graph_project(tmp_path / 'implicit.nproj')
    settings = HarnessSettings(provider='custom', model='offline', base_url='http://127.0.0.1:1/v1')
    wrapper = None
    commands = []
    try:
        wrapper, session = open_conversation_session(ops, settings, 'offline', ResidentStub, access_mode='full', tool_handler=lambda n,a: tool_api.call(n,a,project=tmp_path/'implicit.nproj'))
        manager, owner = wrapper.manager, wrapper._host_task_id
        cmd = subprocess.list2cmdline([sys.executable, '-c', 'import time; time.sleep(20)'])
        # A valid action before first SDK call has the same task owner afterward.
        first = manager._dispatch('run_host_command', {'command': cmd, 'cwd': str(tmp_path), 'background': True}, session, wrapper.binding_nonce)
        commands.append(first['command_id'])
        wrapper.agent_turn('continue', session)
        assert manager._binding['task_id'] == owner == wrapper._host_task_id
        wrapper.agent_turn('continue again', session)
        assert manager._binding['task_id'] == owner
        wrapper.close()
        wrapper, session = open_conversation_session(ops, settings, 'offline', ResidentStub, access_mode='full', tool_handler=lambda n,a: tool_api.call(n,a,project=tmp_path/'implicit.nproj'))
        state = wrapper.manager._dispatch('read_host_command', {'command_id': first['command_id']}, session, wrapper.binding_nonce)
        assert state['detached'] is True
        second = wrapper.manager._dispatch('run_host_command', {'command': cmd, 'cwd': str(tmp_path)}, session, wrapper.binding_nonce)
        commands.append(second['command_id'])
        owner = wrapper._host_task_id
        wrapper.manager.signal_stop(wrapper.binding_nonce)
        wrapper.close(completed=False)
        with host_access_context('full', owner, threading.Event(), ops.project_info().path):
            stopped = ops.read_host_command(second['command_id'])
            assert stopped['status'] in {'cancelled', 'stopped'}
    finally:
        if wrapper is not None:
            wrapper.close()
        close_project_runtime(ops)


def test_implicit_host_owner_does_not_reuse_identity_after_runtime_restarts(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    ops = Operations.create_graph_project(tmp_path / 'restart.nproj')
    settings = HarnessSettings(provider='custom', model='offline', base_url='http://127.0.0.1:1/v1')
    first, _ = open_conversation_session(ops, settings, 'offline', ResidentStub, access_mode='standard')
    owner = first._host_task_id
    first.close()
    close_project_runtime(ops)
    second, _ = open_conversation_session(ops, settings, 'offline', ResidentStub, access_mode='standard')
    try:
        assert second._host_task_id != owner
        assert second._host_task_id.startswith('host-task-')
    finally:
        second.close()
        close_project_runtime(ops)
