"""Native attachment/steering admission uses isolated Ops and explicit runtime stubs."""
import json
import threading

from PIL import Image
from test_chat_scroll import transcript_shell

from narrative.operations import Operations


def seed_job(ui, runtime):
    chat, task = ui.a['id'], 'native-steer-test'
    job = {'chat_id': chat, 'task_id': task, 'cancel': threading.Event(), 'runtime': runtime,
           'binding_nonce': 'nonce-test', 'access_mode': 'standard'}
    ui.bridge._assistant_jobs[9000] = job
    return chat, task, job


def test_native_multiple_attachments_queued_ack_cannot_downgrade_actual_delivery(tmp_path, monkeypatch):
    captured = {}
    class Runtime:
        def steer(self, chat_id, task_id, nonce, content_blocks, *, message_id=None, on_status=None):
            captured.update(chat=chat_id, task=task_id, nonce=nonce, blocks=content_blocks)
            on_status({'message_id': message_id, 'status': 'delivered'})
            return {'message_id': message_id, 'status': 'queued'}
    with transcript_shell(tmp_path, monkeypatch) as ui:
        chat, task, _ = seed_job(ui, Runtime())
        image = tmp_path / 'reference.png'
        Image.new('RGB', (10, 10)).save(image)
        text = tmp_path / 'note.txt'
        text.write_text('用户补充资料', encoding='utf-8')
        ui.bridge.assistantSteer('调整当前方向', json.dumps([str(image), text.as_uri()]), chat, task)
        ui.wait(lambda: any(kind == 'assistant_steer' and result.get('status') == 'queued' for kind, result in ui.results))
        rows = [row for row in ui.store.load(chat)['messages'] if row.get('kind') == 'user_steering']
        assert len(rows) == 1 and rows[0]['steering_status'] == 'delivered'
        assert len(rows[0]['attachments']) == 2 and rows[0]['task_id'] == task
        assert rows[0]['message_id'].startswith('steer-20')
        assert [block['type'] for block in captured['blocks']] == ['text', 'image']
        assert captured['task'] == task and captured['nonce'] == 'nonce-test'
        assert 'read_chat_attachment' in captured['blocks'][0]['text']
        assert any(result.get('event') == 'steering_status' and result.get('busy') is True for kind, result in ui.results if kind == 'conversation_progress')


def test_import_then_ended_task_keeps_files_and_not_delivered_message(tmp_path, monkeypatch):
    class Runtime:
        def steer(self, *args, **kwargs):
            raise ValueError('原任务已结束，不另开任务')
    with transcript_shell(tmp_path, monkeypatch) as ui:
        chat, task, _ = seed_job(ui, Runtime())
        source = tmp_path / 'reference.txt'
        source.write_text('仍保留已上传资料', encoding='utf-8')
        ui.bridge.assistantSteer('补充资料', json.dumps([str(source)]), chat, task)
        ui.wait(lambda: any(kind == 'assistant_steer' and result.get('status') == 'not_delivered' for kind, result in ui.results))
        row = next(row for row in ui.store.load(chat)['messages'] if row.get('kind') == 'user_steering')
        assert row['steering_status'] == 'not_delivered' and len(row['attachments']) == 1
        ops = Operations.open(ui.bridge._project_path)
        assert ops.read_chat_attachment(chat, row['attachments'][0]['id'])['content'] == '仍保留已上传资料'


def test_stop_missing_old_task_returns_authority_without_touching_new_task(tmp_path, monkeypatch):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        chat, task, job = seed_job(ui, object())
        ui.bridge.assistantStop(chat, 'old-task')
        payload = next(result for kind, result in ui.results if kind == 'assistant_stop' and result['task_id'] == 'old-task')
        assert payload['status'] == 'not_running' and payload['active_tasks'][chat] == task
        assert not job['cancel'].is_set()
        ui.bridge._assistant_jobs.clear()
        ui.bridge.assistantStop(chat, task)
        assert any(result.get('event') == 'task_complete' and result.get('busy') is False
                   for kind, result in ui.results if kind == 'conversation_progress')

def test_normal_multi_attachment_turn_persists_user_refs_and_task_audit(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from narrative import native_bridge
    from narrative.harness import HarnessSettings
    captured = []
    class Runner:
        def __init__(self, *args, **kwargs):
            self._closed = False
            self.active_session = None
        def resume(self, session):
            return True
        def agent_turn(self, instruction, session, **kwargs):
            captured.append(instruction)
            return SimpleNamespace(raw='附件已收到。', metadata={'finish_reason': 'completed'})
        def close(self, **kwargs):
            self._closed = True
    monkeypatch.setattr(native_bridge, 'HarnessRunner', Runner)
    monkeypatch.setenv('NARRATIVE_ATTACHMENT_OFFLINE_KEY', 'offline-test-key')
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        ops.save_provider_profile(HarnessSettings(provider='custom', model='offline-stub', base_url='http://127.0.0.1:1/v1',
            key_env='NARRATIVE_ATTACHMENT_OFFLINE_KEY').model_dump())
        image = tmp_path / 'image.png'
        Image.new('RGB', (10, 10)).save(image)
        text = tmp_path / 'text.txt'
        text.write_text('待查资料', encoding='utf-8')
        ui.bridge.assistantTurnWithAttachments('接收本条附件', json.dumps([str(image), str(text)]))
        ui.wait(lambda: any(kind == 'assistant' and result.get('task_id') for kind, result in ui.results))
        result = next(result for kind, result in ui.results if kind == 'assistant' and result.get('task_id'))
        row = next(row for row in ui.store.load(ui.a['id'])['messages'] if row.get('text') == '接收本条附件')
        assert len(row['attachments']) == 2 and row['task_id'] == result['task_id']
        assert isinstance(captured[0], list)
        assert len([block for block in captured[0] if block['type'] in ('image', 'image_url')]) == 1
        assert row['attachments'][1]['id'] in captured[0][0]['text']
        audit = json.loads((__import__('pathlib').Path(result['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
        assert [ref['id'] for ref in audit['attachments']] == [ref['id'] for ref in row['attachments']]
        assert audit['status'] == 'completed'
