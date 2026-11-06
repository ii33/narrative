"""An ordinary model completion cannot hide task-end termination of its command."""
import http.client
import json
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

from test_chat_scroll import transcript_shell


def test_native_completed_model_reports_unfinished_command_once_without_retry(tmp_path, monkeypatch):
    from narrative import native_bridge
    from narrative.conversation_runtime import get_existing_runtime
    from narrative.harness import HarnessSettings
    from narrative.operations import Operations
    calls = []
    class Runner:
        def __init__(self, *args, **kwargs):
            self._closed = False
            self.active_session = None
        def resume(self, session):
            return True
        def agent_turn(self, instruction, session, **kwargs):
            calls.append(instruction)
            runtime = get_existing_runtime(ops)
            address = urlsplit(runtime.host.config['url'])
            connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
            try:
                command = subprocess.list2cmdline([sys.executable, '-u', '-c', 'import time; print("started",flush=True); time.sleep(60)'])
                connection.request('POST', '/call', json.dumps({'session_id': session, 'nonce': runtime._binding['nonce'],
                    'name': 'run_host_command', 'args': {'command': command, 'cwd': str(tmp_path)}}),
                    {'Authorization': 'Bearer '+runtime.host.token, 'Content-Type': 'application/json'})
                response = connection.getresponse()
                result = json.loads(response.read())
                assert response.status == 200 and result['result']['status'] == 'running', result
            finally:
                connection.close()
            return SimpleNamespace(raw='模型已结束本轮。', metadata={'finish_reason': 'completed'})
        def close(self, **kwargs):
            self._closed = True
    monkeypatch.setattr(native_bridge, 'HarnessRunner', Runner)
    monkeypatch.setenv('NARRATIVE_CLEANUP_OFFLINE_KEY', 'offline-test-key')
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        ops.save_provider_profile(HarnessSettings(provider='custom', model='offline-stub', base_url='http://127.0.0.1:1/v1',
                                  key_env='NARRATIVE_CLEANUP_OFFLINE_KEY').model_dump())
        ops.set_assistant_access(ui.a['id'], 'full')
        ui.bridge.assistantTurn('运行一个前台测试命令。', '')
        ui.wait(lambda: any(kind == 'assistant' and result.get('task_id') for kind, result in ui.results))
        result = next(result for kind, result in ui.results if kind == 'assistant' and result.get('task_id'))
        assert len(calls) == 1
        assert result['status'] == 'incomplete' and result['model_finish_reason'] == 'completed'
        assert result['host_cleanup']['cleanup_receipts'][0]['status'] == 'stopped'
        assert result['commit_summary']['status'] == 'incomplete'
        assert '部分命令未完成' in result['commit_summary_text']
        messages = ui.store.load(ui.a['id'])['messages']
        summaries = [row for row in messages if row.get('task_id') == result['task_id'] and row.get('kind') == 'task_summary']
        assert len(summaries) == 1 and '部分命令未完成' in summaries[0]['text']
        assert not any(row.get('kind') == 'host_cleanup' for row in messages)
        audit = json.loads((__import__('pathlib').Path(result['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
        assert audit['status'] == 'incomplete' and audit['calls'][0]['finish_reason'] == 'completed'
        assert audit['host_cleanup'] == result['host_cleanup']
