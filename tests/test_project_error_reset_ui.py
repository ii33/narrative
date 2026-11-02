"""A successful project creation clears a prior launcher error in real QML."""
from __future__ import annotations

import sqlite3
import time


def test_create_current_project_clears_prior_incompatible_open_error(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("QT_QUICK_BACKEND", "software")
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtWidgets import QApplication

    from narrative.graph_store import GRAPH_SCHEMA_VERSION
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout

    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    assert isinstance(window.contentItem(), QQuickItem)
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    def wait(check):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return
            time.sleep(.01)
        raise AssertionError(f"Creation did not settle: {errors}")

    try:
        old_error = "旧工程格式不兼容，当前程序只支持当前版本新工程"
        bridge.errorOccurred.emit("project", old_error)
        app.processEvents()
        assert window.property("mode") == "launcher"
        assert window.property("errorText") == old_error

        bridge.createProjectAt(QUrl.fromLocalFile(str(tmp_path)).toString(), "111")
        wait(lambda: window.property("mode") == "workspace")
        assert any(kind == "project" for kind, _ in results)
        assert window.property("errorText") == ""
        assert window.property("notice").startswith("已打开 ")
        entry = tmp_path / "111.nproj"
        assert Operations.open(entry).graph_project_info()["schema_version"] == GRAPH_SCHEMA_VERSION
        with sqlite3.connect(layout(entry).db) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == GRAPH_SCHEMA_VERSION
        assert errors == [("project", old_error)]
    finally:
        bridge.close()
        window.close()
        engine.deleteLater()
        app.processEvents()
