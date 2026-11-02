"""Read-only settled UI of saved action result; no model or SDK turn."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from narrative.chat_store import ChatStore
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    app = QApplication([])
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
    families = QFontDatabase.applicationFontFamilies(fid)
    if families:
        app.setFont(QFont(families[0]))
    app.setStyle("Fusion")
    QQuickStyle.setStyle("Fusion")
    ops = Operations.open(args.project)
    revision = ops.graph_project_info()["revision"]
    bridge = NativeBridge()
    events = []
    bridge.resultReady.connect(lambda k, p: events.append((k, p)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    window.setWidth(1392)

    def plain(v):
        return v.toVariant() if hasattr(v, "toVariant") else v

    def wait(f):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            app.processEvents()
            if f():
                return
            time.sleep(0.01)
        raise AssertionError("saved UI did not settle")

    try:
        bridge.openProject(str(args.project.resolve()))
        wait(lambda: any(k == "project" for k, _ in events))
        chat = ChatStore(ops.project_info().path).active(create=False)
        bridge.openChat(chat["id"])
        window.setProperty("assistantOpen", True)
        window.focusBrowseNode("E1", {})
        page = window.findChild(object, "browsePage")
        wait(
            lambda: (
                plain(page.property("counts")).get("action") == 3 and not window.property("busy")
            )
        )
        for _ in range(10):
            app.processEvents()
            time.sleep(0.01)
        window.grabWindow().save(str(out / "saved-action-result.png"))
        result = {
            "scope": "offline saved real result, no model calls",
            "revision_before": revision,
            "revision_after": ops.graph_project_info()["revision"],
            "counts": plain(page.property("counts")),
            "node_ids": [n["id"] for n in plain(page.property("nodes"))],
            "selected_id": page.property("selectedId"),
            "shared_chat_id": chat["id"],
            "sdk_not_started": bridge._assistant_runner is None,
        }
        (out / "report.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(result)
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
