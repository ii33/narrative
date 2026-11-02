"""Two normal event-identity tasks in one isolated shared conversation; no retries."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from qa_business_actions import read_actions, staging_tables_absent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    directory = parser.parse_args().output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from narrative.assistant import ASSISTANT_VERSION
    from narrative.chat_store import ChatStore
    from narrative.extraction_draft import DRAFT_VERSION
    from narrative.harness import CONVERSATION_SYSTEM_PROMPT, effective_runtime_config, harness_env
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout
    from narrative.property_store import PROPERTY_CONTRACT_VERSION

    def save(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    text = '甲与乙握手。两人握手致意。'
    ops = Operations.create_graph_project(directory / '事件身份验收.nproj')
    sid = ops.import_source('合成握手片段', text)
    part = ops.create_source_slices(sid, [{'title': '合成一片', 'start': 0, 'end': 6}])[0]
    assert ops.source(sid)['text'] == text
    (directory / 'input.txt').write_bytes(text.encode())
    summary = {'status': 'running', 'started': datetime.now(UTC).isoformat(),
               'agent_version': ASSISTANT_VERSION, 'draft_version': DRAFT_VERSION,
               'property_contract_version': PROPERTY_CONTRACT_VERSION,
               'source_id': sid, 'slice': part, 'input_characters': len(text),
               'input_sha256': hashlib.sha256(text.encode()).hexdigest(),
               'system_prompt_sha256': hashlib.sha256(CONVERSATION_SYSTEM_PROMPT.encode()).hexdigest(),
               'source_page_sha256': hashlib.sha256((QML_PATH.parent / 'SourcePage.qml').read_bytes()).hexdigest(),
               'task_budget': 2, 'automatic_retry': False, 'task_deadline': None, 'external_qa_stage_seconds': 180,
               'graph_contract_version': ops.project.schema()['contract_version'],
               'initial_revision': ops.graph_project_info()['revision']}
    save('manifest.json', summary)
    app = QApplication.instance() or QApplication([])
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/NotoSansSC-VF.ttf')
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QFont(families[0]))
    app.setStyle('Fusion')
    QQuickStyle.setStyle('Fusion')
    bridge = NativeBridge()
    results, errors, stages = [], [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, error: errors.append({'kind': kind, 'error': error}))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    assert isinstance(window.contentItem(), QQuickItem)
    panel = window.findChild(object, 'assistantPanel')

    def wait(check, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return True
            time.sleep(.01)
        return False

    def plain(value):
        return value.toVariant() if hasattr(value, 'toVariant') else value

    try:
        settings, _key, _ = harness_env(ops)
        summary.update(provider=settings.provider, model=settings.model, reasoning_effort=settings.reasoning_effort,
                       network_idle_seconds=settings.timeout_seconds, settings=effective_runtime_config(settings))
        save('manifest.json', summary)
        bridge.openProject(str(layout(ops.project_info().path).entry))
        assert wait(lambda: any(kind == 'project' for kind, _ in results)), errors
        window.setProperty('assistantOpen', True)
        bridge.newChat()
        assert wait(lambda: any(kind == 'chat' for kind, _ in results) and not window.property('busy'))
        chat = next(payload['chat'] for kind, payload in reversed(results) if kind == 'chat')
        tasks = [
            f'请提取source_id={sid}在范围start=0,end=6（Unicode左闭右开）出现的人物与事件节点及原文依据，并自动入库。只提交这个范围，不提取普通属性。',
            f'请重新核对source_id={sid}的完整范围start=0,end={len(text)}，更新并提交人物与事件提取结果。对已记录的同一次发生复用已有节点身份，补充完整原文依据，不制造副本。',
        ]
        for index, instruction in enumerate(tasks, 1):
            assert wait(lambda: not window.property('busy'))
            start = len(results)
            error_start = len(errors)
            began = time.monotonic()
            panel.setProperty('draft', instruction)
            panel.send()
            finished = wait(lambda start=start, error_start=error_start: any(kind == 'assistant' for kind, _ in results[start:]) or len(errors) > error_start,
                            summary['external_qa_stage_seconds'])
            observation_expired = not finished
            if observation_expired:
                identifiers = plain(panel.property('activeTaskIds'))
                bridge.assistantStop(panel.property('chatId'), identifiers.get(panel.property('chatId'), ''))
                wait(lambda start=start: any(kind == 'assistant' for kind, _ in results[start:]), 20)
            payload = next((p for kind, p in reversed(results[start:]) if kind == 'assistant'), None)
            runner = bridge._assistant_runner
            process = getattr(getattr(getattr(runner, '_harness', None), 'client', None), '_proc', None)
            snapshot = ops.graph_snapshot()
            records = ops.property_records()
            stage = {'action': 'extract_first_sentence' if index == 1 else 'review_full_source', 'instruction': instruction,
                     'external_observation_expired': observation_expired,
                     'completed': bool(finished and payload and payload.get('status') != 'cancelled' and len(errors) == error_start),
                     'elapsed_seconds': time.monotonic() - began, 'result': payload,
                     'events': [node for node in snapshot['nodes'] if node['node_type'] == 'event'],
                     'chat_id': payload.get('chat_id') if payload else None,
                     'actual_pid': process.pid if process else None,
                     'revision': ops.graph_project_info()['revision'], 'nodes': len(snapshot['nodes']),
                     'record_counts': dict(Counter(row['kind'] for row in records)),
                     'applied_actions': read_actions(ops), 'staging_verified': staging_tables_absent(ops),
                     'panel_open': window.property('assistantOpen'), 'panel_chat_id': panel.property('chatId'),
                     'errors': errors[error_start:]}
            stages.append(stage)
            save(f'stage-{index}.json', stage)
            save(f'snapshot-{index}.json', snapshot)
            save(f'property-records-{index}.json', records)
            window.grabWindow().save(str(directory / f'stage-{index}.png'))
            if not stage['completed'] or (index == 1 and not stage['applied_actions']):
                break
        summary['same_chat'] = len(stages) == 2 and stages[0]['chat_id'] == stages[1]['chat_id'] == chat['id']
        summary['same_actual_pid'] = len(stages) == 2 and bool(stages[0]['actual_pid']) and stages[0]['actual_pid'] == stages[1]['actual_pid']
        metadata = [json.loads(path.read_text(encoding='utf-8')) for s in stages if s.get('result')
                    for path in sorted(Path(s['result']['audit_directory']).glob('metadata-*.json'))]
        saved_chat = ChatStore(ops.project_info().path).load(chat['id'])
        save('chat.json', saved_chat)
        summary['stages'] = stages
        summary['model_request_count'] = sum(len(row.get('model_requests') or []) for row in metadata)
        summary['usage'] = [row.get('usage') for row in metadata]
        summary['finish_reasons'] = [row.get('finish_reason') for row in metadata]
        model_ids = [m['message_id'] for row in metadata for m in row.get('model_messages') or []]
        saved_ids = [m['message_id'] for m in saved_chat['messages'] if m.get('kind') == 'model_output']
        summary['model_messages_order_match'] = bool(model_ids) and model_ids == saved_ids and len(set(saved_ids)) == len(saved_ids)
        node_keys = Counter((n['node_type'], n['name']) for n in ops.graph_snapshot()['nodes'])
        value_keys = Counter((p['node_id'], p['property_id'], json.dumps(p['value'], sort_keys=True, ensure_ascii=False),
                              p.get('event_id'), p.get('scope'), p.get('holder_id'))
                             for p in ops.property_records() if p['kind'] == 'property_value')
        summary['duplicate_nodes'] = [list(key) for key, count in node_keys.items() if count > 1]
        summary['duplicate_values'] = [list(key) for key, count in value_keys.items() if count > 1]
        before_events = stages[0]['events'] if stages else []
        after_events = stages[-1]['events'] if stages else []
        summary['event_identity_preserved'] = (len(before_events) == 1 and len(after_events) == 1
                                                and before_events[0]['id'] == after_events[0]['id'])
        summary['primary_preserved'] = bool(before_events and after_events
                                            and before_events[0]['evidence'] == after_events[0]['evidence']
                                            and before_events[0]['disclosure'] == after_events[0]['disclosure'])
        def span_keys(event):
            return {(x['source_id'], x['start'], x['end'], x['quote']) for x in event.get('evidence_spans') or []}
        summary['evidence_enriched'] = bool(before_events and after_events
                                            and span_keys(before_events[0]) < span_keys(after_events[0]))
        submissions = []
        for stage in stages:
            if not stage.get('result'):
                continue
            for path in sorted(Path(stage['result']['audit_directory']).glob('metadata-*.json')):
                meta = json.loads(path.read_text(encoding='utf-8'))
                for request in meta.get('model_requests') or []:
                    for call in request.get('tool_calls') or []:
                        if call.get('name') != 'submit_extraction':
                            continue
                        try:
                            arguments = json.loads(call['arguments'])
                        except (ValueError, KeyError):
                            continue
                        submissions.append({'stage': stage['action'], 'arguments': arguments,
                                            'existing_ids': [{'name': n.get('name'), 'existing_id': n.get('existing_id')}
                                                             for n in arguments.get('result', {}).get('nodes', []) if n.get('existing_id')]})
        save('submissions.json', submissions)
        summary['submit_calls'] = len(submissions)
        summary['review_submit_calls'] = sum(row['stage'] == 'review_full_source' for row in submissions)
        summary['review_existing_ids'] = [item for row in submissions if row['stage'] == 'review_full_source' for item in row['existing_ids']]
        summary['status'] = 'passed' if (len(stages) == 2 and all(s['completed'] and s['panel_open'] and not s['pending_count'] for s in stages)
                                        and summary['same_chat'] and summary['same_actual_pid'] and summary['model_messages_order_match']
                                        and summary['event_identity_preserved'] and summary['primary_preserved']
                                        and not summary['duplicate_nodes'] and not summary['duplicate_values']) else 'not_passed'
    except Exception as exc:
        summary.update(status='not_completed', error_type=type(exc).__name__, error=str(exc))
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()
        summary['errors'] = errors
        summary['finished'] = datetime.now(UTC).isoformat()
        save('events.json', results)
        save('report.json', summary)
    print(json.dumps({'directory': str(directory), 'status': summary['status']}, ensure_ascii=False))
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
