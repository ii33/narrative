"""One real task: uploaded text plus native next-step steering during its read."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import accept_screenplay as qa
from accept_usage_statistics import seed as base_seed

from narrative import native_bridge
from narrative.chat_attachments import ChatAttachmentOperationsMixin
from narrative.project_package import layout

BASE_BRIDGE = native_bridge.NativeBridge
BASE_READ = ChatAttachmentOperationsMixin.read_chat_attachment
ENTERED, RELEASE = threading.Event(), threading.Event()
STATE = {'bridge': None, 'sent': False, 'receipts': []}
TASK = '这是附件与插话测试。读取本条上传文本附件，最后只回答附件里的确认编号。不要调用系统命令，不修改任何业务资料。'


class AcceptanceBridge(BASE_BRIDGE):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        STATE['bridge'] = self
        self.resultReady.connect(self.capture)

    def capture(self, kind, payload):
        if kind == 'assistant_steer':
            STATE['receipts'].append(dict(payload))
            if payload.get('status') in {'queued', 'delivered', 'not_delivered'}:
                RELEASE.set()

    def assistantTurn(self, text, image_path=''):
        self.assistantTurnWithAttachments(text, json.dumps([STATE['file']], ensure_ascii=False))


def seed(ops):
    fixture = base_seed(ops)
    source = layout(ops.project_info().path).entry.parent / 'selected-brief.txt'
    source.write_text('附件确认编号：UPLOAD-PROOF-1。\n这是参考资料。', encoding='utf-8')
    STATE['file'] = str(source)
    fixture['selected_file'] = str(source)
    fixture['selected_file_sha256'] = qa.digest(source.read_bytes())
    return fixture


def on_started(ops, fixture, report, directory, began):
    report['interaction_implementation_sha256'] = {
        str(path): qa.digest(path.read_bytes()) for path in
        (Path('src/narrative/chat_attachments.py'), Path('src/narrative/chat_store.py'),
         Path('src/narrative/task_tools.mjs'), Path('src/narrative/task_tool_host.py'),
         Path('src/narrative/assistant_attachment_tools.py'))}


def read_with_observation_gate(self, *args, **kwargs):
    # Test-only synchronization before an unchanged real Operations read. This
    # makes busy-message injection reproducible without altering model output.
    ENTERED.set()
    if not RELEASE.wait(15):
        raise TimeoutError('QA did not receive the queued steering acknowledgement')
    return BASE_READ(self, *args, **kwargs)


def on_progress(ops, fixture, report, directory, began, observed):
    if not ENTERED.is_set() or STATE['sent']:
        return
    bridge = STATE['bridge']
    with bridge._assistant_lock:
        job = next(iter(bridge._assistant_jobs.values()))
        chat, task = job['chat_id'], job['task_id']
    STATE['sent'] = True
    bridge.assistantSteer('补充：同一条最终回答还须附上 STEER-PROOF-2，同时保留附件编号。', '[]', chat, task)


def validate(ops, fixture, screenplay, calls, report):
    audit = json.loads((Path(report['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
    chat = ops._store.path
    from narrative.chat_store import ChatStore
    messages = ChatStore(chat).load(audit['chat_id'])['messages']
    serialized = json.dumps([message for message in messages if message.get('role') == 'assistant'], ensure_ascii=False)
    receipts = STATE['receipts']
    attachments = ops.list_chat_attachments(audit['chat_id'])['attachments']
    steps = audit['steps']
    report['interaction_evidence'] = {'receipts': receipts, 'attachments': attachments,
        'steps': steps, 'synchronization': 'QA-only read gate released on native queued receipt; actual Operations result unchanged.',
        'no_retry': True, 'no_warmup': True}
    return {'one_task_completed': audit['status'] == 'completed',
        'real_attachment_read': any(step['tool'] == 'read_chat_attachment' and step.get('ok') and 'UPLOAD-PROOF-1' in step.get('result', {}).get('content', '') for step in steps),
        'original_file_preserved': Path(fixture['selected_file']).read_text(encoding='utf-8') == '附件确认编号：UPLOAD-PROOF-1。\n这是参考资料。',
        'one_attachment_registered': len(attachments) == 1,
        'same_task_steering_queued': any(row.get('status') == 'queued' and row['task_id'] == audit['task_id'] for row in receipts),
        'same_task_steering_delivered': any(row.get('status') == 'delivered' and row['task_id'] == audit['task_id'] for row in receipts),
        'assistant_uses_both_codes': 'UPLOAD-PROOF-1' in serialized and 'STEER-PROOF-2' in serialized,
        'no_user_cancel': not any(row.get('status') == 'cancelled' for row in receipts),
        'screenplay_unchanged': screenplay == fixture['before_screenplay']}


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = 'assistant-interaction-real/1'
    qa.FIXTURE_ORIGIN = 'New isolated project; one original text upload; one native same-task steering during a test-gated attachment read.'
    native_bridge.NativeBridge = AcceptanceBridge
    ChatAttachmentOperationsMixin.read_chat_attachment = read_with_observation_gate
    try:
        return qa.main(validate=validate, required_tools=['read_chat_attachment'], use_writing_target=False,
                       prompt_factory=lambda _: TASK, on_progress=on_progress, on_started=on_started)
    finally:
        RELEASE.set()
        native_bridge.NativeBridge = BASE_BRIDGE
        ChatAttachmentOperationsMixin.read_chat_attachment = BASE_READ


if __name__ == '__main__':
    raise SystemExit(main())
