"""Real pinned SDK malformed-tool diagnostics against a local provider stub only."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from current_format_assertions import action_receipts, assert_no_staging

BAD_ARGUMENTS = '{"source_id":"source" as_placeholder}'


def require_runtime():
    from narrative.harness import SDK_VERSION
    try:
        import deepseek_harness_runtime
        if version('deepseek-harness-sdk') != SDK_VERSION or not deepseek_harness_runtime.bundled_runtime_path().is_file():
            pytest.skip('Pinned native SDK runtime unavailable')
    except (ImportError, PackageNotFoundError, FileNotFoundError):
        pytest.skip('Pinned native SDK runtime unavailable')


@contextmanager
def stub_model(action):
    requests, errors = [], []

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            try:
                delta, finish = action(len(requests), request)
                chunks = [
                    {'id': 'diagnostics-local', 'model': 'local-stub', 'object': 'chat.completion.chunk',
                     'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                    {'id': 'diagnostics-local', 'model': 'local-stub', 'object': 'chat.completion.chunk',
                     'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                     'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}},
                ]
                raw = (''.join('data: ' + json.dumps(x, ensure_ascii=False) + '\n\n' for x in chunks)
                       + 'data: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
            except Exception as exc:
                errors.append(repr(exc))
                raw = b'{"error":{"message":"Local diagnostics assertion failed"}}'
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests, errors
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def tool_delta(name, arguments, identifier='tool-1'):
    return {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': identifier, 'type': 'function',
             'function': {'name': name, 'arguments': arguments}}]}, 'tool_calls'


def test_same_native_sdk_session_can_receive_new_feedback_after_a_malformed_turn(tmp_path):
    """Feasibility experiment: a new caller input, never an automatic retry."""
    require_runtime()
    from narrative.harness import HarnessRunner, HarnessSettings
    from narrative.task_tool_host import TaskToolHost

    calls = []

    def action(number, request):
        if number == 1:
            return tool_delta('read_source', BAD_ARGUMENTS)
        assert 'EXPLICIT_NEW_FORMAT_FEEDBACK' in json.dumps(request['messages'])
        assert 'PRIVATE_CONTEXT_MARKER' in json.dumps(request['messages'])
        if number == 2:
            return tool_delta('read_source', '{"source_id":"source"}', 'corrected')
        assert number == 3
        assert any(row.get('role') == 'tool' for row in request['messages'])
        return {'role': 'assistant', 'content': '格式已修订，读取成功。'}, 'stop'

    catalog = [{'name': 'read_source', 'description': 'Read synthetic source',
                'parameters': {'type': 'object', 'properties': {'source_id': {'type': 'string'}}, 'required': ['source_id']}}]
    with stub_model(action) as (url, requests, errors):
        host = TaskToolHost(catalog, lambda name, args: calls.append((name, args)) or {'text': '合成原文'})
        runner = HarnessRunner(HarnessSettings(provider='custom', model='local-stub', base_url=url,
                                              timeout_seconds=20), 'offline-placeholder', tmp_path / 'sdk', task_tools=host.config)
        try:
            first = runner.agent_turn('PRIVATE_CONTEXT_MARKER。请读取原文。', 'format-experiment')
            assert first.metadata['finish_reason'] == 'error'
            assert 'invalid json' in json.dumps(first.metadata['sdk_error_diagnostics']).lower()
            assert len(requests) == 1 and calls == []
            process = runner._harness.client._proc
            assert process and process.poll() is None
            second = runner.agent_turn('EXPLICIT_NEW_FORMAT_FEEDBACK：上一输出的read_source工具参数不是合法JSON。'
                                       '请在同一会话重新生成合法参数后完成读取。', 'format-experiment')
            assert runner._harness.client._proc is process and process.poll() is None
            assert second.metadata['finish_reason'] == 'completed'
            assert second.raw == '格式已修订，读取成功。'
            assert len(requests) == 3 and len(calls) == 1
            assert not errors, errors
        finally:
            runner.close()
            host.close()


@pytest.mark.parametrize('partial_commit', [False, True])
@pytest.mark.parametrize('recover', [False, True])
def test_native_malformed_tool_diagnostics_preserve_raw_and_actual_commit_state(tmp_path, monkeypatch, partial_commit, recover):
    require_runtime()
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('QT_QUICK_BACKEND', 'software')
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    monkeypatch.setenv('ARK_API_KEY', 'offline-placeholder')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'offline-placeholder')
    from agent_fixtures import TEXT, draft_result
    from PySide6.QtCore import QObject, QPointF, Qt, QUrl, Slot
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from narrative.chat_store import ChatStore
    from narrative.harness import HarnessSettings
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    entry = tmp_path / 'invalid-tool.nproj'
    ops = Operations.create_graph_project(entry)
    sid = ops.import_source('合成原文', TEXT)
    initial_revision = ops.graph_project_info()['revision']

    pids = []
    def action(number, request):
        process = bridge._assistant_runner._harness.client._proc
        pids.append(process.pid)
        if partial_commit and number == 1:
            return tool_delta('submit_extraction', json.dumps({'source_id': sid, 'result': draft_result()}, ensure_ascii=False))
        bad_number = 2 if partial_commit else 1
        if number == bad_number:
            delta, _finish = tool_delta('read_source', BAD_ARGUMENTS, 'malformed')
            return delta, 'tool_calls' if recover else 'stop'
        assert recover
        serialized = json.dumps(request['messages'], ensure_ascii=False)
        assert 'as_placeholder' in serialized and 'position' in serialized and 'read_source' in serialized
        assert '离线格式失败工程验证' in serialized
        if number == bad_number + 1:
            return tool_delta('read_source', json.dumps({'source_id': sid, 'start': 0, 'length': len(TEXT)}), 'corrected')
        assert number == bad_number + 2
        assert any(row.get('role') == 'tool' for row in request['messages'])
        return {'role': 'assistant', 'content': '同一任务的参数已修正并完成原文读取。'}, 'stop'

    app = QApplication.instance() or QApplication([])
    opened = []

    class CaptureUrl(QObject):
        @Slot(QUrl)
        def capture(self, url):
            opened.append(url.toLocalFile())

    receiver = CaptureUrl()
    QDesktopServices.setUrlHandler('file', receiver, 'capture')
    with stub_model(action) as (url, requests, server_errors):
        ops.save_provider_profile(HarnessSettings(provider='custom', model='local-stub', base_url=url,
                                                 timeout_seconds=20).model_dump())
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

        def wait(check):
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                app.processEvents()
                if check():
                    return
                time.sleep(.01)
            pytest.fail(f'No native diagnostics: {errors}; server={server_errors}')

        try:
            bridge.openProject(str(entry))
            wait(lambda: any(kind == 'project' for kind, _ in results))
            window.setProperty('assistantOpen', True)
            wait(lambda: not window.property('busy'))
            bridge.assistantTurn('请提取合成原文；这是离线格式失败工程验证。', '')
            wait(lambda: any(kind == 'assistant' for kind, _ in results) or any(kind == 'assistant' for kind, _ in errors))
            assert not server_errors, server_errors
            assert len(set(pids)) == 1
            revision = ops.graph_project_info()['revision']
            assert (revision > initial_revision) is partial_commit
            assert_no_staging(ops)
            store = ChatStore(ops.project_info().path)
            chat = store.active(create=False)
            saved = store.load(chat['id'])
            if recover:
                assert not errors, errors
                result = next(payload for kind, payload in results if kind == 'assistant')
                assert len(result['format_repairs']) == 1
                assert len(requests) == (4 if partial_commit else 3)
                assert not any(row.get('kind') == 'assistant_incomplete' for row in saved['messages'])
                failure = next(row for row in saved['messages'] if row.get('kind') == 'format_correction')
                assert len(action_receipts(ops)) == (1 if partial_commit else 0)
                assert sum(row['role'] == 'user' for row in saved['messages']) == 1
            else:
                error = next(text for kind, text in errors if kind == 'assistant')
                assert 'JSON 格式错误' in error and '超时' not in error
                assert len(requests) == (2 if partial_commit else 1)
                failure = next(row for row in saved['messages'] if row.get('kind') == 'assistant_incomplete')
                assert failure['starting_revision'] == initial_revision and failure['revision'] == revision
                assert ('已完成的入库变更仍保留' if partial_commit else '未产生新的入库变更') in failure['text']
            assert failure['error_code'] == 'tool_input_invalid_json'
            path = Path(failure['diagnostic_path'])
            assert path.is_file()
            metadata = json.loads(path.read_text(encoding='utf-8'))
            assert metadata['finish_reason'] == 'error' and metadata['usage']
            invalid = [tool for request in metadata['model_requests'] for tool in request.get('tool_calls', [])
                       if tool.get('arguments_json_valid') is False]
            assert any(tool['arguments'] == BAD_ARGUMENTS for tool in invalid)
            task = json.loads((Path(failure['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
            if recover:
                assert task['status'] == 'completed' and task['model_calls'] == 2
                failed_meta = json.loads((Path(failure['audit_directory']) / 'metadata-1.json').read_text(encoding='utf-8'))
                final_meta = json.loads((Path(failure['audit_directory']) / 'metadata-2.json').read_text(encoding='utf-8'))
                assert failed_meta['finish_reason'] == 'error' and final_meta['finish_reason'] == 'completed'
                assert failed_meta['usage'] and final_meta['usage']
            else:
                assert task['finish_reason'] == 'error' and task['sdk_error_diagnostics']
            panel = window.findChild(object, 'assistantPanel')
            transcript = panel.findChild(object, 'assistantTranscript')
            wait(lambda: panel.property('chatId') == chat['id'])
            transcript.positionViewAtEnd()
            transcript.forceLayout()
            rows = [item for item in transcript.property('contentItem').childItems() if item.property('modelData')]
            link = next(row.findChild(object, 'assistantDiagnosticLink') for row in rows
                        if row.findChild(object, 'assistantDiagnosticLink').property('visible'))
            assert link.property('text') == ('查看格式诊断' if recover else '查看失败诊断')
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                             link.mapToScene(QPointF(link.width() / 2, link.height() / 2)).toPoint())
            app.processEvents()
            assert opened and Path(opened[-1]) == path
            if recover:
                assert bridge._assistant_runner._harness.client._proc.pid == pids[0]
            else:
                assert bridge._assistant_runner is None
        finally:
            bridge.close()
            engine.deleteLater()
            app.processEvents()
            QDesktopServices.unsetUrlHandler('file')
