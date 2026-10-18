"""Native timeline QA on an isolated manually authored fixture; no model request."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    bridge = NativeBridge(project.parent)
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda warnings: print([w.toString() for w in warnings]))
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    assert isinstance(window, QQuickWindow)
    window.setTitle("界面验证样例 · 人工夹具，非模型输出")
    window.show()

    def wait(kind):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(20)
            if errors:
                raise RuntimeError(errors)
            if any(k == kind for k, p in results):
                return
        raise TimeoutError(kind)

    def capture(name):
        QTest.qWait(350)
        grab = window.contentItem().grabToImage()
        deadline = time.monotonic() + 10
        while grab.image().isNull() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(20)
        if not grab.saveToFile(str(args.output / name)):
            raise RuntimeError(name)
        print("saved", name)

    try:
        window.openProjectPath(str(project))
        wait("project")
        wait("canvas")
        wait("hierarchy")
        wait("timeline")
        info = window.property("projectInfo")
        info = info.toVariant() if hasattr(info, "toVariant") else info
        window.setProperty("projectInfo", info | {"title": "界面验证样例 · 人工夹具，非模型输出"})
        window.setProperty("currentPage", "graph")
        window.setProperty("graphMode", "timeline")
        window.selectNode("event")
        wait("detail")
        capture("timeline.png")
        window.setProperty("graphMode", "relations")
        window.fitGraph()
        capture("temporal-graph.png")
        window.setProperty("graphMode", "hierarchy")
        window.setProperty("displayType", "event")
        window.setProperty("displayDepth", 0)
        capture("main-events.png")
        window.setProperty("displayType", "all")
        window.setProperty("displayDepth", -1)
        window.setProperty("graphMode", "relations")
        window.expandNode("event")
        wait("expand")
        capture("expanded.png")
        bridge.collapseNode("event")
        wait("collapse")
        capture("collapsed.png")
        print("QA finished without model requests; fixture and original projects retained")
    finally:
        window.close()
        bridge.close()


if __name__ == "__main__":
    main()
