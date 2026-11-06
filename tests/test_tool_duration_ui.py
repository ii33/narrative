"""Actual host execution timing is distinct from model/network waiting."""
from __future__ import annotations

import http.client
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click, plain


def test_tool_call_measures_success_and_failure_and_public_keeps_timing(monkeypatch):
    from narrative import assistant, tool_api

    clock = iter([10.0, 10.012, 20.0, 20.003])
    monkeypatch.setattr(assistant.time, 'perf_counter', lambda: next(clock))
    monkeypatch.setattr(tool_api, 'call', lambda *a, **k: {'sources': []})
    success = assistant._call('unused', 'list_sources', {})
    assert success['duration_ms'] == 12.0

    def fail(*args, **kwargs):
        raise tool_api.ToolError('isolated failure')

    monkeypatch.setattr(tool_api, 'call', fail)
    failure = assistant._call('unused', 'list_sources', {})
    assert not failure['ok'] and failure['duration_ms'] == 3.0
    assert assistant._public([success, failure])[1]['duration_ms'] == 3.0
    assert 'duration_ms' not in assistant._public([{'tool': 'list_sources', 'ok': True}])[0]


def test_actual_qml_tool_cards_match_live_timings_once_and_reload_saved_history(tmp_path, monkeypatch):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        chat = ui.store.create('单工具实际耗时')
        args = {'source_id': 'S1', 'quotes': ['引文']}
        blocks = [{'type': 'tool-call', 'id': f'call-{i}', 'name': 'locate_source', 'arguments': json.dumps(args)} for i in range(2)]
        blocks += [{'type': 'tool-call', 'id': 'unknown-time', 'name': 'read_source', 'arguments': '{}'}]
        ui.store.append(chat['id'], 'assistant', '', kind='model_output', message_id='timed:tools', task_id='timed-task', content=blocks, full_output=json.dumps(blocks))
        ui.bridge.openChat(chat['id'])
        ui.wait(lambda: ui.panel.property('chatId') == chat['id'])
        steps = []
        for index, duration in enumerate([3.0, 7.0]):
            # Reordered argument keys still match the same actual invocation.
            step = {'tool': 'locate_source', 'args': {'quotes': ['引文'], 'source_id': 'S1'}, 'ok': True, 'duration_ms': duration}
            steps.append(step)
            payload = {'event': 'tool_complete', 'chat_id': chat['id'], 'current': chat['id'], 'task_id': 'timed-task',
                'project_path': str(ui.bridge._project_path), 'busy': True, 'step_index': index, 'step': step}
            ui.bridge.resultReady.emit('conversation_progress', payload)
            ui.bridge.resultReady.emit('conversation_progress', payload)  # re-render never duplicates a card
        ui.settle()
        tools = ui.messages()[0]['toolBatches'][0]['tools']
        assert len(tools) == 3, ui.messages()
        assert [tool.get('duration_ms') for tool in tools] == [3.0, 7.0, None]
        assert '等待模型继续' in ui.panel.property('progressText')
        assert ui.panel.property('sessions')  # lightweight timing events retain the chat list
        ui.store.annotate_message(chat['id'], 'timed:tools', steps=steps)
        ui.bridge.resultReady.emit('conversation_progress', {'event': 'task_complete', 'chat_id': chat['id'], 'current': chat['id'],
            'task_id': 'timed-task', 'project_path': str(ui.bridge._project_path), 'chat': ui.store.load(chat['id']), 'items': ui.store.list(), 'busy': False})
        ui.settle()
        assert plain(ui.panel.property('taskToolSteps')) == {}
        render_ready(ui)
        row = ui.rows()[0]
        click(ui, row.findChild(object, 'assistantProcessToggle'))
        ui.wait(lambda: any(x.objectName() == 'assistantToolActivity' for x in visuals(row)))
        activity = next(x for x in visuals(row) if x.objectName() == 'assistantToolActivity')
        assert not any(x.objectName() == 'assistantToolTiming' for x in visuals(activity))
        click(ui, activity.findChild(object, 'assistantToolToggle'))
        details = activity.findChild(object, 'assistantToolDetails').property('text')
        assert '程序用时' not in details and 'duration_ms' not in details and '不含模型等待' not in details
        ui.bridge.openChat(ui.b['id'])
        ui.wait(lambda: ui.panel.property('chatId') == ui.b['id'])
        assert all('duration_ms' not in tool for message in ui.messages() for batch in message['toolBatches'] for tool in batch['tools'])
        ui.bridge.openChat(chat['id'])
        ui.wait(lambda: ui.panel.property('chatId') == chat['id'])
        assert [tool.get('duration_ms') for tool in ui.messages()[0]['toolBatches'][0]['tools']] == [3.0, 7.0, None]
        assert not ui.errors


def test_native_host_publishes_program_duration_and_persists_it(tmp_path, monkeypatch):
    from narrative import native_bridge
    from narrative.conversation_runtime import get_existing_runtime
    from narrative.harness import HarnessSettings
    from narrative.operations import Operations

    class Runner:
        def __init__(self, *args, **kwargs):
            self._closed = False
            self.active_session = None

        def resume(self, session):
            return True

        def agent_turn(self, instruction, session, **kwargs):
            args = {'source_id': source_id, 'quotes': ['甲走过桥。']}
            content = [{'type': 'tool-call', 'id': 'native-call-1', 'name': 'locate_source', 'arguments': json.dumps(args)}]
            kwargs['on_message']({'message_id': 'native:tool', 'text': '', 'content': content, 'full_output': json.dumps(content), 'seq': 0})
            runtime = get_existing_runtime(ops)
            address = urlsplit(runtime.host.config['url'])
            connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
            try:
                connection.request('POST', '/call', json.dumps({'session_id': session, 'nonce': runtime._binding['nonce'],
                    'name': 'locate_source', 'args': args}), {'Authorization': 'Bearer ' + runtime.host.token, 'Content-Type': 'application/json'})
                response = connection.getresponse()
                assert response.status == 200, response.read()
                response.read()
            finally:
                connection.close()
            kwargs['on_message']({'message_id': 'native:answer', 'text': '定位完成。', 'content': [{'type': 'text', 'text': '定位完成。'}], 'seq': 1})
            self.active_session = None
            return SimpleNamespace(raw='定位完成。', metadata={'finish_reason': 'completed'})

        def close(self, **kwargs):
            self._closed = True

    monkeypatch.setattr(native_bridge, 'HarnessRunner', Runner)
    monkeypatch.setenv('NARRATIVE_TOOL_TIMING_TEST_KEY', 'offline-stub')
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        source_id = ops.import_source('隔离计时原文', '甲走过桥。')
        ops.save_provider_profile(HarnessSettings(provider='custom', model='offline-stub', base_url='http://127.0.0.1:1/v1', key_env='NARRATIVE_TOOL_TIMING_TEST_KEY').model_dump())
        ui.bridge.assistantTurn('定位这一句。', '')
        ui.wait(lambda: any(kind == 'assistant' for kind, _ in ui.results))
        event = next(payload for kind, payload in ui.results if kind == 'conversation_progress' and payload.get('event') == 'tool_complete')
        assert event['step_index'] == 0 and event['step']['duration_ms'] >= 0
        chat = ui.store.load(ui.a['id'])
        saved = next(row for row in chat['messages'] if row.get('message_id') == 'native:answer')
        assert saved['steps'][0]['duration_ms'] == event['step']['duration_ms']
        assert not ui.errors
