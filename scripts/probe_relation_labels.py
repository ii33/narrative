"""One real readonly conversation plus two-panel wheel isolation QA; no product deadline."""
from __future__ import annotations

import argparse
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
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout

    def save(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    original = Path('data/wheel-isolation-live-20261101T095922Z').resolve()
    ops = Operations.open(original / '双面板滚轮验收.nproj')
    saved_chat = json.loads((original / 'chat.json').read_text(encoding='utf-8'))
    summary = {'scope': 'offline saved real chat; zero new model requests', 'status': 'running'}
    app = QApplication.instance() or QApplication([])
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/NotoSansSC-VF.ttf')
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QFont(families[0]))
    app.setStyle('Fusion')
    QQuickStyle.setStyle('Fusion')
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(1392)
    assert isinstance(window.contentItem(), QQuickItem)
    panel = window.findChild(object, 'assistantPanel')

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

    try:
        bridge.openProject(str(layout(ops.project_info().path).entry))
        assert wait(lambda: any(kind == 'project' for kind, _ in results))
        window.setProperty('currentPage', 'relations')
        window.setProperty('assistantOpen', True)
        bridge.openChat(saved_chat['id'])
        assert wait(lambda: panel.property('chatId') == saved_chat['id'] and not window.property('busy'))
        settle()
        page = window.findChild(object, 'relationPage')
        canvas = window.findChild(object, 'relationCanvas')
        payload = json.loads(Path('artifacts/relation-labels-20261101/user-relation-payload.json').read_text(encoding='utf-8'))
        records = []
        for width in (1392,1442):
            for opened in (True,False):
                window.setWidth(width)
                window.setProperty('assistantOpen', opened)
                settle()
                page.apply(payload)
                assert wait(lambda: page.property('labelGeometryReady'))
                for zoom in (1.0,2.7):
                    page.resetView()
                    if zoom != 1:
                        page.zoomAt(canvas.width()/2, canvas.height()/2, zoom)
                    settle()
                    record = {'width': width, 'assistant_open': opened, 'zoom': zoom,
                              'links': plain(page.links()), 'nodes': plain(page.property('graphLayout')),
                              'labels': plain(page.property('displayLabelLayout'))}
                    filename = f'user-{width}-assistant-{opened}-zoom-{zoom}.png'
                    window.grabWindow().save(str(directory/filename))
                    record['screenshot'] = filename
                    records.append(record)
        summary.update(status='observed', scope='readonly user relation payload applied only to isolated saved-chat UI; no model/no user writes', records=records)
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
    return 0 if summary['status'] == 'observed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
