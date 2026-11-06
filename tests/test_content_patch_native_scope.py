"""The native fixed-target guard resolves generic field patches before any write."""
import http.client
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from test_chat_scroll import transcript_shell
from test_scene_table_task_scope import setup

from narrative import native_bridge
from narrative.operations import Operations


def test_native_patch_preserves_fixed_writing_target_and_raw_audit(tmp_path, monkeypatch):
    replies = []

    class Runner:
        def __init__(self, *args, **kwargs):
            self._closed = False
            self.active_session = None

        def resume(self, _session):
            return True

        def agent_turn(self, instruction, session, **kwargs):
            from narrative.conversation_runtime import get_existing_runtime

            runtime = get_existing_runtime(ops)
            address = urlsplit(runtime.host.config['url'])
            for pending in (foreign, current):
                args = {'submission_id': pending['submission_id'], 'expected_revision': pending['submission_revision'],
                        'patches': [{'op': 'set', 'path': ['changes', 0, 'changes', 'plan', 'cast_ids'], 'value': []}], 'reason': '只修人物引用'}
                connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
                try:
                    connection.request('POST', '/call', json.dumps({'session_id': session, 'nonce': runtime._binding['nonce'],
                        'name': 'patch_content_submission', 'args': args}), {'Authorization': 'Bearer '+runtime.host.token, 'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    replies.append((response.status, json.loads(response.read()), args))
                finally:
                    connection.close()
            return SimpleNamespace(raw='已处理目标字段。', metadata={'finish_reason': 'completed'})

        def close(self, **kwargs):
            self._closed = True

    monkeypatch.setattr(native_bridge, 'HarnessRunner', Runner)
    monkeypatch.setenv('NARRATIVE_PATCH_TEST_KEY', 'offline-stub')
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        ops.save_provider_profile({'provider': 'custom', 'model': 'offline-stub', 'base_url': 'http://127.0.0.1:1/v1', 'key_env': 'NARRATIVE_PATCH_TEST_KEY'})
        fixture = setup(ops)
        first = ops.read_script_segment(fixture['segment_id'])
        other = ops.read_script_segment(fixture['other_segment_id'])
        other = ops.apply_scene_table_changes(other['id'], [{'op': 'insert', 'unit': {'plan': {'summary': '另一段。'}}}], other['outline_revision'], reason='其他段fixture')

        def stage(segment):
            unit = segment['units'][0]
            plan = unit['plan'] | {'cast_ids': ['missing-person']}
            return ops.apply_scene_table_changes(segment['id'], [{'op': 'update', 'unit_id': unit['id'], 'changes': {'plan': plan}}], segment['outline_revision'], reason='暂存错误引用')

        current, foreign = stage(first), stage(other)
        assert current['status'] == foreign['status'] == 'staged'
        ui.bridge.screenplayWritingTurn('仅修目标场表。', fixture['target'])
        ui.wait(lambda: any(kind == 'assistant' for kind, _ in ui.results))
        assert [r[0] for r in replies] == [400, 200], replies
        assert ops.read_content_submission(foreign['submission_id'])['status'] == 'staged'
        assert ops.read_content_submission(current['submission_id'])['status'] == 'accepted'
        result = next(p for k, p in ui.results if k == 'assistant')
        audit = json.loads((Path(result['audit_directory'])/'task.json').read_text(encoding='utf-8'))
        steps = audit['steps'] if 'steps' in audit else audit.get('native_steps', [])
        if not steps:
            steps = json.loads((Path(result['audit_directory'])/'steps.json').read_text(encoding='utf-8'))
        assert [s['args'] for s in steps] == [r[2] for r in replies]
        assert not ui.errors
