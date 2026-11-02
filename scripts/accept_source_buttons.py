"""Two real Source-button tasks in one isolated shared conversation; no retries."""
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
    from PySide6.QtCore import QPointF, Qt, QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
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

    text = '甲结识乙，二人成为朋友。'
    ops = Operations.create_graph_project(directory / '原文按钮验收.nproj')
    sid = ops.import_source('合成相识片段', text)
    part = ops.create_source_slices(sid, [{'title': '合成一片', 'start': 0, 'end': len(text)}])[0]
    assert ops.source(sid)['text'] == text
    (directory / 'input.txt').write_bytes(text.encode())
    summary = {'status': 'running', 'started': datetime.now(UTC).isoformat(),
               'agent_version': ASSISTANT_VERSION, 'draft_version': DRAFT_VERSION,
               'property_contract_version': PROPERTY_CONTRACT_VERSION,
               'source_id': sid, 'slice': part, 'input_characters': len(text),
               'input_sha256': hashlib.sha256(text.encode()).hexdigest(),
               'system_prompt_sha256': hashlib.sha256(CONVERSATION_SYSTEM_PROMPT.encode()).hexdigest(),
               'source_page_sha256': hashlib.sha256((QML_PATH.parent / 'SourcePage.qml').read_bytes()).hexdigest(),
               'task_budget': 2, 'automatic_retry': False, 'initial_revision': ops.graph_project_info()['revision']}
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
    page = window.findChild(object, 'sourcePage')
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

    def click(item):
        app.processEvents()
        QTest.qWait(30)
        assert item.property('visible') and item.property('enabled')
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint())
        app.processEvents()

    try:
        settings, _key, _ = harness_env(ops)
        summary.update(provider=settings.provider, model=settings.model, reasoning_effort=settings.reasoning_effort,
                       timeout_seconds=settings.timeout_seconds, settings=effective_runtime_config(settings))
        save('manifest.json', summary)
        bridge.openProject(str(layout(ops.project_info().path).entry))
        assert wait(lambda: any(kind == 'project' for kind, _ in results)), errors
        window.setProperty('currentPage', 'source')
        assert wait(lambda: page.property('selectedId') == sid and len(plain(page.property('slices'))) == 1)
        view = window.findChild(object, 'sourceSliceList')
        view.forceLayout()
        rows = [item for item in view.property('contentItem').childItems() if item.property('modelData')]
        click(rows[0])
        assert wait(lambda: page.property('selectedSliceId') == part['id'] and page.property('previewText') == text)
        bridge.newChat()
        assert wait(lambda: any(kind == 'chat' for kind, _ in results) and not window.property('busy'))
        chat = next(payload['chat'] for kind, payload in reversed(results) if kind == 'chat')
        for index, button_name in enumerate(('sourceSliceExtract', 'sourceSliceReview'), 1):
            assert wait(lambda: not window.property('busy'))
            start = len(results)
            error_start = len(errors)
            began = time.monotonic()
            click(window.findChild(object, button_name))
            finished = wait(lambda start=start, error_start=error_start: any(kind == 'assistant' for kind, _ in results[start:]) or len(errors) > error_start,
                            settings.timeout_seconds + 30)
            payload = next((p for kind, p in reversed(results[start:]) if kind == 'assistant'), None)
            runner = bridge._assistant_runner
            process = getattr(getattr(getattr(runner, '_harness', None), 'client', None), '_proc', None)
            snapshot = ops.graph_snapshot()
            records = ops.property_records()
            stage = {'action': 'extract' if index == 1 else 'review', 'button': button_name,
                     'completed': bool(finished and payload and len(errors) == error_start),
                     'elapsed_seconds': time.monotonic() - began, 'result': payload,
                     'source_task': [p for kind, p in results[start:] if kind == 'source_task'],
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
        metadata = [json.loads((Path(s['result']['audit_directory']) / 'metadata-1.json').read_text(encoding='utf-8'))
                    for s in stages if s.get('result')]
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
        summary['review_revision_unchanged'] = len(stages) == 2 and stages[0]['revision'] == stages[1]['revision']
        summary['status'] = 'passed' if (len(stages) == 2 and all(s['completed'] and s['panel_open'] and not s['pending_count'] for s in stages)
                                        and summary['same_chat'] and summary['same_actual_pid'] and summary['model_messages_order_match']
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
