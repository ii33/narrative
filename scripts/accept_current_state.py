"""One normal live value timeline, followed by explicit program/UI suffix rollback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    directory = parser.parse_args().output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF, Qt, QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
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

    text = "甲拿石头。甲把石头交给乙。"
    first = "甲拿石头。"
    cut = len(first) - 1
    task = (
        "请按原文叙述顺序提取人物、场景、事件、物体和属性并实际入库。"
        "保留物体持有状态的先后变化：旧值在原文改变处结束，新值从改变处开始；不要只保留最终状态。"
    )
    ops = Operations.create_graph_project(directory / "值区间与回退验收.nproj")
    sid = ops.import_source("合成石头换手原文", text)
    (directory / "input.txt").write_bytes(text.encode())
    (directory / "user-task.txt").write_bytes(task.encode())
    schema = ops.project.schema()
    summary = {
        "status": "running",
        "started": datetime.now(UTC).isoformat(),
        "agent_version": ASSISTANT_VERSION,
        "draft_version": DRAFT_VERSION,
        "graph_contract_version": schema["contract_version"],
        "property_contract_version": schema["property_contract_version"],
        "input_characters": len(text),
        "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "system_prompt_sha256": hashlib.sha256(CONVERSATION_SYSTEM_PROMPT.encode()).hexdigest(),
        "task_budget": 1,
        "automatic_retry": False,
        "model_parameters_overridden": False,
        "product_task_deadline": None,
        "external_qa_seconds": 180,
        "source_id": sid,
        "rollback_boundary": {"source_id": sid, "start": cut},
        "program_rollback_is_model_output": False,
        "source_hashes": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                QML_PATH.parent / "StaticCheckPage.qml",
                QML_PATH.parent / "BrowsePage.qml",
                QML_PATH.parent.parent / "temporal_state.py",
                QML_PATH.parent.parent / "event_merge.py",
            ]
        },
    }
    save("manifest.json", summary)
    app = QApplication.instance() or QApplication([])
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
    families = QFontDatabase.applicationFontFamilies(fid)
    if families:
        app.setFont(QFont(families[0]))
    app.setStyle("Fusion")
    QQuickStyle.setStyle("Fusion")
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    window.setWidth(1392)

    def plain(value):
        return value.toVariant() if hasattr(value, "toVariant") else value

    def wait(check, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return True
            time.sleep(0.01)
        return False

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
        assert wait(lambda: any(k == "project" for k, _ in results))
        began = time.monotonic()
        bridge.assistantTurn(task, "")
        finished = wait(
            lambda: any(k == "assistant" for k, _ in results) or errors,
            summary["external_qa_seconds"],
        )
        summary["observation_expired"] = not finished
        if not finished:
            for job in list(bridge._assistant_jobs.values()):
                if job.get("task_id"):
                    bridge.assistantStop(job["chat_id"], job["task_id"])
            wait(lambda: any(k == "assistant" for k, _ in results) or errors)
        result = next((p for k, p in results if k == "assistant"), None)
        metadata = []
        audit = {}
        if result and result.get("audit_directory"):
            audit_dir = Path(result["audit_directory"])
            metadata = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(audit_dir.glob("metadata-*.json"))
            ]
            audit = json.loads((audit_dir / "task.json").read_text(encoding="utf-8"))
        store = ChatStore(ops.project_info().path)
        chat = store.active(create=False)
        save("chat.json", store.load(chat["id"]) if chat else {})
        runner = bridge._assistant_runner
        process = getattr(getattr(getattr(runner, "_harness", None), "client", None), "_proc", None)
        summary.update(
            task_status=audit.get("status"),
            errors=errors,
            model_elapsed_seconds=time.monotonic() - began,
            model_requests=sum(len(m.get("model_requests") or []) for m in metadata),
            usage=[m.get("usage") for m in metadata],
            finish_reasons=[m.get("finish_reason") for m in metadata],
            actual_pid=process.pid if process else None,
        )
        graph = ops.graph_snapshot()
        records = ops.property_records()
        save("model-snapshot.json", graph)
        save("model-property-records.json", records)
        assert finished and audit.get("status") == "completed" and not errors, (
            "model task not completed; rollback not attempted"
        )
        people = {n["name"]: n["id"] for n in graph["nodes"] if n["node_type"] == "person"}
        item = next(n for n in graph["nodes"] if n["node_type"] == "item" and n["name"] == "石头")
        holder_name = next(
            (
                r["id"]
                for r in records
                if r["kind"] == "property_definition" and r.get("semantic") == "item_holder"
            ),
            "持有者",
        )
        rows = [
            r
            for r in records
            if r.get("node_id") == item["id"]
            and r.get("property_id") == holder_name
            and (r.get("scope") or "world") == "world"
        ]
        old = next(r for r in rows if r["value"] == people["甲"])
        new = next(r for r in rows if r["value"] == people["乙"])
        assert old["valid_from"]["source_id"] == sid and old["valid_from"]["start"] <= cut
        assert (
            old["valid_to"]["source_id"] == sid
            and old["valid_to"]["start"] == new["valid_from"]["start"] > cut
        )
        assert new.get("valid_to") is None
        assert ops.property_state(item["id"], holder_name)["state"]["value"] == people["乙"]
        assert not any(
            r["kind"] in {"property_retraction", "property_merge", "property_rename"}
            for r in records
        )
        assert not [r for r in ops.check_project()["issues"] if r["severity"] == "error"]
        summary["model_intervals_verified"] = True
        window.setProperty("assistantOpen", False)
        window.setProperty("currentPage", "static_check")
        settle()
        page = window.findChild(object, "staticCheckPage")
        window.findChild(object, "rollbackSource").setProperty("text", sid)
        window.findChild(object, "rollbackStart").setProperty("text", str(cut))
        settle()
        before = ops.graph_snapshot()
        click(window.findChild(object, "previewRollbackButton"))
        assert wait(lambda: bool(plain(page.property("rollbackPlan")).get("valid")))
        plan = plain(page.property("rollbackPlan"))
        save("program-rollback-preview.json", plan)
        assert ops.graph_snapshot() == before
        window.grabWindow().save(str(directory / "rollback-preview.png"))
        click(window.findChild(object, "executeRollbackButton"))
        assert wait(lambda: any(k == "rollback" for k, _ in results))
        rollback = next(p for k, p in results if k == "rollback")
        save("program-rollback-result.json", rollback)
        assert rollback["status"] == "accepted"
        after = ops.graph_snapshot()
        after_records = ops.property_records()
        save("program-snapshot-after-rollback.json", after)
        save("program-property-records-after-rollback.json", after_records)
        assert ops.property_state(item["id"], holder_name)["state"]["value"] == people["甲"]
        assert new["id"] not in {r["id"] for r in after_records}
        assert next(r for r in after_records if r["id"] == old["id"]).get("valid_to") is None
        assert not [r for r in ops.check_project()["issues"] if r["severity"] == "error"]
        window.focusBrowseNode(item["id"], {})
        browse = window.findChild(object, "browsePage")
        assert wait(
            lambda: any(
                r.get("valueNodeId") == people["甲"] and r.get("valueText") == "甲"
                for r in plain(browse.property("propertyRows"))
            )
        )
        window.grabWindow().save(str(directory / "ui-holder-restored.png"))
        with sqlite3.connect(ops.project.store.path) as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            version = db.execute("PRAGMA user_version").fetchone()[0]
        assert version == 9 and "revisions" not in tables
        summary.update(
            status="passed",
            program_rollback_verified=True,
            ui_holder_restored=True,
            db_schema=version,
            db_tables=tables,
            model_holder_record_ids=[old["id"], new["id"]],
            before_revision_token=before["revision"],
            after_revision_token=after["revision"],
        )
    except Exception as exc:
        summary.update(status="not_passed", error_type=type(exc).__name__, error=str(exc))
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
