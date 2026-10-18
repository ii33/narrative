"""Qt Quick settings integration; run separately from QCoreApplication tests."""

import os
import time
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge
from narrative.operations import Operations


def test_native_context_settings_round_trip_and_provider_switch(tmp_path: Path):
    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
    app = cast(QApplication, QApplication.instance() or QApplication([]))
    path = tmp_path / "native-context.sqlite3"
    ops = Operations.create_graph_project(path)
    bridge = NativeBridge(tmp_path)
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window: Any = engine.rootObjects()[0]
    assert isinstance(window, QQuickWindow)

    def until(condition):
        deadline = time.monotonic() + 8
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(20)
        assert condition(), errors

    def field(name):
        item = window.findChild(QQuickItem, f"service{name}Field")
        assert item is not None
        return item

    def edit(name, value):
        item = field(name)
        item.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        for character in str(value):
            QTest.keyClick(window, Qt.Key(ord(character)))
        assert item.property("text") == str(value)

    def load_provider(provider):
        before = len(results)
        window.loadProviderProfile(provider)
        until(lambda: any(kind == "provider" for kind, _ in results[before:]))

    try:
        window.openProjectPath(str(path))
        until(lambda: field("ContextWindow").property("text") == "1000000")
        assert field("OutputReserve").property("text") == "131072"
        assert field("CompactionReserve").property("text") == "65536"
        assert field("CompactionMaxTokens").property("text") == "32768"
        assert field("OutputLimit").property("text") == "131072"
        window.setProperty("currentPage", "agent")
        window.setProperty("showServiceSettings", True)
        app.processEvents()
        for name, value in (
            ("ContextWindow", 900_000), ("OutputReserve", 0),
            ("CompactionReserve", 50_000), ("CompactionMaxTokens", 90_000),
            ("OutputLimit", 200_000),
        ):
            edit(name, value)
        label = window.findChild(QQuickItem, "serviceHistoryBudgetLabel")
        assert label is not None and "650000" in label.property("text")
        until(lambda: not window.property("busy"))
        before = len(results)
        scroll = window.findChild(QQuickItem, "serviceSettingsScroll")
        assert scroll is not None
        content = scroll.property("contentItem")
        content.setProperty("contentY", content.property("contentHeight") - content.height())
        app.processEvents()
        QTest.qWait(100)
        save = window.findChild(QQuickItem, "saveServiceSettingsButton")
        assert save is not None
        point = save.mapToScene(QPointF(save.width() / 2, save.height() / 2))
        top = scroll.mapToScene(QPointF(0, 0))
        assert top.y() <= point.y() <= top.y() + scroll.height(), (
            point, top, scroll.height(), content.property("contentY"), content.property("contentHeight"),
        )
        assert save.isEnabled()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(round(point.x()), round(point.y())))
        until(lambda: any(kind == "provider" for kind, _ in results[before:]))
        saved = ops.harness_settings("ark")
        assert saved["context_window"] == 900_000
        assert saved["output_reserve"] == 0
        assert saved["compaction_max_tokens"] == 90_000
        assert saved["max_tokens"] == 200_000
        edit("ContextWindow", 200_000)
        window.saveCurrentProfile()
        assert "有效输出预留" in window.property("profileNotice")
        assert ops.harness_settings("ark")["context_window"] == 900_000
        edit("ContextWindow", "1.5")
        window.saveCurrentProfile()
        assert "整数" in window.property("profileNotice")
        load_provider("qwen")
        assert field("ContextWindow").property("text") == "1000000"
        assert field("OutputReserve").property("text") == "131072"
        assert ops.harness_settings()["provider"] == "ark"
        load_provider("ark")
        assert field("ContextWindow").property("text") == "900000"
        assert field("OutputReserve").property("text") == "0"
        assert field("CompactionMaxTokens").property("text") == "90000"
        assert not errors
        window.setHeight(720)
        app.processEvents()
        start = window.findChild(QQuickItem, "startAnalysisButton")
        assert start is not None
        panel = start.parentItem().parentItem()
        button_bottom = start.mapToScene(QPointF(0, start.height())).y()
        panel_inner_bottom = panel.mapToScene(QPointF(0, panel.height() - 14)).y()
        assert button_bottom <= panel_inner_bottom
        window.setHeight(940)
        app.processEvents()
        QTest.qWait(100)
        window.grabWindow().save(str(tmp_path / "context-settings.png"))
    finally:
        window.close()
        bridge.close()
        engine.deleteLater()
        app.processEvents()
