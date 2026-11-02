"""One bounded live relation-conversation acceptance; never retries or patches outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from qa_business_actions import read_actions, staging_tables_absent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--input-spec', type=Path, required=True)
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication

    from narrative.assistant import ASSISTANT_VERSION
    from narrative.chat_store import ChatStore
    from narrative.extraction_draft import DRAFT_VERSION
    from narrative.harness import CONVERSATION_SYSTEM_PROMPT, effective_runtime_config, harness_env
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout
    from narrative.property_store import PROPERTY_CONTRACT_VERSION

    try:
        spec = json.loads(args.input_spec.read_text(encoding='utf-8'))
        origin = Operations.open_graph_project(spec['source_project'])
        original = origin.source(spec['source_id'])
        assert original['hash'] == spec['source_sha256'], 'Original source changed'
        ops = Operations.create_graph_project(directory / '关系对话验收.nproj')
        inputs = []
        for index, piece in enumerate(spec['pieces'], 1):
            text = original['text'][piece['start']:piece['end']]
            assert len(text) == piece['chars']
            assert hashlib.sha256(text.encode()).hexdigest() == piece['sha256']
            (directory / f'input-{index}.txt').write_bytes(text.encode('utf-8'))
            source_id = ops.import_source(f'原文连续片段{index}', text)
            assert ops.source(source_id)['text'] == text
            inputs.append({**piece, 'source_id': source_id})
        context_info = None
        if spec.get('context'):
            context = spec['context']
            text = original['text'][context['start']:context['end']]
            context_id = ops.import_source('完整第一章上下文（供查阅）', text)
            assert ops.source(context_id)['text'] == text
            context_info = {**context, 'source_id': context_id, 'chars': len(text),
                            'sha256': hashlib.sha256(text.encode()).hexdigest()}
            (directory / 'context.txt').write_bytes(text.encode('utf-8'))
    except Exception as exc:
        failure = {'status': 'input_error', 'model_request_count': 0, 'automatic_retry': False,
                   'error_type': type(exc).__name__, 'error': str(exc)}
        (directory / 'report.json').write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding='utf-8')
        (directory / 'report.md').write_text('输入校验失败；未发模型请求，未重试。\n', encoding='utf-8')
        print(json.dumps({'directory': str(directory), **failure}, ensure_ascii=False))
        return 1
    (directory / 'system-prompt.txt').write_bytes(CONVERSATION_SYSTEM_PROMPT.encode('utf-8'))
    summary = {
        'started': datetime.now(UTC).isoformat(), 'status': 'running',
        'assistant_version': ASSISTANT_VERSION, 'draft_version': DRAFT_VERSION,
        'property_contract_version': PROPERTY_CONTRACT_VERSION,
        'reading_position_picker_sha256': hashlib.sha256((Path(__file__).resolve().parents[1] / 'src/narrative/qml/ReadingPositionPicker.qml').read_bytes()).hexdigest(),
        'system_prompt_sha256': hashlib.sha256(CONVERSATION_SYSTEM_PROMPT.encode()).hexdigest(),
        'inputs': inputs, 'context': context_info, 'origin': spec,
        'project': str(layout(ops.project_info().path).entry),
        'task_budget': 1, 'automatic_retry': False,
    }
    (directory / 'manifest.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results, errors, progress = [], [], []
    bridge.resultReady.connect(lambda kind, payload: (results.append((kind, payload)),
                                progress.append(payload) if kind == 'conversation_progress' else None))
    bridge.errorOccurred.connect(lambda kind, message: errors.append({'kind': kind, 'message': message}))

    def wait(check, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if check():
                return True
            time.sleep(.02)
        return False

    try:
        settings, _key, _workdir = harness_env(ops)
        summary.update(settings=effective_runtime_config(settings), provider=settings.provider,
                       model=settings.model, reasoning_effort=settings.reasoning_effort,
                       timeout_seconds=settings.timeout_seconds)
        (directory / 'manifest.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        bridge.openProject(str(layout(ops.project_info().path).entry))
        if not wait(lambda: any(k == 'project' for k, _ in results) or errors, 30) or errors:
            raise RuntimeError('隔离工程未能打开')
        identifiers = '、'.join(row['source_id'] for row in inputs)
        context_note = f"需要核对前后文时可查source_id={context_info['source_id']}的完整章上下文。" if context_info else ''
        bridge.assistantTurn(f'请读取source_id={identifiers}两篇原样连续片段，按原文顺序提取人物间的关系，以及表达这些关系成立或变化所需的'
                             '人物、事件与证据，在同一任务中自审查漏及有效性后直接入库，并简要说明完成情况。'
                             + context_note, '')
        completed = wait(lambda: any(k == 'assistant' for k, _ in results) or errors,
                         settings.timeout_seconds + 30)
        if not completed:
            errors.append({'kind': 'observer_timeout', 'message': '观察期限结束；未重试'})
        chats = ChatStore(ops.project_info().path)
        chat = chats.active(create=False)
        saved = chats.load(chat['id']) if chat else None
        accepted = read_actions(ops)
        snapshot = ops.graph_snapshot()
        from narrative import tool_api
        relation_map = tool_api.call('relation_map', {}, project=layout(ops.project_info().path).entry)
        (directory / 'relation-map.json').write_text(json.dumps(relation_map, ensure_ascii=False, indent=2), encoding='utf-8')
        metadata_files = list((layout(ops.project_info().path).extraction / 'assistant/tasks').glob('*/metadata-*.json'))
        metadata = [json.loads(path.read_text(encoding='utf-8')) for path in metadata_files]
        sdk_messages = [message for row in metadata for message in row.get('model_messages') or []]
        sdk_ids = [row['message_id'] for row in sdk_messages]
        stored_outputs = [row for row in (saved or {}).get('messages', []) if row.get('kind') == 'model_output']
        stored_ids = [row['message_id'] for row in stored_outputs]
        realtime_ids = [row['message_id'] for row in progress if row.get('event') == 'model_message']
        messages_ok = bool(sdk_ids) and sdk_ids == stored_ids == realtime_ids and len(set(stored_ids)) == len(stored_ids)
        runner = bridge._assistant_runner
        process = getattr(getattr(getattr(runner, '_harness', None), 'client', None), '_proc', None)
        summary.update(actual_pid=process.pid if process else None, model_messages=len(sdk_messages),
                       messages_order_and_unique=messages_ok, realtime_model_events=len(realtime_ids),
                       metadata_files=[str(path) for path in metadata_files],
                       model_request_count=sum(len(row['model_requests']) for row in metadata if isinstance(row.get('model_requests'), list)) or None,
                       usage=[row.get('usage') for row in metadata],
                       finish_reasons=[row.get('finish_reason') for row in metadata])
        summary['relation_edges'] = len(relation_map.get('edges') or [])
        passed = completed and not errors and accepted and snapshot['nodes'] and summary['relation_edges'] and messages_ok and staging_tables_absent(ops)
        summary.update(status='passed' if passed else 'not_passed',
                       errors=errors, revision=ops.graph_project_info()['revision'],
                       applied_actions=accepted, staging_verified=staging_tables_absent(ops),
                       nodes=len(snapshot['nodes']), chat_id=chat['id'] if chat else None)
        (directory / 'chat.json').write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
        (directory / 'events.json').write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding='utf-8')
        (directory / 'snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        (directory / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as exc:
        summary.update(status='not_completed', error_type=type(exc).__name__, error=str(exc))
    finally:
        bridge.close()
        app.processEvents()
        summary['finished'] = datetime.now(UTC).isoformat()
        (directory / 'report.md').write_text(
            f"状态：{summary['status']}\n\n任务预算1，不自动重试。独立原文片段数{len(inputs)}。\n\n"
            f"模型输出数：{summary.get('model_messages', 0)}；顺序/唯一性：{summary.get('messages_order_and_unique', False)}。\n",
            encoding='utf-8')
        (directory / 'report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'directory': str(directory), 'status': summary['status']}, ensure_ascii=False))
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
