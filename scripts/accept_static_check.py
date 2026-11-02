"""One normal minimal live task, then manual static-check UI over healthy and explicit nonmodel legacy states."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from qa_business_actions import staging_tables_absent


def seed(ops):
    text = "😀甲捡起石头。"
    sid = ops.import_source("合成拾石原文", text)
    return {"source_id": sid, "text": text, "revision": ops.graph_project_info()["revision"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    directory = parser.parse_args().output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF, Qt, QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from narrative.assistant import ASSISTANT_VERSION
    from narrative.chat_store import ChatStore
    from narrative.extraction_draft import DRAFT_VERSION
    from narrative.harness import CONVERSATION_SYSTEM_PROMPT, effective_runtime_config, harness_env
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout

    def save(name, value):
        (directory / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    ops = Operations.create_graph_project(directory / "静态检查页验收.nproj")
    initial = seed(ops)
    raw_before = copy.deepcopy(ops.property_records())
    graph_before = copy.deepcopy(ops.graph_snapshot())
    save("seed.json", initial)
    save("snapshot-before.json", graph_before)
    save("records-before.json", raw_before)
    (directory / "input.txt").write_bytes(initial["text"].encode())
    summary = {
        "status": "running",
        "started": datetime.now(UTC).isoformat(),
        "agent_version": ASSISTANT_VERSION,
        "draft_version": DRAFT_VERSION,
        "graph_contract_version": ops.project.schema()["contract_version"],
        "property_contract_version": ops.project.schema()["property_contract_version"],
        "input_sha256": hashlib.sha256(initial["text"].encode()).hexdigest(),
        "input_characters": len(initial["text"]),
        "system_prompt_sha256": hashlib.sha256(CONVERSATION_SYSTEM_PROMPT.encode()).hexdigest(),
        "task_budget": 1,
        "automatic_retry": False,
        "task_deadline": None,
        "external_qa_seconds": 180,
        "initial_revision": initial["revision"],
        "seed_is_model_output": False,
    }
    summary["source_hashes"] = {str(path.name): hashlib.sha256(path.read_bytes()).hexdigest() for path in
                                [QML_PATH, QML_PATH.parent/"StaticCheckPage.qml", QML_PATH.parent/"BrowsePage.qml", QML_PATH.parent/"SourcePage.qml",
                                 QML_PATH.parent.parent/"project_check.py", QML_PATH.parent.parent/"native_bridge.py"]}
    save("manifest.json", summary)
    app = QApplication.instance() or QApplication([])
    font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QFont(families[0]))
    app.setStyle("Fusion")
    QQuickStyle.setStyle("Fusion")
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    assert isinstance(window.contentItem(), QQuickItem)
    window.setWidth(1392)

    def plain(value):
        return value.toVariant() if hasattr(value, "toVariant") else value

    def settle():
        for _ in range(8):
            app.processEvents()
            time.sleep(0.01)

    def click(item):
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(
            window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint()
        )
        settle()

    def find_visual(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            result = find_visual(child, name)
            if result:
                return result
        return None

    def wait(check, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if check():
                return True
            time.sleep(0.01)
        return False

    try:
        settings, _key, _workdir = harness_env(ops)
        summary.update(
            provider=settings.provider,
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
            network_idle_seconds=settings.timeout_seconds,
            settings=effective_runtime_config(settings),
        )
        save("manifest.json", summary)
        bridge.openProject(str(layout(ops.project_info().path).entry))
        assert (
            wait(lambda: any(kind == "project" for kind, _ in results) or errors, 20) and not errors
        )
        task = (
            "请读取工程中的完整原文并提取其中的人物、场景、事件、物体及原文支持的属性。"
            "每个事件须归属场景，每个物体须有原文支持的当前持有状态；通过工程工具实际入库，保留逐字证据。"
        )
        (directory / "user-task.txt").write_bytes(task.encode())
        began = time.monotonic()
        bridge.assistantTurn(task, "")
        finished = wait(
            lambda: any(kind == "assistant" for kind, _ in results) or errors,
            summary["external_qa_seconds"],
        )
        summary["observation_expired"] = not finished
        if not finished:
            jobs = list(bridge._assistant_jobs.values())
            for job in jobs:
                if job.get("task_id"):
                    bridge.assistantStop(job["chat_id"], job["task_id"])
            wait(lambda: any(kind == "assistant" for kind, _ in results) or errors, 20)
        result = next((payload for kind, payload in results if kind == "assistant"), None)
        snapshot = ops.graph_snapshot()
        raw_after = ops.property_records()
        current = ops.property_records()
        source_unchanged = ops.source(initial["source_id"])["text"] == initial["text"]
        active = [n for n in snapshot["nodes"]]
        items = [n for n in active if n["node_type"] == "item"]
        events = [n for n in active if n["node_type"] == "event"]
        scenes = [n for n in active if n["node_type"] == "scene"]
        people = [n for n in active if n["node_type"] == "person"]
        covered = bool(
            events
            and all(any(e["id"] in scene.get("events", []) for scene in scenes) for e in events)
        )
        holder_states = {item["id"]: ops.property_state(item["id"], "持有者") for item in items}
        named_holders = bool(
            items
            and people
            and all(
                state.get("state", {}).get("value") in {n["id"] for n in people}
                for state in holder_states.values()
            )
        )
        metadata = []
        task_audit = {}
        if result and result.get("audit_directory"):
            metadata = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(Path(result["audit_directory"]).glob("metadata-*.json"))
            ]
        if result and result.get("audit_directory"):
            task_audit = json.loads(
                (Path(result["audit_directory"]) / "task.json").read_text(encoding="utf-8")
            )
        chat_store = ChatStore(ops.project_info().path)
        chat = chat_store.active(create=False)
        saved = chat_store.load(chat["id"]) if chat else {}
        save("chat.json", saved)
        runner = bridge._assistant_runner
        process = getattr(getattr(getattr(runner, "_harness", None), "client", None), "_proc", None)
        calls = [
            call
            for row in metadata
            for request in row.get("model_requests") or []
            for call in request.get("tool_calls") or []
        ]
        summary.update(
            tool_names=[call.get("name") for call in calls],
            errors=errors,
            revision=ops.graph_project_info()["revision"],
            imported_source_unchanged=source_unchanged,
            event_scene_covered=covered,
            item_ids=[n["id"] for n in items],
            holder_states=holder_states,
            current_holder_is_named_person=named_holders,
            model_requests=sum(len(row.get("model_requests") or []) for row in metadata),
            usage=[row.get("usage") for row in metadata],
            finish_reasons=[row.get("finish_reason") for row in metadata],
            actual_pid=process.pid if process else None,
            elapsed_seconds=time.monotonic() - began,
            staging_verified=staging_tables_absent(ops),
            task_status=task_audit.get("status"),
        )
        precondition = bool(
            finished
            and task_audit.get("status") == "completed"
            and not errors
            and covered
            and named_holders
            and source_unchanged
            and not summary["pending_count"]
        )
        assert precondition, (
            "normal live extraction was not completed; no fixture corruption performed"
        )
        window.setProperty("assistantOpen", False)
        window.setProperty("currentPage", "static_check")
        settle()
        page = window.findChild(object, "staticCheckPage")

        def scan():
            click(window.findChild(object, "staticCheckRun"))
            assert wait(lambda: page.property("hasReport") and not page.property("running"), 20)
            return plain(page.property("report"))

        before_check = ops.check_project()
        ui_before = scan()
        assert ui_before["issues"] == before_check["issues"]
        save("check-before-fixture.json", before_check)
        save("ui-check-before-fixture.json", ui_before)
        window.grabWindow().save(str(directory / "healthy-check.png"))
        save("model-snapshot-before-fixture.json", snapshot)
        save("model-records-before-fixture.json", raw_after)
        revision = ops.graph_project_info()["revision"]
        holder_id = next(
            r["id"]
            for r in raw_after
            if r.get("node_id") == items[0]["id"] and r.get("property_id") == "持有者"
        )
        new_rows = [
            {
                "kind": "property_value",
                "id": "qa_fixture_missing_definition",
                "node_id": items[0]["id"],
                "property_id": "验收未定义属性",
                "value": "隔离fixture文字",
                "event_id": events[0]["id"],
                "evidence": {"source_id": initial["source_id"], "quote": initial["text"]},
                "disclosure": {"source_id": initial["source_id"], "start": 0},
            },
        ]
        with ops.project.store.connect(write=True) as db:
            for row in new_rows:
                db.execute(
                    "INSERT INTO property_records(id,kind,data,accepted_revision) VALUES (?,?,?,?)",
                    (row["id"], row["kind"], json.dumps(row, ensure_ascii=False), revision),
                )
            db.execute("DELETE FROM property_records WHERE id=?", (holder_id,))
        save(
            "nonmodel-legacy-fixture.json",
            {
                "scope": "explicit isolated legacy corruption AFTER successful live result, never model output",
                "rows": new_rows, "physically_deleted_holder_record": holder_id,
            },
        )
        expected = ops.check_project()
        ui_after = scan()
        assert ui_after["issues"] == expected["issues"]
        assert any(r["code"] == "ITEM_HOLDER_REQUIRED" for r in ui_after["issues"])
        save("check-after-fixture.json", expected)
        save("ui-check-after-fixture.json", ui_after)
        window.grabWindow().save(str(directory / "fixture-errors-check.png"))
        view = window.findChild(object, "staticCheckIssues")
        issues = plain(page.property("visibleIssues"))
        index = next(
            i for i, r in enumerate(issues) if r.get("record_id") == "qa_fixture_missing_definition"
        )
        navigation = []
        for action in ("Node", "Record", "Source"):
            window.setProperty("currentPage", "static_check")
            settle()
            view.positionViewAtIndex(index, 0)
            settle()
            row = next(
                r
                for r in view.property("contentItem").childItems()
                if r.objectName() == "staticCheckIssue"
                and plain(r.property("issue")).get("record_id") == "qa_fixture_missing_definition"
            )
            click(find_visual(row, "staticCheck" + action))
            assert wait(
                lambda action=action: (
                    window.property("currentPage") == ("source" if action == "Source" else "browse")
                ),
                20,
            )
            if action == "Record":
                focus = window.findChild(object, "browseFocusedRecord")
                assert wait(
                    lambda focus=focus: focus.property("recordId") == "qa_fixture_missing_definition", 20
                )
            if action == "Source":
                preview = window.findChild(object, "sourcePreview")
                assert wait(lambda preview=preview: preview.property("selectedText") == initial["text"], 20)
            navigation.append(
                {"action": action, "page": window.property("currentPage"), "passed": True}
            )
            window.grabWindow().save(str(directory / f"fixture-jump-{action}.png"))
        summary.update(
            manual_scan_matches_operations=True,
            healthy_scan_no_errors=not any(
                r["severity"] == "error" for r in before_check["issues"]
            ),
            nonmodel_fixture_visible=True,
            navigation=navigation,
            check_revision_unchanged=ops.graph_project_info()["revision"] == revision,
            static_page_sha256=hashlib.sha256(
                (QML_PATH.parent / "StaticCheckPage.qml").read_bytes()
            ).hexdigest(),
        )
        summary["status"] = (
            "passed"
            if summary["healthy_scan_no_errors"] and summary["check_revision_unchanged"]
            else "not_passed"
        )
        save("snapshot-after.json", snapshot)
        save("records-after-raw.json", raw_after)
        save("records-after-current.json", current)
    except Exception as exc:
        summary.update(status="not_completed", error_type=type(exc).__name__, error=str(exc))
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()
        summary["finished"] = datetime.now(UTC).isoformat()
        save("events.json", results)
        save("report.json", summary)
    print(
        json.dumps({"directory": str(directory), "status": summary["status"]}, ensure_ascii=False)
    )
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
