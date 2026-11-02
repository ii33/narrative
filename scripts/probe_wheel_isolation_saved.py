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
    from PySide6.QtCore import QPoint, QPointF, QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
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

    def anchor():
        y = view.property('contentY')
        rows = [row for row in view.property('contentItem').childItems()
                if row.property('modelData') and row.y() + row.height() > y and row.y() < y + view.height()]
        if not rows:
            return None
        row = min(rows, key=lambda item: item.y())
        return {'key': row.property('messageKey'), 'offset': row.y() - y, 'content_y': y}

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
        assert page and canvas and canvas.isVisible() and view.isVisible()
        def graph_state():
            return {key: page.property(key) for key in ('zoom', 'panX', 'panY')}
        def chat_state():
            return {'contentY': view.property('contentY'), 'anchor': anchor(),
                    'followTail': panel.property('followTail')}
        def wheel(item, delta, pixel=None):
            # The actual regression lived in the assistant's leftmost overlap strip.
            point = item.mapToScene(QPointF(2 if item is view else item.width() * .5, item.height() * .5))
            QTest.mouseMove(window, point.toPoint())
            QTest.wheelEvent(window, point, QPoint(0, delta), pixel or QPoint(0, 0))
            assert wait(lambda: not view.property('moving') and not view.property('flicking') and not view.property('verticalOvershoot'), 5)
            settle()
            return {'x': point.x(), 'y': point.y(), 'delta': delta}
        canvas_left = canvas.mapToScene(QPointF(0, 0)).x()
        canvas_right = canvas.mapToScene(QPointF(canvas.width(), 0)).x()
        page_left = page.mapToScene(QPointF(0, 0)).x()
        page_right = page.mapToScene(QPointF(page.width(), 0)).x()
        checks = [{'name': 'canvas_inside_page', 'canvas_bounds': [canvas_left, canvas_right],
                   'page_bounds': [page_left, page_right],
                   'passed': canvas_left >= page_left - 1 and canvas_right <= page_right + 1}]
        # Genuine wheel input chooses historical/tail positions; no programmatic scroll proxy.
        for label, delta in [('assistant_middle', 240), ('assistant_top', 120),
                             ('assistant_top_boundary', 120), ('assistant_bottom', -120),
                             ('assistant_bottom_boundary', -120)]:
            before = graph_state()
            coordinate = wheel(view, delta)
            if label in ('assistant_top', 'assistant_bottom'):
                for _ in range(80):
                    y0 = view.property('contentY')
                    top0 = view.property('originY')
                    bottom0 = max(top0, top0+view.property('contentHeight')-view.height())
                    if abs(y0-(top0 if delta > 0 else bottom0)) < 5:
                        break
                    coordinate = wheel(view, delta)
            top = view.property('originY')
            bottom = max(top, top + view.property('contentHeight') - view.height())
            y = view.property('contentY')
            position_ok = (top + 5 < y < bottom - 5) if label == 'assistant_middle' else (abs(y-top) < 5 if 'top' in label else abs(y-bottom) < 5)
            checks.append({'name': label, 'position_ok': position_ok, 'graph_before': before, 'graph_after': graph_state(),
                           'chat_after': chat_state(), 'coordinate': coordinate,
                           'passed': graph_state() == before and position_ok})
        for label, delta, pixel in [('graph_zoom', 120, None), ('graph_pan', 0, QPoint(25, 18))]:
            before = chat_state()
            graph_before = graph_state()
            coordinate = wheel(canvas, delta, pixel)
            checks.append({'name': label, 'chat_before': before, 'chat_after': chat_state(),
                           'graph_before': graph_before, 'graph_after': graph_state(),
                           'coordinate': coordinate,
                           'passed': chat_state() == before and graph_state() != graph_before})
        # Alternate targets without a settling wait between pointer moves and wheel delivery.
        for index, item in enumerate((canvas, view, canvas)):
            other_before = chat_state() if item is canvas else graph_state()
            point = item.mapToScene(QPointF(2 if item is view else item.width() * .5, item.height() * .5))
            QTest.mouseMove(window, point.toPoint())
            QTest.wheelEvent(window, point, QPoint(0, 120))
            assert wait(lambda: not view.property('moving') and not view.property('flicking') and not view.property('verticalOvershoot'), 5)
            settle()
            other_after = chat_state() if item is canvas else graph_state()
            checks.append({'name': f'rapid_target_{index}', 'other_before': other_before,
                           'other_after': other_after, 'passed': other_before == other_after})
        window.grabWindow().save(str(directory / 'two-panels-final.png'))
        save('wheel-checks.json', checks)
        summary.update(status='passed' if all(row['passed'] for row in checks) else 'not_passed', checks=checks)
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
