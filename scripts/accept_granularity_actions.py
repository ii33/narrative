"""Three bounded QA tasks: two project granularities and explicit event-editor actions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    out = parser.parse_args().output.resolve()
    out.mkdir(parents=True, exist_ok=False)
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

    text = "甲走进屋，停下并向乙点头。乙起身走到门口。"
    task = "请根据完整原文提取人物、地点、场景、事件与有原文依据的属性，依据工程设置的粒度组织事件并实际入库。"

    def save(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def public_profile(profile):
        result = {k: v for k, v in profile.items() if k not in {"api_key", "base_url", "headers"}}
        parsed = urlsplit(profile.get("base_url") or "")
        result["base_url_origin"] = (
            parsed.scheme
            + "://"
            + (parsed.hostname or "")
            + (f":{parsed.port}" if parsed.port else "")
        )
        return result

    summary = {
        "status": "running",
        "agent_version": ASSISTANT_VERSION,
        "draft_version": DRAFT_VERSION,
        "started": datetime.now(UTC).isoformat(),
        "input_characters": len(text),
        "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "system_prompt_sha256": hashlib.sha256(CONVERSATION_SYSTEM_PROMPT.encode()).hexdigest(),
        "task_budget": 3,
        "automatic_retry": False,
        "product_task_deadline": None,
        "external_qa_seconds_per_task": 180,
        "model_parameters_overridden": False,
        "setting_groups": ["standard", "fine"],
        "user_projects_modified": False,
        "source_hashes": {
            name: hashlib.sha256((QML_PATH.parent.parent / name).read_bytes()).hexdigest()
            for name in [
                "assistant.py",
                "extraction.py",
                "graph_models.py",
                "project_settings.py",
                "native_bridge.py",
            ]
        },
    }
    save(out / "manifest.json", summary)
    app = QApplication.instance() or QApplication([])
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
    families = QFontDatabase.applicationFontFamilies(fid)
    if families:
        app.setFont(QFont(families[0]))
    app.setStyle("Fusion")
    QQuickStyle.setStyle("Fusion")
    bridges = []
    engine = None
    stages = []

    def wait(check, seconds=20):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if check():
                return True
            time.sleep(0.01)
        return False

    def settle():
        for _ in range(8):
            app.processEvents()
            time.sleep(0.01)

    def plain(value):
        return value.toVariant() if hasattr(value, "toVariant") else value

    def pid(bridge):
        proc = getattr(
            getattr(getattr(bridge._assistant_runner, "_harness", None), "client", None),
            "_proc",
            None,
        )
        return proc.pid if proc else None

    def group(name, granularity):
        directory = out / name
        directory.mkdir()
        ops = Operations.create_graph_project(directory / f"{name}.nproj")
        source = ops.import_source("合成进屋与招呼原文", text)
        cfg = ops.load_project_settings()
        profile = cfg["profile"]
        ops.save_project_settings({"extraction_granularity": granularity, "profile": profile})
        settings, _key, _work = harness_env(ops)
        schema = ops.project.schema()
        save(
            directory / "manifest.json",
            {
                "granularity": granularity,
                "profile": public_profile(settings.model_dump()),
                "runtime_config": effective_runtime_config(settings),
                "network_idle_seconds": settings.timeout_seconds,
                "source_id": source,
                "input_sha256": summary["input_sha256"],
                "agent_version": ASSISTANT_VERSION,
                "draft_version": DRAFT_VERSION,
                "graph_contract_version": schema["contract_version"],
                "property_contract_version": schema["property_contract_version"],
            },
        )
        (directory / "input.txt").write_bytes(text.encode())
        (directory / "user-task.txt").write_bytes(task.encode())
        bridge = NativeBridge()
        bridges.append(bridge)
        results = []
        errors = []
        bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
        bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))
        return {
            "directory": directory,
            "ops": ops,
            "bridge": bridge,
            "results": results,
            "errors": errors,
            "profile": profile,
            "granularity": granularity,
        }

    def open_group(g):
        g["bridge"].openProject(str(layout(g["ops"].project_info().path).entry))
        assert wait(lambda: any(k == "project" for k, _ in g["results"]))

    def run(g, name, trigger):
        directory = g["directory"] / name
        directory.mkdir()
        before = len([p for k, p in g["results"] if k == "assistant"])
        error_count = len(g["errors"])
        began = time.monotonic()
        trigger()
        finished = wait(
            lambda: (
                len([p for k, p in g["results"] if k == "assistant"]) > before
                or len(g["errors"]) > error_count
            ),
            180,
        )
        if not finished:
            for job in list(g["bridge"]._assistant_jobs.values()):
                if job.get("task_id"):
                    g["bridge"].assistantStop(job["chat_id"], job["task_id"])
            wait(
                lambda: (
                    len([p for k, p in g["results"] if k == "assistant"]) > before
                    or len(g["errors"]) > error_count
                )
            )
        replies = [p for k, p in g["results"] if k == "assistant"]
        result = replies[before] if len(replies) > before else None
        metadata = []
        audit = {}
        if result and result.get("audit_directory"):
            audit_dir = Path(result["audit_directory"])
            audit = json.loads((audit_dir / "task.json").read_text(encoding="utf-8"))
            metadata = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(audit_dir.glob("metadata-*.json"))
            ]
        snapshot = g["ops"].graph_snapshot()
        records = g["ops"].property_records()
        chat = ChatStore(g["ops"].project_info().path).active(create=False)
        stage = {
            "name": name,
            "granularity": g["granularity"],
            "task_status": audit.get("status"),
            "completed": finished and audit.get("status") == "completed",
            "errors": g["errors"][error_count:],
            "observation_expired": not finished,
            "elapsed_seconds": time.monotonic() - began,
            "actual_pid": pid(g["bridge"]),
            "chat_id": chat["id"] if chat else None,
            "model_requests": sum(len(m.get("model_requests") or []) for m in metadata),
            "usage": [m.get("usage") for m in metadata],
            "finish_reasons": [m.get("finish_reason") for m in metadata],
            "event_ids": [n["id"] for n in snapshot["nodes"] if n["node_type"] == "event"],
            "action_ids": [n["id"] for n in snapshot["nodes"] if n["node_type"] == "action"],
            "audit_directory": result.get("audit_directory") if result else None,
        }
        save(directory / "report.json", stage)
        save(directory / "snapshot.json", snapshot)
        save(directory / "property-records.json", records)
        save(
            directory / "chat.json",
            ChatStore(g["ops"].project_info().path).load(chat["id"]) if chat else {},
        )
        stages.append(stage)
        save(out / "stages.json", stages)
        return stage

    try:
        standard = group("standard", "standard")
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("bridge", standard["bridge"])
        engine.load(QUrl.fromLocalFile(str(QML_PATH)))
        window = engine.rootObjects()[0]
        window.setWidth(1392)
        open_group(standard)
        first = run(standard, "task-1-standard", lambda: standard["bridge"].assistantTurn(task, ""))
        assert first["completed"] and not first["errors"], (
            "standard task did not complete; remaining tasks not issued"
        )
        fine = group("fine", "fine")
        open_group(fine)
        assert fine["profile"] == standard["profile"], "model profiles differ beyond granularity"
        second = run(fine, "task-2-fine", lambda: fine["bridge"].assistantTurn(task, ""))
        fine["bridge"].close()
        assert second["completed"] and not second["errors"], (
            "fine task did not complete; action task not issued"
        )
        density_ok = len(second["event_ids"]) > len(first["event_ids"])
        implicit_actions_absent = not first["action_ids"] and not second["action_ids"]
        candidates = [
            n for n in standard["ops"].graph_snapshot()["nodes"] if n["node_type"] == "event"
        ]
        parent = max(
            candidates, key=lambda n: sum(len(s["quote"]) for s in n.get("evidence_spans", []))
        )
        save(out / "selected-parent-event.json", parent)
        standard["bridge"].openChat(first["chat_id"])
        assert wait(
            lambda: (
                standard["bridge"]._assistant_chat == first["chat_id"]
                and not window.property("busy")
            )
        )
        window.setProperty("currentPage", "browse")
        window.setProperty("assistantOpen", False)
        settle()
        editor = window.findChild(object, "nodeEditor")
        editor.openFor(parent["id"])
        assert wait(lambda: plain(editor.property("node")).get("id") == parent["id"])
        button = window.findChild(object, "editorExtractActions")
        settle()
        assert button.property("visible") and button.property("enabled")
        window.grabWindow().save(str(out / "action-button-before.png"))

        def click_action():
            point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
            QTest.mouseClick(
                window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint()
            )

        third = run(standard, "task-3-explicit-actions", click_action)
        after = standard["ops"].graph_snapshot()
        actions = [n for n in after["nodes"] if n["node_type"] == "action"]
        parents = {
            a["id"]: [n["id"] for n in after["nodes"] if a["id"] in n.get("events", [])]
            for a in actions
        }
        parent_ok = bool(actions) and all(p == [parent["id"]] for p in parents.values())
        same_runtime = (
            first["actual_pid"] is not None
            and third["actual_pid"] == first["actual_pid"]
            and first["chat_id"] == third["chat_id"]
        )
        panel_ok = window.property("assistantOpen") and not editor.property("visible")
        window.grabWindow().save(str(out / "action-task-panel-after.png"))
        diagnostics = standard["ops"].check_project()["issues"]
        save(out / "final-static-check.json", diagnostics)
        summary.update(
            status="passed"
            if third["completed"]
            and not third["errors"]
            and density_ok
            and implicit_actions_absent
            and parent_ok
            and same_runtime
            and panel_ok
            and not any(r["severity"] == "error" for r in diagnostics)
            else "not_passed",
            standard_event_count=len(first["event_ids"]),
            fine_event_count=len(second["event_ids"]),
            density_increased=density_ok,
            no_implicit_actions=implicit_actions_absent,
            explicit_action_count=len(actions),
            action_parents=parents,
            selected_parent=parent["id"],
            same_chat_and_pid=same_runtime,
            shared_panel_open=panel_ok,
            model_requests=sum(s["model_requests"] for s in stages),
            stages=stages,
        )
    except Exception as exc:
        summary.update(
            status="not_passed", error_type=type(exc).__name__, error=str(exc), stages=stages
        )
    finally:
        for bridge in bridges:
            bridge.close()
        if engine:
            engine.deleteLater()
        app.processEvents()
        summary["finished"] = datetime.now(UTC).isoformat()
        save(out / "report.json", summary)
    print(json.dumps({"directory": str(out), "status": summary["status"]}, ensure_ascii=False))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
