"""Offline actual-wheel probe of a saved chat; never calls a model or writes chat content."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['QT_QUICK_BACKEND'] = 'software'
    from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from narrative.chat_store import ChatStore
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    app = QApplication([])
    fid = QFontDatabase.addApplicationFont('C:/Windows/Fonts/NotoSansSC-VF.ttf')
    families = QFontDatabase.applicationFontFamilies(fid)
    if families:
        app.setFont(QFont(families[0]))
    app.setStyle('Fusion')
    QQuickStyle.setStyle('Fusion')
    bridge = NativeBridge()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    assert isinstance(window.contentItem(), QQuickItem)
    panel = window.findChild(object, 'assistantPanel')
    view = panel.findChild(object, 'assistantTranscript')
    events = []

    class WheelObserver(QObject):
        def eventFilter(self, _target, event):
            if event.type() == QEvent.Type.Wheel:
                events.append({'timestamp': event.timestamp(), 'angle_y': event.angleDelta().y(),
                               'pixel_y': event.pixelDelta().y(), 'phase': int(event.phase().value)})
            return False

    observer = WheelObserver()
    window.installEventFilter(observer)

    def settle(n=10):
        for _ in range(n):
            app.processEvents()
            time.sleep(.01)

    def state():
        y = view.property('contentY')
        rows = [row for row in view.property('contentItem').childItems()
                if row.property('modelData') and row.y() + row.height() > y and row.y() < y + view.height()]
        row = min(rows, key=lambda item: item.y()) if rows else None
        return {'y': y, 'height': view.property('contentHeight'), 'viewport': view.height(),
                'bottom': abs(view.property('originY') + view.property('contentHeight') - view.height() - y) < 5,
                'follow_tail': panel.property('followTail'), 'generation': panel.property('scrollGeneration'),
                'scroll_scheduled': panel.property('scrollScheduled'),
                'restore_after_movement': panel.property('restoreAfterMovement'),
                'key': row.property('messageKey') if row else None, 'offset': row.y() - y if row else None}

    def wheel(delta):
        point = view.mapToScene(QPointF(view.width() / 2, view.height() / 2))
        QTest.wheelEvent(window, point, QPoint(0, delta))
        settle()
        return {'point': [point.x(), point.y()], 'state': state()}

    trace = {'scope': 'offline saved chat, no model calls; final added row is QML-only and not stored'}
    try:
        bridge.openProject(str(args.project.resolve()))
        window.setProperty('assistantOpen', True)
        for _ in range(100):
            settle(2)
            if panel.property('chatId') and view.property('contentHeight') > view.height():
                break
        settle()
        trace['opened'] = state()
        trace['up'] = wheel(1200)
        trace['down'] = []
        for _ in range(30):
            trace['down'].append(wheel(-120))
            if trace['down'][-1]['state']['bottom']:
                break
        window.grabWindow().save(str(directory / 'after-wheel.png'))
        ops = Operations.open_graph_project(args.project)
        store = ChatStore(ops.project_info().path)
        saved = store.load(panel.property('chatId'))
        original = json.dumps(saved, ensure_ascii=False, sort_keys=True)
        saved['messages'].append({'role': 'assistant', 'text': '离线排版探针，不是模型输出。\n\n' * 30,
                                  'message_id': 'offline-layout-probe'})
        panel.applyChat(saved)
        settle(30)
        trace['after_qml_only_append'] = state()
        trace['stored_chat_unchanged'] = json.dumps(store.load(panel.property('chatId')), ensure_ascii=False, sort_keys=True) == original
        trace['wheel_events'] = events
        window.grabWindow().save(str(directory / 'after-qml-only-append.png'))
        (directory / 'probe.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'directory': str(directory), 'before': trace['down'][-1]['state'],
                          'after': trace['after_qml_only_append']}, ensure_ascii=False))
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    main()
