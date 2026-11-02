"""No elapsed task cutoff: real SDK streams/tools, network idle, and manual stop."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from current_format_assertions import assert_no_staging
from test_native_model_diagnostics import require_runtime, tool_delta


@contextmanager
def streamed_provider(frames):
    requests = []
    disconnected = threading.Event()

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            try:
                for delta, finish, delay in frames(len(requests), request):
                    if delay:
                        time.sleep(delay)
                    chunk = {'id': 'streamed-local', 'model': 'local-stub', 'object': 'chat.completion.chunk',
                             'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                    if finish is not None:
                        chunk['usage'] = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}
                    self.wfile.write(('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n').encode())
                    self.wfile.flush()
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                disconnected.set()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests, disconnected
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pinned_sdk_slow_tool_and_active_stream_exceed_idle_budget_without_total_cutoff(tmp_path):
    require_runtime()
    from narrative.harness import HarnessRunner, HarnessSettings
    from narrative.task_tool_host import TaskToolHost

    tool_calls = []

    def handler(name, args):
        tool_calls.append((name, args))
        time.sleep(5.3)  # No model HTTP request exists while this local tool runs.
        return {'text': '慢工具结果'}

    def frames(number, request):
        if number == 1:
            delta, finish = tool_delta('slow_read', '{}')
            yield delta, None, 0
            yield {}, finish, 0
        else:
            assert number == 2 and any(row.get('role') == 'tool' for row in request['messages'])
            yield {'role': 'assistant', 'content': '持续输出'}, None, 0
            for _ in range(28):
                yield {'content': '。'}, None, .2
            yield {}, 'stop', 0

    catalog = [{'name': 'slow_read', 'description': 'Synthetic local slow tool',
                'parameters': {'type': 'object', 'properties': {}}}]
    with streamed_provider(frames) as (url, requests, _disconnected):
        host = TaskToolHost(catalog, handler)
        runner = HarnessRunner(HarnessSettings(provider='custom', model='local-stub', base_url=url, timeout_seconds=5),
                               'offline', tmp_path / 'sdk', task_tools=host.config)
        try:
            started = time.monotonic()
            reply = runner.agent_turn('运行慢工具并读取持续流。', 'no-total-timeout')
            elapsed = time.monotonic() - started
            assert elapsed > 10
            assert reply.metadata['finish_reason'] == 'completed'
            assert reply.raw.startswith('持续输出')
            assert len(requests) == 2 and len(tool_calls) == 1
            assert runner._harness.client._proc.poll() is None
        finally:
            runner.close()
            host.close()


def test_pinned_sdk_still_reports_network_stream_idle(tmp_path):
    require_runtime()
    from narrative.harness import HarnessRunner, HarnessSettings

    def frames(_number, _request):
        yield {'role': 'assistant', 'content': '开始'}, None, 0
        yield {'content': '迟到'}, None, 6
        yield {}, 'stop', 0

    with streamed_provider(frames) as (url, requests, _disconnected):
        runner = HarnessRunner(HarnessSettings(provider='custom', model='local-stub', base_url=url, timeout_seconds=5),
                               'offline', tmp_path / 'sdk')
        try:
            reply = runner.agent_turn('读取网络空闲失败。', 'network-idle')
            assert reply.metadata['finish_reason'] != 'completed'
            assert reply.metadata.get('service_error') or reply.metadata.get('sdk_error_diagnostics')
            assert len(requests) == 1
        finally:
            runner.close()


def test_reasoning_only_network_activity_does_not_trigger_sdk_idle_or_enter_visible_history(tmp_path):
    require_runtime()
    from narrative.harness import HarnessRunner, HarnessSettings

    def frames(_number, _request):
        yield {'role': 'assistant', 'reasoning_content': 'PRIVATE_THOUGHT'}, None, 0
        for _ in range(28):
            yield {'reasoning_content': 'PRIVATE_THOUGHT'}, None, .2
        yield {'content': '正常可见正文'}, None, 0
        yield {}, 'stop', 0

    with streamed_provider(frames) as (url, requests, _disconnected):
        runner = HarnessRunner(HarnessSettings(provider='custom', model='local-stub', base_url=url, timeout_seconds=5),
                               'offline', tmp_path / 'sdk')
        try:
            reply = runner.agent_turn('等待持续网络流，不显示思考。', 'reasoning-idle')
            assert reply.metadata['finish_reason'] == 'completed', reply.metadata.get('sdk_error_diagnostics')
            assert reply.raw == '正常可见正文'
            assert 'PRIVATE_THOUGHT' not in json.dumps(reply.metadata, ensure_ascii=False)
            assert len(requests) == 1
        finally:
            runner.close()


@pytest.mark.parametrize('partial_commit', [False, True])
def test_real_sdk_manual_stop_button_cancels_current_task_and_preserves_commits(tmp_path, monkeypatch, partial_commit):
    require_runtime()
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('QT_QUICK_BACKEND', 'software')
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    monkeypatch.setenv('ARK_API_KEY', 'offline-placeholder')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'offline-placeholder')
    from agent_fixtures import TEXT, draft_result
    from PySide6.QtCore import QPointF, Qt, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from narrative.chat_store import ChatStore
    from narrative.harness import HarnessSettings
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    entry = tmp_path / 'manual-stop.nproj'
    ops = Operations.create_graph_project(entry)
    sid = ops.import_source('合成原文', TEXT)
    before = ops.graph_project_info()['revision']
    streaming, release, followup = threading.Event(), threading.Event(), threading.Event()

    def frames(number, _request):
        if followup.is_set():
            assert "停止上下文标记" in str(_request["messages"])
            yield {"role": "assistant", "content": "原进程上下文仍在"}, "stop", 0
            return
        if partial_commit and number == 1:
            delta, finish = tool_delta('submit_extraction', json.dumps({'source_id': sid, 'result': draft_result()}, ensure_ascii=False))
            yield delta, None, 0
            yield {}, finish, 0
            return
        streaming.set()
        yield {'role': 'assistant', 'content': '正常持续输出'}, None, 0
        while not release.is_set():
            yield {'content': '。'}, None, .1
        yield {}, 'stop', 0

    app = QApplication.instance() or QApplication([])
    with streamed_provider(frames) as (url, requests, _disconnected):
        ops.save_provider_profile(HarnessSettings(provider='custom', model='local-stub', base_url=url,
                                                 timeout_seconds=5).model_dump())
        bridge = NativeBridge()
        results, errors = [], []
        bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
        bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty('bridge', bridge)
        engine.load(QUrl.fromLocalFile(str(QML_PATH)))
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        assert isinstance(window.contentItem(), QQuickItem)
        panel = window.findChild(object, 'assistantPanel')
        stop = panel.findChild(object, 'assistantStop')

        def wait(check):
            deadline = time.monotonic() + 15  # Test observation bound, not a product task deadline.
            while time.monotonic() < deadline:
                app.processEvents()
                if check():
                    return
                time.sleep(.01)
            pytest.fail(f'No manual-stop completion: {errors}')

        try:
            bridge.openProject(str(entry))
            wait(lambda: any(kind == 'project' for kind, _ in results))
            window.setProperty('assistantOpen', True)
            wait(lambda: not window.property('busy'))
            bridge.assistantTurn('合成手动停止验证，记住停止上下文标记。', '')
            wait(lambda: streaming.is_set() and stop.property('visible') and stop.property('enabled'))
            runtime = bridge._assistant_runtime
            process = bridge._assistant_runner._harness.client._proc
            assert process.poll() is None
            task_ids = panel.property('activeTaskIds')
            task_ids = task_ids.toVariant() if hasattr(task_ids, 'toVariant') else task_ids
            task_id = task_ids[panel.property('chatId')]
            bridge.assistantStop('unrelated-chat', task_id)
            bridge.assistantStop(panel.property('chatId'), 'stale-task')
            assert not runtime.closed and process.poll() is None
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                             stop.mapToScene(QPointF(stop.width() / 2, stop.height() / 2)).toPoint())
            wait(lambda: any(kind == 'assistant' and payload.get('status') == 'cancelled' for kind, payload in results))
            release.set()
            wait(lambda: not window.property('busy'))
            result = next(payload for kind, payload in results if kind == 'assistant' and payload.get('status') == 'cancelled')
            assert not errors, errors
            assert process.poll() is None and not runtime.closed
            assert bridge._assistant_runner is runtime.runner
            assert len(requests) == (2 if partial_commit else 1)
            revision = ops.graph_project_info()['revision']
            assert (revision > before) is partial_commit
            assert result['revision'] == revision and result['graph_reset_required'] is partial_commit
            assert_no_staging(ops)
            store = ChatStore(ops.project_info().path)
            saved = store.load(result['chat_id'])
            cancelled = next(row for row in saved['messages'] if row.get('kind') == 'assistant_cancelled')
            assert cancelled['error_code'] == 'user_stopped' and cancelled['finish_reason'] == 'cancelled'
            assert ('已完成的入库变更仍保留' if partial_commit else '未产生新的入库变更') in cancelled['text']
            assert 'stdout closed' not in cancelled['text'] and '超时' not in cancelled['text']
            task = json.loads((Path(result['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
            assert task['status'] == 'cancelled'
            assert not panel.property('busy') and not stop.property('visible')
            transcript = panel.findChild(object, 'assistantTranscript')
            transcript.positionViewAtEnd()
            transcript.forceLayout()
            def row_data(row):
                data = row.property('modelData')
                return data.toVariant() if hasattr(data, 'toVariant') else data

            cancelled_row = next(
                row for row in transcript.property('contentItem').childItems()
                if row.property('modelData')
                and row_data(row)['kind'] == 'assistant_cancelled')
            stop_record = cancelled_row.findChild(object, 'assistantDiagnosticLink')
            assert stop_record.property('visible') and stop_record.property('text') == '查看停止记录'
            followup.set()
            bridge.assistantTurn('继续同一会话', '')
            bridge.assistantStop(result['chat_id'], task_id)  # stale task cannot stop its successor
            wait(lambda: any(kind == 'assistant' and payload.get('answer') == '原进程上下文仍在' for kind, payload in results))
            wait(lambda: not window.property('busy'))
            assert not errors and bridge._assistant_runtime is runtime
            assert bridge._assistant_runner._harness.client._proc is process and process.poll() is None
            assert not any(row.get('kind') == 'runtime_restart' for row in store.load(result['chat_id'])['messages'])

        finally:
            release.set()
            bridge.close()
            engine.deleteLater()
            app.processEvents()
