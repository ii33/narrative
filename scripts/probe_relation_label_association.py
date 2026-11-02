"""Offline actual-QML relation labels, readonly user payload plus synthetic crowding."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zoom", type=float, action="append")
    parser.add_argument("--user-only", action="store_true")
    parser.add_argument("--hover-zoom",type=float,default=1.55)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QUICK_BACKEND"] = "software"
    os.environ["NARRATIVE_PROVIDER_DB"] = str(out / "isolated-providers.sqlite3")
    from PySide6.QtCore import QPointF, QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

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
    ops = Operations.create_graph_project(out / "纯UI隔离.nproj")
    bridge = NativeBridge()
    events = []
    bridge.resultReady.connect(lambda k, p: events.append((k, p)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    window.setWidth(1442)
    assert isinstance(window.contentItem(), QQuickItem)

    def plain(v):
        return v.toVariant() if hasattr(v, "toVariant") else v

    def settle():
        for _ in range(12):
            app.processEvents()
            time.sleep(0.01)

    def wait(check):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            app.processEvents()
            if check():
                return
            time.sleep(0.01)
        raise AssertionError("UI did not settle")

    records = []
    try:
        bridge.openProject(str(out / "纯UI隔离.nproj"))
        wait(lambda: any(k == "project" for k, p in events))
        window.setProperty("currentPage", "relations")
        window.setProperty("assistantOpen", False)
        wait(lambda: any(k == "relations" for k, p in events) and not window.property("busy"))
        page = window.findChild(object, "relationPage")
        canvas = window.findChild(object, "relationCanvas")
        user = json.loads(args.payload.read_text(encoding="utf-8"))
        crowd = {"persons": [{"id": f"P{i}", "name": f"人物{i}"} for i in range(1, 7)], "edges": []}
        for i in range(1, 7):
            for j in range(i + 1, 7):
                crowd["edges"].append(
                    {"from": f"P{i}", "to": f"P{j}", "kind": f"联系{i}{j}", "state": ""}
                )
        cases = [("user-aa", user)] if args.user_only else [("user-aa", user), ("crowded", crowd)]
        for case, payload in cases:
            page.apply(payload)
            settle()
            wait(lambda: page.property("labelGeometryReady"))
            base = plain(page.property("graphLabelLayout"))
            positions = plain(page.property("graphLayout"))
            for zoom in args.zoom or (1.0, 1.55, 3.0, 0.35):
                page.resetView()
                if zoom != 1:
                    page.zoomAt(canvas.width() / 2, canvas.height() / 2, zoom)
                settle()
                actual = plain(page.property("displayLabelLayout"))
                filename = f"{case}-zoom-{zoom}.png"
                window.grabWindow().save(str(out / filename))
                record = {
                    "case": case,
                    "zoom": zoom,
                    "screen_font_size": page.property("labelScreenFontSize"),
                    "world_font_size": page.property("labelWorldFontSize"),
                    "nodes": plain(page.property("graphLayout")),
                    "base_routes": plain(page.property("graphLabelLayout")),
                    "display_labels": actual,
                    "camera": {
                        "zoom": page.property("zoom"),
                        "panX": page.property("panX"),
                        "panY": page.property("panY"),
                    },
                    "nodes_stable": positions == plain(page.property("graphLayout")),
                    "base_stable": base == plain(page.property("graphLabelLayout")),
                    "screenshot": filename,
                }
                records.append(record)
                if case == "user-aa" and zoom == args.hover_zoom:
                    scale = zoom * base["fitScale"]
                    for route in actual["edges"]:
                        box = route["label"]
                        x = canvas.width() / 2 + (box["x"] + box["w"] / 2) * scale
                        y = canvas.height() / 2 + (box["y"] + box["h"] / 2) * scale
                        if 4 < x < canvas.width() - 4 and 4 < y < canvas.height() - 4:
                            point = canvas.mapToScene(QPointF(x, y))
                            QTest.mouseMove(window, point.toPoint())
                            settle()
                            record["hovered_key"] = page.property("hoveredEdgeKey")
                            record["expected_hovered_key"] = route["edge_key"]
                            window.grabWindow().save(str(out / f"user-aa-zoom-{zoom}-hover.png"))
                            break
        summary = {
            "scope": "offline UI only, user payload read-only, no model requests",
            "provider_requests": 0,
            "records": records,
            "payload_sha256": hashlib.sha256(args.payload.read_bytes()).hexdigest(),
            "source_hashes": {
                name: hashlib.sha256((QML_PATH.parent / name).read_bytes()).hexdigest()
                for name in ["RelationPage.qml", "RelationLayout.js"]
            },
            "isolated_revision": ops.graph_project_info()["revision"],
        }
        (out / "report.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()
    print(str(out))


if __name__ == "__main__":
    main()
