"""One minimal real extraction task verifies persisted current item possession and scene coverage."""
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
    text = '甲捡起石头。'
    sid = ops.import_source('合成拾石原文', text)
    return {'source_id': sid, 'text': text, 'revision': ops.graph_project_info()['revision']}


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

    ops = Operations.create_graph_project(directory / '物体持有者验收.nproj')
    initial = seed(ops)
    raw_before = copy.deepcopy(ops.property_records())
    graph_before = copy.deepcopy(ops.graph_snapshot())
    save('seed.json', initial)
    save('snapshot-before.json', graph_before)
    save('records-before.json', raw_before)
    (directory / 'input.txt').write_bytes(initial['text'].encode())
    summary = {'status': 'running', 'started': datetime.now(UTC).isoformat(), 'agent_version': ASSISTANT_VERSION,
               'draft_version': DRAFT_VERSION, 'graph_contract_version': ops.project.schema()['contract_version'],
               'property_contract_version': ops.project.schema()['property_contract_version'],
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
        task = ('请读取工程中的完整原文并提取其中的人物、场景、事件、物体及原文支持的属性。'
                '每个事件须归属场景，每个物体须有原文支持的当前持有状态；通过工程工具实际入库，保留逐字证据。')
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
        active = [n for n in snapshot['nodes']]
        items = [n for n in active if n['node_type']=='item']
        events = [n for n in active if n['node_type']=='event']
        scenes = [n for n in active if n['node_type']=='scene']
        people = [n for n in active if n['node_type']=='person']
        covered = bool(events and all(any(e['id'] in scene.get('events',[]) for scene in scenes) for e in events))
        holder_states = {item['id']: ops.property_state(item['id'],'持有者') for item in items}
        named_holders = bool(items and people and all(state.get('state',{}).get('value') in {n['id'] for n in people} for state in holder_states.values()))
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
        summary.update(tool_names=[call.get('name') for call in calls], errors=errors, revision=ops.graph_project_info()['revision'],
                       imported_source_unchanged=source_unchanged, event_scene_covered=covered,
                       item_ids=[n['id'] for n in items], holder_states=holder_states,
                       current_holder_is_named_person=named_holders,
                       model_requests=sum(len(row.get('model_requests') or []) for row in metadata),
                       usage=[row.get('usage') for row in metadata], finish_reasons=[row.get('finish_reason') for row in metadata],
                       actual_pid=process.pid if process else None, elapsed_seconds=time.monotonic()-began,
                       staging_verified=staging_tables_absent(ops), task_status=task_audit.get('status'))
        summary['status'] = 'passed' if (finished and task_audit.get('status') == 'completed' and not errors
                                        and covered and named_holders and source_unchanged
                                        and not summary['pending_count']) else 'not_passed'
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
