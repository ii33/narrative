"""One live incremental event task referencing an existing scene, without relisting scene nodes."""
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
    text = '甲与乙握手。甲向乙点头。'
    sid = ops.import_source('合成会面原文', text)
    payload = {'nodes': [
        {'type': 'person', 'name': '甲', 'quotes': ['甲与乙握手。']},
        {'type': 'person', 'name': '乙', 'quotes': ['甲与乙握手。']},
        {'type': 'scene', 'name': '会面', 'quotes': ['甲与乙握手。']},
        {'type': 'event', 'name': '握手', 'scene': '会面', 'participants': ['甲', '乙'], 'quotes': ['甲与乙握手。']}], 'properties': []}
    result = ops.submit_extraction(sid, payload, end=6)
    assert result['status'] == 'accepted'
    # Authorized isolated historical fixture only: reproduce the legacy writer's missing edge.
    # Not a product API and never applied to a user project.
    scene = next(n for n in ops.graph_snapshot()['nodes'] if n['node_type'] == 'scene')
    scene['events'] = []
    with ops.project.store.connect(write=True) as db:
        db.execute('UPDATE objects SET data=? WHERE id=?', (json.dumps(scene, ensure_ascii=False), scene['id']))
    return {'source_id': sid, 'text': text, 'revision': ops.graph_project_info()['revision'],
            'seed_result': result, 'legacy_fixture_sql': 'isolated scene.events emptied to simulate old writer orphan'}


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

    ops = Operations.create_graph_project(directory / '已有场景增量事件验收.nproj')
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
        task = ('请读取工程原文及当前完整图，核对已有事件是否正确归属场景；若有历史结构遗漏，'
                '请用工程写入工具按原文修复，并补提后半句尚未记录的事件。所有事件都应归入适当场景；'
                '复用已有身份，保留原证据、已有属性和历史，不重建重复人物或事件。')
        (directory / 'user-task.txt').write_bytes(task.encode())
        began = time.monotonic()
        bridge.assistantTurn(task, '')
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
        old_scene = next(n for n in graph_before['nodes'] if n['node_type'] == 'scene')
        scene = next(n for n in snapshot['nodes'] if n['id'] == old_scene['id'])
        new_events = [n for n in snapshot['nodes'] if n['node_type'] == 'event' and n['id'] not in {n['id'] for n in graph_before['nodes']}]
        all_events = {n['id'] for n in snapshot['nodes'] if n['node_type'] == 'event'}
        linked = bool(new_events and set(scene['events']) == all_events)
        scene_evidence = all(scene.get(k) == old_scene.get(k) for k in ('evidence', 'evidence_spans', 'disclosure'))
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
        calls = [call for row in metadata for request in row.get('model_requests') or [] for call in request.get('tool_calls') or []]
        generic_writes = [call for call in calls if call.get('name') == 'apply_changes']
        summary.update(errors=errors, revision=ops.graph_project_info()['revision'],
                       raw_records_unchanged=raw_after == raw_before, imported_source_unchanged=source_unchanged,
                       scene_children_unioned=linked, scene_evidence_preserved=scene_evidence,
                       new_event_ids=[n['id'] for n in new_events], generic_write_count=len(generic_writes),
                       model_requests=sum(len(row.get('model_requests') or []) for row in metadata),
                       usage=[row.get('usage') for row in metadata], finish_reasons=[row.get('finish_reason') for row in metadata],
                       actual_pid=process.pid if process else None, elapsed_seconds=time.monotonic()-began,
                       staging_verified=staging_tables_absent(ops), task_status=task_audit.get('status'))
        summary['status'] = 'passed' if (finished and task_audit.get('status') == 'completed' and not errors
                                        and linked and scene_evidence and source_unchanged and generic_writes
                                        and raw_after == raw_before and not summary['pending_count']) else 'not_passed'
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
