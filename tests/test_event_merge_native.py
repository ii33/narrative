"""Native model tools execute explicit event merge through the public bridge."""
from __future__ import annotations

import json
import time

from current_format_assertions import assert_no_staging
from event_merge_fixtures import seed_event_duplicates
from test_native_model_diagnostics import require_runtime, stub_model, tool_delta


def test_native_tools_preview_merge_and_physically_rewrite_current_references(tmp_path, monkeypatch):
    require_runtime()
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    monkeypatch.setenv('ARK_API_KEY', 'offline-placeholder')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'offline-placeholder')
    from PySide6.QtWidgets import QApplication

    from narrative.harness import HarnessSettings
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    entry = tmp_path / 'merge-native.nproj'
    ops = Operations.create_graph_project(entry)
    seed = seed_event_duplicates(ops)
    arguments = {'source_event_id': seed['source'], 'target_event_id': seed['target'],
                 'expected_revision': seed['revision'], 'evidence': seed['proof'],
                 'reason': '合成原文同一次握手的两个历史节点'}

    def action(number, request):
        names = {tool['function']['name'] for tool in request['tools']}
        assert {'preview_event_merge', 'merge_events', 'get_node'} <= names
        if number == 1:
            return tool_delta('preview_event_merge', json.dumps(arguments, ensure_ascii=False))
        tool_messages = [row for row in request['messages'] if row.get('role') == 'tool']
        assert tool_messages
        if number == 2:
            assert 'true' in json.dumps(tool_messages[-1]).lower()
            return tool_delta('merge_events', json.dumps(arguments, ensure_ascii=False), 'merge')
        if number == 3:
            assert 'accepted' in json.dumps(tool_messages[-1])
            return tool_delta('get_node', json.dumps({'node_id': seed['target']}), 'old-node')
        assert number == 4
        assert seed['target'] in json.dumps(tool_messages[-1])
        return {'role': 'assistant', 'content': '已合并同一事件并更新当前引用。'}, 'stop'

    app = QApplication.instance() or QApplication([])
    with stub_model(action) as (url, requests, server_errors):
        ops.save_provider_profile(HarnessSettings(provider='custom', model='local-stub', base_url=url, timeout_seconds=20).model_dump())
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
            raise AssertionError(f'Merge SDK task did not finish: {errors}; stub={server_errors}')

        try:
            bridge.openProject(str(entry))
            wait(lambda: any(kind == 'project' for kind, _ in results))
            bridge.assistantTurn('请核对并合并合成工程中确认重复的同一事件。', '')
            wait(lambda: any(kind == 'assistant' for kind, _ in results) or errors)
            assert not errors and not server_errors
            assert len(requests) == 4
            result = next(payload for kind, payload in results if kind == 'assistant')
            assert result['graph_reset_required'] and result['revision'] == seed['revision'] + 1
            assert seed['source'] not in {row['id'] for row in ops.graph_snapshot()['nodes']}
            projected = {row['id']: row for row in ops.property_records()}
            assert projected['v_anchor']['event_id'] == seed['target']
            assert projected['v_subject']['node_id'] == seed['target']
            assert projected['v_target']['value'] == seed['target']
            assert projected['v_literal']['value'] == seed['source']
            assert_no_staging(ops)
        finally:
            bridge.close()
            app.processEvents()
