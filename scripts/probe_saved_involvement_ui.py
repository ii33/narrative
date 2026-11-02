"""Read-only actual Main.qml aggregate and location-link clicks over saved live output."""

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
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF, Qt, QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    def save(name, value):
        (args.output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def plain(value):
        return value.toVariant() if hasattr(value, "toVariant") else value

    def find(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = find(child, name)
            if found:
                return found
        return None

    def wait(check, seconds=10):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if check():
                return True
            time.sleep(0.01)
        return False

    def settle():
        for _ in range(12):
            app.processEvents()
            time.sleep(0.01)

    def texts(item):
        result = [str(item.property("text"))] if item.property("text") is not None else []
        for child in item.childItems():
            result.extend(texts(child))
        return result

    ops = Operations.open_graph_project(args.project)
    before = ops.property_records()
    revision = ops.graph_project_info()["revision"]
    graph = ops.graph_snapshot()
    app = QApplication.instance() or QApplication([])
    fontid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
    fonts = QFontDatabase.applicationFontFamilies(fontid)
    if fonts:
        app.setFont(QFont(fonts[0]))
    app.setStyle("Fusion")
    QQuickStyle.setStyle("Fusion")
    bridge = NativeBridge()
    results = []
    errors = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    assert isinstance(window.contentItem(), QQuickItem)
    window.setWidth(1392)
    window.setHeight(1000)
    report = {
        "status": "running",
        "provider_requests": 0,
        "read_only_UI_probe": True,
        "project": str(args.project.resolve()),
        "revision_before": revision,
        "views": {},
    }
    try:
        bridge.openProject(str(args.project.resolve()))
        assert wait(lambda: any(k == "project" for k, _ in results)), errors
        window.setProperty("assistantOpen", False)
        window.setProperty("currentPage", "browse")
        page = window.findChild(object, "browsePage")
        page.load()
        assert wait(lambda: not window.property("busy"))
        settle()

        def choose(identifier):
            page.setProperty("relatedOnly", False)
            page.setProperty("selectedType", "")
            page.setProperty("query", "")
            page.load()
            assert wait(lambda: not window.property("busy"))
            view = find(window.contentItem(), "browseNodeList")
            index = next(
                i for i, n in enumerate(plain(page.property("nodes"))) if n["id"] == identifier
            )
            view.positionViewAtIndex(index, 0)
            view.forceLayout()
            settle()
            row = next(
                item
                for item in view.property("contentItem").childItems()
                if item.property("id") == identifier
            )
            point = row.mapToScene(QPointF(row.width() / 2, row.height() / 2))
            QTest.mouseClick(
                window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint()
            )
            assert wait(
                lambda: plain(page.property("detail")).get("node", {}).get("id") == identifier
            )
            assert wait(lambda: not window.property("busy"))
            settle()

        uppers = []
        for kind in ["big_event", "scene"]:
            found = next((n for n in graph["nodes"] if n["node_type"] == kind), None)
            if found:
                uppers.append(found)
        assert uppers, "No model upper nodes available for live-data UI check"
        conditions = {}
        jumps = []
        for upper in uppers:
            choose(upper["id"])
            expected = ops.property_node_summary(upper["id"])["involvement"]
            actual = plain(page.property("involvement"))
            report["views"][upper["id"]] = {
                "node": upper,
                "involvement": actual,
                "expected": expected,
                "title": window.findChild(object, "browseDetailTitle").property("text"),
            }
            conditions[upper["id"] + "_same_as_Ops"] = actual == expected
            conditions[upper["id"] + "_title"] = (
                report["views"][upper["id"]]["title"] == upper["name"]
            )
            window.grabWindow().save(str(args.output / f"upper-{upper['id']}.png"))
            if upper["node_type"] != "big_event":
                continue
            for target in expected["locations"]:
                choose(upper["id"])
                group = find(window.contentItem(), "browseInvolvementlocations")
                assert group and group.isVisible()
                links = [i for i in group.childItems() if i.objectName() == "browseInvolvementLink"]
                link = next(
                    i for i in links if str(i.property("text")).rsplit(" · ", 1)[-1] == target["id"]
                )
                scroll = window.findChild(object, "browseDetailScroll")
                content = scroll.property("contentItem")
                content.setProperty("contentY", max(0, group.y() - 40))
                settle()
                point = link.mapToScene(QPointF(link.width() / 2, link.height() / 2))
                QTest.mouseClick(
                    window,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                    point.toPoint(),
                )
                ok = wait(
                    lambda target=target: (
                        plain(page.property("detail")).get("node", {}).get("id") == target["id"]
                    )
                )
                assert wait(lambda: not window.property("busy"))
                settle()
                jumps.append(
                    {
                        "from": upper["id"],
                        "target": target,
                        "passed": ok,
                        "actual": plain(page.property("detail")).get("node", {}).get("id"),
                    }
                )
                window.grabWindow().save(str(args.output / f"location-{target['id']}.png"))
        conditions.update(
            all_location_links_work=bool(jumps) and all(j["passed"] for j in jumps),
            raw_records_unchanged=ops.property_records() == before,
            revision_unchanged=ops.graph_project_info()["revision"] == revision,
        )
        report.update(
            status="passed" if all(conditions.values()) else "not_passed",
            conditions=conditions,
            location_jumps=jumps,
        )

    except Exception as exc:
        report.update(status="not_passed", error_type=type(exc).__name__, error=str(exc))
    finally:
        report["revision_after"] = ops.graph_project_info()["revision"]
        report["errors"] = errors
        report["model_runtime_started"] = bridge._assistant_runner is not None
        save("report.json", report)
        bridge.close()
        window.close()
        engine.deleteLater()
        app.processEvents()
    print(json.dumps({"output": str(args.output), "status": report["status"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
