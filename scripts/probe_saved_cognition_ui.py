"""Read-only actual Main.qml clicks over saved real cognition output; no model calls."""

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
    people = {n["name"]: n["id"] for n in graph["nodes"] if n["node_type"] == "person"}
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
        for name in ["甲", "乙", "丙"]:
            identifier = people[name]
            view = find(window.contentItem(), "browseNodeList")
            view.forceLayout()
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
                lambda identifier=identifier: (
                    plain(page.property("detail")).get("node", {}).get("id") == identifier
                )
            )
            assert wait(lambda: not window.property("busy"))
            settle()
            captured = {
                key: plain(page.property(key))
                for key in ["detail", "claimRows", "heardRows", "propertyRows", "categoryGroups"]
            }
            captured["title"] = window.findChild(object, "browseDetailTitle").property("text")
            captured["identity"] = window.findChild(object, "browseDetailIdentity").property("text")
            captured["rendered_text"] = "\n".join(texts(page))
            assert identifier in captured["identity"] and captured["title"] == name
            window.grabWindow().save(str(args.output / f"person-{name}.png"))
            report["views"][name] = captured
        rows = {r["id"]: r for r in before if r.get("kind") == "property_value"}
        cog = {
            r["id"]
            for r in rows.values()
            if r.get("node_id") == people["乙"]
            and r.get("holder_id") == people["甲"]
            and r.get("scope") == "cognition"
        }
        claim = {
            r["id"]
            for r in rows.values()
            if r.get("node_id") == people["乙"]
            and r.get("holder_id") == people["丙"]
            and r.get("scope") == "claim"
        }
        holder_cog = {r["record_id"] for r in report["views"]["甲"]["claimRows"]}
        holder_claim = {r["record_id"] for r in report["views"]["丙"]["claimRows"]}
        subject_heard = {r["record_id"] for r in report["views"]["乙"]["heardRows"]}
        subject_props = report["views"]["乙"]["propertyRows"]
        ids_in_props = {
            identifier for row in subject_props for identifier in row.get("recordIds", [])
        }
        world_doctor = any("医生" in str(row.get("valueText", "")) for row in subject_props)
        wrong_world = any(
            str(row.get("valueText", "")) in {"教师", "律师"} for row in subject_props
        )
        conditions = {
            "raw_expected_subject_holder_layers_present": bool(cog) and bool(claim),
            "甲_holder_sees_cognition": bool(cog) and cog <= holder_cog,
            "丙_speaker_sees_claim": bool(claim) and claim <= holder_claim,
            "乙_subject_sees_both_heard": bool(cog)
            and bool(claim)
            and cog | claim <= subject_heard,
            "subjective_values_not_in_world_property_rows": not bool((cog | claim) & ids_in_props)
            and not wrong_world,
            "world_doctor_visible": world_doctor,
            "raw_records_unchanged": ops.property_records() == before,
            "revision_unchanged": ops.graph_project_info()["revision"] == revision,
        }
        report.update(
            status="passed" if all(conditions.values()) else "not_passed", conditions=conditions
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
