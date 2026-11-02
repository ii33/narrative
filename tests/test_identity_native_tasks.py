"""Two native SDK tasks: partial accepted output survives failure and is reused later."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from current_format_assertions import assert_no_staging
from test_extraction_identity import FIRST_DAY_END, Q1, Q2, TEXT, draft, events, node
from test_native_model_diagnostics import BAD_ARGUMENTS, require_runtime, stub_model, tool_delta


@pytest.mark.parametrize('identity_mode', ['shared_evidence', 'explicit_id'])
def test_later_native_task_updates_partially_committed_event_without_creating_another(tmp_path, monkeypatch, identity_mode):
    require_runtime()
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    monkeypatch.setenv('ARK_API_KEY', 'offline-placeholder')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'offline-placeholder')
    from PySide6.QtWidgets import QApplication

    from narrative.chat_store import ChatStore
    from narrative.harness import HarnessSettings
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    entry = tmp_path / 'partial-identity.nproj'
    ops = Operations.create_graph_project(entry)
    source_id = ops.import_source('跨任务合成身份原文', TEXT)

    def action(number, request):
        if number == 1:
            return tool_delta('submit_extraction', json.dumps({'source_id': source_id, 'result': draft(), 'end': FIRST_DAY_END}, ensure_ascii=False))
        if number == 2:
            # Deliberately not complete tool_calls: fatal diagnostics, not the
            # format-recovery path. The already accepted batch must survive.
            delta, _finish = tool_delta('read_source', BAD_ARGUMENTS, 'fatal-format')
            return delta, 'stop'
        history = json.dumps(request['messages'], ensure_ascii=False)
        assert 'PRIVATE_PARTIALTASK_CONTEXT_MARKER' in history
        assert 'PRIVATE_REVISION_TASK_MARKER' in history
        if number == 3:
            if identity_mode == 'shared_evidence':
                result = draft([Q2, '乙的手'])
            else:
                result = draft(['乙的手'], existing_id=node(ops, '甲乙握手')['id'])
            return tool_delta('submit_extraction', json.dumps({'source_id': source_id, 'result': result, 'end': FIRST_DAY_END}, ensure_ascii=False), 'update-event')
        assert number == 4
        assert 'accepted' in history or 'unchanged' in history
        return {'role': 'assistant', 'content': '已完善同一事件的依据，没有创建另一次发生。'}, 'stop'

    app = QApplication.instance() or QApplication([])
    with stub_model(action) as (url, requests, server_errors):
        ops.save_provider_profile(HarnessSettings(provider='custom', model='local-stub', base_url=url,
                                                 timeout_seconds=20).model_dump())
        bridge = NativeBridge()
        results, errors = [], []
        bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
        bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

        def wait(check):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                app.processEvents()
                if check():
                    return
                time.sleep(.01)
            pytest.fail(f'Native identity task incomplete: {errors}; stub={server_errors}')

        try:
            bridge.openProject(str(entry))
            wait(lambda: any(kind == 'project' for kind, _ in results))
            bridge.assistantTurn('PRIVATE_PARTIALTASK_CONTEXT_MARKER。提取合成原文。', '')
            wait(lambda: any(kind == 'assistant' for kind, _ in errors))
            assert len(requests) == 2 and not server_errors
            original = node(ops, '甲乙握手')
            event_id = original['id']
            assert len(events(ops)) == 1 and {s['quote'] for s in original['evidence_spans']} == {Q1, Q2}
            assert bridge._assistant_runner is None
            store = ChatStore(ops.project_info().path)
            chat = store.active(create=False)
            first_chat = store.load(chat['id'])
            failed = next(row for row in first_chat['messages'] if row.get('kind') == 'assistant_incomplete')
            first_audit = json.loads((Path(failed['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
            assert first_audit['status'] == 'incomplete'
            errors.clear()
            results.clear()
            bridge.assistantTurn('PRIVATE_REVISION_TASK_MARKER。核对当前片段并完善已入库的事件证据。', '')
            wait(lambda: any(kind == 'assistant' for kind, _ in results) or errors)
            assert not errors and not server_errors
            result = next(payload for kind, payload in results if kind == 'assistant')
            assert result['chat_id'] == chat['id']
            second_audit = json.loads((Path(result['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
            assert second_audit['status'] == 'completed'
            assert first_audit['session_id'] == second_audit['session_id'] == chat['session']
            assert len(requests) == 4 and len(events(ops)) == 1
            updated = node(ops, '甲乙握手')
            assert updated['id'] == event_id and updated['evidence'] == original['evidence']
            assert updated['disclosure'] == original['disclosure']
            assert {span['quote'] for span in updated['evidence_spans']} == {Q1, Q2, '乙的手'}
            assert node(ops, '会面')['events'] == [event_id]
            assert_no_staging(ops)
        finally:
            bridge.close()
            app.processEvents()
