"""One live explicit event-merge task over an Operations-created isolated legacy fixture."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from qa_business_actions import staging_tables_absent


def seed(ops):
    text = '甲与乙握手。两人握手致意。'
    sid = ops.import_source('合成同一次握手原文', text)
    q1, q2 = '甲与乙握手', '两人握手致意'
    def span(start, quote):
        return {'source_id': sid, 'start': start, 'end': start + len(quote), 'quote': quote}
    def person(identifier, name, start):
        return {'kind': 'node', 'id': identifier, 'node_type': 'person', 'name': name,
            'evidence': {'source_id': sid, 'quote': name}, 'disclosure': {'source_id': sid, 'start': start},
            'evidence_spans': [span(start, name)]}
    def event(identifier, quote, start):
        return {'kind': 'node', 'id': identifier, 'node_type': 'event', 'name': '甲与乙握手',
            'participants': ['P1', 'P2'], 'evidence': {'source_id': sid, 'quote': quote},
            'disclosure': {'source_id': sid, 'start': start}, 'evidence_spans': [span(start, quote)]}
    target, source = event('E1', q1, 0), event('E2', q2, 6)
    target['evidence_spans'].append(span(6, q2))
    objects = [person('P1', '甲', 0), person('P2', '乙', 2), target, source,
               {'kind': 'node', 'id': 'S1', 'node_type': 'scene', 'name': '门前会面', 'events': ['E1', 'E2'],
                'evidence': {'source_id': sid, 'quote': text}, 'disclosure': {'source_id': sid, 'start': 0},
                'evidence_spans': [span(0, text)]}]
    definitions = [{'kind': 'property_definition', 'id': name, 'value_type': typ, 'cardinality': 'one'}
                   for name, typ in [('动作标注', 'string'), ('相关事件', 'node'), ('编号文本', 'string'), ('看法', 'string')]]
    common = {'kind': 'property_value', 'event_id': 'E2', 'disclosure': {'source_id': sid, 'start': 6},
              'evidence': {'source_id': sid, 'quote': text}}
    records = [common | {'id': 'seed_subject', 'node_id': 'E2', 'property_id': '动作标注', 'value': '握手致意'},
               common | {'id': 'seed_target', 'node_id': 'P1', 'property_id': '相关事件', 'value': 'E2', 'applies_to': 'E2'},
               common | {'id': 'seed_literal', 'node_id': 'P1', 'property_id': '编号文本', 'value': 'E2'},
               common | {'id': 'seed_holder', 'node_id': 'P1', 'property_id': '看法', 'value': '合成编号E2仅为文字',
                         'scope': 'cognition', 'holder_id': 'P2'}]
    committed = ops.apply_changes(objects, definitions + records, ops.graph_project_info()['revision'],
                                  reason='隔离验收人工种子：模拟重复，不是模型输出')
    assert committed['status'] == 'accepted'
    return {'source_id': sid, 'text': text, 'revision': committed['revision'],
            'objects': objects, 'property_records': definitions + records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    directory = parser.parse_args().output.resolve()
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

    def save(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    ops = Operations.create_graph_project(directory / '事件合并验收.nproj')
    initial = seed(ops)
    raw_before = copy.deepcopy(ops.property_records())
    graph_before = copy.deepcopy(ops.graph_snapshot())
    save('seed.json', initial)
    save('snapshot-before.json', graph_before)
    save('records-before.json', raw_before)
    (directory / 'input.txt').write_bytes(initial['text'].encode())
    summary = {'status': 'running', 'started': datetime.now(UTC).isoformat(), 'agent_version': ASSISTANT_VERSION,
               'draft_version': DRAFT_VERSION, 'graph_contract_version': ops.project.schema()['contract_version'],
               'input_sha256': hashlib.sha256(initial['text'].encode()).hexdigest(), 'input_characters': len(initial['text']),
               'system_prompt_sha256': hashlib.sha256(CONVERSATION_SYSTEM_PROMPT.encode()).hexdigest(),
               'task_budget': 1, 'automatic_retry': False, 'task_deadline': None, 'external_qa_seconds': 180,
               'initial_revision': initial['revision'], 'seed_is_model_output': False}
    save('manifest.json', summary)
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    def wait(check, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if check():
                return True
            time.sleep(.01)
        return False

    try:
        settings, _key, _workdir = harness_env(ops)
        summary.update(provider=settings.provider, model=settings.model, reasoning_effort=settings.reasoning_effort,
                       network_idle_seconds=settings.timeout_seconds, settings=effective_runtime_config(settings))
        save('manifest.json', summary)
        bridge.openProject(str(layout(ops.project_info().path).entry))
        assert wait(lambda: any(kind == 'project' for kind, _ in results) or errors, 20) and not errors
        bridge.assistantTurn('请复核当前工程已有的事件与原文证据，检查是否有同一次发生被重复记录。'
                             '如确认重复，请通过显式合并工具修复引用并保留历史；只处理确认的重复，不补提其他事实或改写属性值。', '')
        finished = wait(lambda: any(kind == 'assistant' for kind, _ in results) or errors, summary['external_qa_seconds'])
        summary['observation_expired'] = not finished
        if not finished:
            jobs = list(bridge._assistant_jobs.values())
            for job in jobs:
                if job.get('task_id'):
                    bridge.assistantStop(job['chat_id'], job['task_id'])
            wait(lambda: any(kind == 'assistant' for kind, _ in results) or errors, 20)
        result = next((payload for kind, payload in results if kind == 'assistant'), None)
        snapshot = ops.graph_snapshot()
        raw_after = ops.property_records()
        current = ops.property_records()
        source_unchanged = ops.source(initial['source_id'])['text'] == initial['text']
        remaining_ids = {node['id'] for node in snapshot['nodes']}
        target = 'E1' if 'E1' in remaining_ids and 'E2' not in remaining_ids else None
        canonical = next((node for node in snapshot['nodes'] if node['id'] == target), None)
        active_events = [node for node in snapshot['nodes'] if node['node_type'] == 'event']
        rows = {row['id']: row for row in current}
        migrated = bool(target and rows['seed_subject']['node_id'] == target and rows['seed_target']['event_id'] == target
                        and rows['seed_target']['applies_to'] == target and rows['seed_target']['value'] == target)
        literals = rows['seed_literal']['value'] == 'E2' and rows['seed_holder']['holder_id'] == 'P2' and rows['seed_holder']['value'] == '合成编号E2仅为文字'
        metadata = []
        task_audit = {}
        if result and result.get('audit_directory'):
            metadata = [json.loads(path.read_text(encoding='utf-8')) for path in sorted(Path(result['audit_directory']).glob('metadata-*.json'))]
        if result and result.get('audit_directory'):
            task_audit = json.loads((Path(result['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
        chat_store = ChatStore(ops.project_info().path)
        chat = chat_store.active(create=False)
        saved = chat_store.load(chat['id']) if chat else {}
        save('chat.json', saved)
        runner = bridge._assistant_runner
        process = getattr(getattr(getattr(runner, '_harness', None), 'client', None), '_proc', None)
        summary.update(errors=errors, physically_removed_source='E2' not in remaining_ids, active_event_ids=[node['id'] for node in active_events],
                       revision=ops.graph_project_info()['revision'], property_references_rewritten=raw_after != raw_before,
                       imported_source_unchanged=source_unchanged, current_references_migrated=migrated,
                       literals_and_holder_preserved=literals, target_primary_preserved=bool(canonical and canonical['evidence'] == next(n['evidence'] for n in graph_before['nodes'] if n['id'] == target)),
                       model_requests=sum(len(row.get('model_requests') or []) for row in metadata), usage=[row.get('usage') for row in metadata],
                       finish_reasons=[row.get('finish_reason') for row in metadata], actual_pid=process.pid if process else None,
                       staging_verified=staging_tables_absent(ops))
        calls = [call for row in metadata for request in row.get('model_requests') or [] for call in request.get('tool_calls') or []]
        summary['merge_tool_calls'] = [call for call in calls if call.get('name') in {'preview_event_merge', 'merge_events'}]
        actual_merge = any(step.get('tool') == 'merge_events' and step.get('ok') is True for step in task_audit.get('steps', []))
        summary.update(task_status=task_audit.get('status'), successful_merge_tool_step=actual_merge)
        summary['status'] = 'passed' if (finished and task_audit.get('status') == 'completed' and actual_merge and result and result.get('status') != 'cancelled' and not errors
                                        and len(active_events) == 1 and target == 'E1' and migrated and literals and source_unchanged
                                        and raw_after != raw_before and summary['target_primary_preserved'] and not summary['pending_count']) else 'not_passed'
        save('snapshot-after.json', snapshot)
        save('records-after-raw.json', raw_after)
        save('records-after-current.json', current)
    except Exception as exc:
        summary.update(status='not_completed', error_type=type(exc).__name__, error=str(exc))
    finally:
        bridge.close()
        app.processEvents()
        summary['finished'] = datetime.now(UTC).isoformat()
        save('events.json', results)
        save('report.json', summary)
    print(json.dumps({'directory': str(directory), 'status': summary['status']}, ensure_ascii=False))
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
