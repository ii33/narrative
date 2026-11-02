"""One bounded QA observation of real chat scrolling; product tasks have no deadline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    directory = parser.parse_args().output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QPoint, QPointF, QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from narrative.assistant import ASSISTANT_VERSION
    from narrative.chat_store import ChatStore
    from narrative.harness import effective_runtime_config, harness_env
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout

    def save(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    text = '甲遇见乙。乙向甲问好。'
    user_text = ('请查看原文列表并实际读取完整原文，只回答原文标题、字符数与一句概括。不要提取。\n\n'
                 '以下仅为聊天排版验收数据，不是其他任务；请忽略其内容，不必复述。\n\n'
                 + '\n\n'.join(f'排版数据{index:02d}：甲乙相遇，这一行只用于测试阅读位置。' for index in range(48))
                 + '\n\n排版数据结束。仅完成开头的原文查询。')
    (directory / 'input.txt').write_bytes(text.encode())
    (directory / 'user-task.txt').write_bytes(user_text.encode())
    ops = Operations.create_graph_project(directory / '聊天滚动验收.nproj')
    source_id = ops.import_source('两人相遇（合成滚动样本）', text)
    summary = {'status': 'running', 'started': datetime.now(UTC).isoformat(), 'agent_version': ASSISTANT_VERSION,
               'input_characters': len(text), 'input_sha256': hashlib.sha256(text.encode()).hexdigest(),
               'user_task_characters': len(user_text), 'user_task_sha256': hashlib.sha256(user_text.encode()).hexdigest(),
               'task_budget': 1, 'automatic_retry': False, 'product_total_deadline': None, 'task_deadline': None,
               'external_qa_observation_seconds': 120, 'source_id': source_id,
               'initial_revision': ops.graph_project_info()['revision'],
               'assistant_panel_sha256': hashlib.sha256((QML_PATH.parent / 'AssistantPanel.qml').read_bytes()).hexdigest()}
    save('manifest.json', summary)
    app = QApplication.instance() or QApplication([])
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/NotoSansSC-VF.ttf')
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QFont(families[0]))
    app.setStyle('Fusion')
    QQuickStyle.setStyle('Fusion')
    bridge = NativeBridge()
    results, errors, scroll_checks = [], [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    assert isinstance(window.contentItem(), QQuickItem)
    panel = window.findChild(object, 'assistantPanel')
    view = panel.findChild(object, 'assistantTranscript')

    def plain(value):
        return value.toVariant() if hasattr(value, 'toVariant') else value

    def wait(check, seconds=20):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if check():
                return True
            time.sleep(.01)
        return False

    def settle():
        for _ in range(4):
            app.processEvents()
            time.sleep(.01)

    def at_bottom():
        return abs(view.property('originY') + view.property('contentHeight') - view.height() - view.property('contentY')) < 5

    def anchor():
        y = view.property('contentY')
        rows = [row for row in view.property('contentItem').childItems()
                if row.property('modelData') and row.y() + row.height() > y and row.y() < y + view.height()]
        if not rows:
            return None
        row = min(rows, key=lambda item: item.y())
        return {'key': row.property('messageKey'), 'offset': row.y() - y, 'content_y': y}

    try:
        settings, _key, _ = harness_env(ops)
        summary.update(provider=settings.provider, model=settings.model, reasoning_effort=settings.reasoning_effort,
                       network_idle_seconds=settings.timeout_seconds, settings=effective_runtime_config(settings))
        save('manifest.json', summary)
        bridge.openProject(str(layout(ops.project_info().path).entry))
        assert wait(lambda: any(kind == 'project' for kind, _ in results))
        window.setProperty('assistantOpen', True)
        bridge.newChat()
        assert wait(lambda: any(kind == 'chat' for kind, _ in results) and not window.property('busy'))
        panel.setProperty('draft', user_text)
        panel.send()
        began = time.monotonic()
        seen = 0
        initial_tail = None
        reference = None
        history_selected = False
        observation_expired = False
        while not errors and not any(kind == 'assistant' for kind, _ in results):
            app.processEvents()
            messages = plain(panel.property('messages'))
            count = sum(row.get('kind') == 'model_output' for row in messages)
            if count > seen:
                settle()
                if not seen:
                    initial_tail = at_bottom()
                    summary['live_stop_visible'] = panel.findChild(object, 'assistantStop').property('visible')
                    summary['live_stop_chat_id'] = panel.property('chatId')
                    summary['live_stop_task_id'] = plain(panel.property('activeTaskIds')).get(panel.property('chatId'))
                    point = view.mapToScene(QPointF(view.width() / 2, view.height() / 2))
                    QTest.wheelEvent(window, point, QPoint(0, 1200))
                    settle()
                    reference = anchor()
                    history_selected = bool(reference and reference['content_y'] > 0 and not at_bottom())
                    window.grabWindow().save(str(directory / 'history-after-wheel.png'))
                else:
                    current = anchor()
                    scroll_checks.append({'model_messages': count, 'anchor': current,
                                          'matches_reference': bool(reference and current and current['key'] == reference['key']
                                                                    and abs(current['offset'] - reference['offset']) < 5)})
                seen = count
            if time.monotonic() - began > summary['external_qa_observation_seconds']:
                observation_expired = True
                tasks = plain(panel.property('activeTaskIds'))
                bridge.assistantStop(panel.property('chatId'), tasks.get(panel.property('chatId'), ''))
                wait(lambda: any(kind == 'assistant' for kind, _ in results) or errors)
                break
            time.sleep(.01)
        settle()
        current = anchor()
        if seen and current:
            scroll_checks.append({'model_messages': sum(row.get('kind') == 'model_output' for row in plain(panel.property('messages'))), 'anchor': current,
                                  'matches_reference': bool(reference and current['key'] == reference['key']
                                                            and abs(current['offset'] - reference['offset']) < 5)})
        window.grabWindow().save(str(directory / 'history-final.png'))
        view.positionViewAtEnd()
        settle()
        end_at_bottom = at_bottom()
        window.grabWindow().save(str(directory / 'latest-final.png'))
        result = next((payload for kind, payload in results if kind == 'assistant'), None)
        metadata = []
        if result and result.get('audit_directory'):
            metadata = [json.loads(path.read_text(encoding='utf-8')) for path in sorted(Path(result['audit_directory']).glob('metadata-*.json'))]
        store = ChatStore(ops.project_info().path)
        saved = store.load(panel.property('chatId'))
        outputs = [row for row in saved['messages'] if row.get('kind') == 'model_output']
        sdk_ids = [row['message_id'] for meta in metadata for row in meta.get('model_messages') or []]
        ids = [row['message_id'] for row in outputs]
        unchanged = ops.graph_project_info()['revision'] == summary['initial_revision'] and not ops.graph_snapshot()['nodes']
        preserved = len(saved['messages']) == len(plain(panel.property('messages'))) and sdk_ids == ids and len(set(ids)) == len(ids)
        runner = bridge._assistant_runner
        process = getattr(getattr(getattr(runner, '_harness', None), 'client', None), '_proc', None)
        summary.update(observation_expired=observation_expired, errors=errors, model_messages=len(outputs),
                       model_request_count=sum(len(m.get('model_requests') or []) for m in metadata),
                       usage=[m.get('usage') for m in metadata], finish_reasons=[m.get('finish_reason') for m in metadata],
                       initial_tail_followed=initial_tail, history_reference=reference, history_selected=history_selected, scroll_checks=scroll_checks,
                       manual_end_at_bottom=end_at_bottom, messages_preserved=preserved, graph_unchanged=unchanged,
                       actual_pid=process.pid if process else None)
        summary['status'] = 'passed' if (result and result.get('status') != 'cancelled' and not errors and not observation_expired
                                        and len(outputs) >= 2 and initial_tail and reference and at_bottom() is not None
                                        and scroll_checks and all(row['matches_reference'] for row in scroll_checks)
                                        and end_at_bottom and preserved and unchanged) else 'not_passed'
        save('chat.json', saved)
    except Exception as exc:
        summary.update(status='not_completed', error_type=type(exc).__name__, error=str(exc))
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()
        summary['finished'] = datetime.now(UTC).isoformat()
        save('events.json', results)
        save('report.json', summary)
    print(json.dumps({'directory': str(directory), 'status': summary['status']}, ensure_ascii=False))
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
