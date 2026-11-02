"""Exercise shared reading-position delegates in the actual two-page QML shell."""
from __future__ import annotations

import time

import pytest


def selected_path(picker):
    value = picker.property("selectedPath")
    return value.toVariant() if hasattr(value, "toVariant") else value


TREE = {
    'levels': [{'key': 'storyline', 'label': '主要故事'}, {'key': 'big_event', 'label': '大事件'}, {'key': 'scene', 'label': '场景'},
               {'key': 'event', 'label': '事件'}, {'key': 'action', 'label': '动作'}],
    'nodes': [
        {'id': 'T1', 'level': 'storyline', 'parent_id': '', 'name': '起程'},
        {'id': 'T2', 'level': 'storyline', 'parent_id': '', 'name': '渡河'},
        {'id': 'unassigned', 'level': 'storyline', 'parent_id': '', 'name': '未归组', 'synthetic': True},
        {'id': 'B1', 'level': 'big_event', 'parent_id': 'T1', 'name': '出发阶段'},
        {'id': 'B2', 'level': 'big_event', 'parent_id': 'T2', 'name': '渡河阶段'},
        {'id': 'B3', 'level': 'big_event', 'parent_id': 'unassigned', 'name': '独立阶段'},
        {'id': 'S1', 'level': 'scene', 'parent_id': 'B1', 'name': '约定'},
        {'id': 'S2', 'level': 'scene', 'parent_id': 'B2', 'name': '抵岸'},
        {'id': 'S3', 'level': 'scene', 'parent_id': 'B3', 'name': '独立场景'},
        {'id': 'E1', 'level': 'event', 'parent_id': 'S1', 'name': '出门'},
        {'id': 'E2', 'level': 'event', 'parent_id': 'S2', 'name': '上岸'},
        {'id': 'E3', 'level': 'event', 'parent_id': 'S3', 'name': '返回'},
    ],
}


@pytest.mark.parametrize('page_key,page_name,prefix', [
    ('browse', 'browsePage', 'browseTimeline'), ('relations', 'relationPage', 'relationCut'),
])
def test_two_pages_share_expand_select_and_restore_behavior(tmp_path, monkeypatch, page_key, page_name, prefix):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('QT_QUICK_BACKEND', 'software')
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    from PySide6 import QtQuick  # Register QQuickItem pointer conversion for Popup content.
    from PySide6.QtCore import QPointF, Qt, QUrl, Slot
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge

    class RecordingBridge(NativeBridge):
        def __init__(self):
            super().__init__()
            self.reads = []

        @Slot('QVariantMap')
        def loadBrowse(self, spec):
            self.reads.append(('browse', spec['cut_id']))

        @Slot()
        @Slot(str, bool)
        def loadRelations(self, cut='', related_only=False):
            self.reads.append(('relations', cut))

    assert QtQuick.QQuickItem
    app = QApplication.instance() or QApplication([])
    bridge = RecordingBridge()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]

    def wait(check):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return
            QTest.qWait(10)
        pytest.fail('QML picker condition did not become true')

    try:
        window.setProperty('mode', 'workspace')
        window.setProperty('currentPage', page_key)
        app.processEvents()
        page = window.findChild(object, page_name)
        page.setProperty('moments', TREE)
        page.setProperty('cutId', 'E2')
        picker = window.findChild(object, prefix + 'Popup')
        other_prefix = 'relationCut' if prefix == 'browseTimeline' else 'browseTimeline'
        other = window.findChild(object, other_prefix + 'Popup')
        assert picker.metaObject().className() == other.metaObject().className()
        assert 'ReadingPositionPicker' in picker.metaObject().className()
        picker.open()
        wait(lambda: picker.property('opened'))
        assert selected_path(picker) == ['T2', 'B2', 'S2', 'E2', '']

        def find_visual(item, name):
            if item.objectName() == name:
                return item
            for child in item.childItems():
                found = find_visual(child, name)
                if found is not None:
                    return found
            return None

        def click(level, index):
            view = find_visual(window.contentItem(), prefix + 'Level' + str(level))

            def delegates():
                view.forceLayout()
                return sorted([item for item in view.property('contentItem').childItems()
                               if item.property('modelData') and item.isVisible()
                               and item.width() > 0 and item.height() > 0], key=lambda item: item.y())

            # opened is a Popup lifecycle signal, not a ListView delegate-ready
            # signal. Use the existing bounded wait for the actual click target.
            wait(lambda: len(delegates()) > index)
            row = delegates()[index]
            point = row.mapToScene(QPointF(row.width() / 2, row.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
            app.processEvents()

        bridge.reads.clear()
        click(0, 0)
        assert picker.property('opened')
        assert page.property('cutId') == 'T1'
        assert selected_path(picker)[:2] == ['T1', 'B1']
        assert bridge.reads[-1] == (page_key, 'T1')
        click(1, 0)
        assert page.property('cutId') == 'B1'
        click(2, 0)
        assert picker.property('opened') and page.property('cutId') == 'S1'
        click(3, 0)
        assert picker.property('opened') and page.property('cutId') == 'E1'

        before = len(bridge.reads)
        click(0, 2)
        assert picker.property('opened') and page.property('cutId') == 'E1'
        assert selected_path(picker)[:2] == ['unassigned', 'B3']
        assert len(bridge.reads) == before
        click(1, 0)
        click(2, 0)
        click(3, 0)
        assert page.property('cutId') == 'E3'
        picker.close()
        wait(lambda: not picker.property('opened'))
        picker.open()
        wait(lambda: picker.property('opened'))
        assert selected_path(picker) == ['unassigned', 'B3', 'S3', 'E3', '']
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()
