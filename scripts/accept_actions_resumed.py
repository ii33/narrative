"""Remaining third QA task: native resume, then actual event-editor action click."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original = args.original.resolve()
    out = args.output.resolve()
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
    from narrative.conversation_session import open_conversation_session
    from narrative.extraction_draft import DRAFT_VERSION
    from narrative.harness import HarnessRunner, effective_runtime_config, harness_env
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout

    def save(name, value):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    baseline = json.loads(
        (original / "standard/task-1-standard/report.json").read_text(encoding="utf-8")
    )
    entry = original / "standard/standard.nproj"
    ops = Operations.open(entry)
    store = ChatStore(ops.project_info().path)
    chat = store.load(baseline["chat_id"])
    session = chat["session"]
    untouched = {
        str(p.relative_to(original)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            original / "report.json",
            original / "stages.json",
            original / "standard/task-1-standard/report.json",
            original / "fine/task-2-fine/report.json",
        ]
    }
    summary = {
        "status": "running",
        "started": datetime.now(UTC).isoformat(),
        "agent_version": ASSISTANT_VERSION,
        "draft_version": DRAFT_VERSION,
        "original_group": str(original),
        "remaining_task_number": 3,
        "new_model_task_budget": 1,
        "automatic_retry": False,
        "external_qa_seconds": 360,
        "product_task_deadline": None,
        "model_parameters_overridden": False,
        "original_standard_pid": baseline["actual_pid"],
        "qa_closed_original_runtime": True,
        "cross_cleanup_same_pid_claimed": False,
        "original_chat_id": chat["id"],
        "original_sdk_session_id": session,
        "original_report_hashes": untouched,
        "fine_task_retried": False,
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
    results = []
    errors = []
    bridge.resultReady.connect(lambda k, p: results.append((k, p)))
    bridge.errorOccurred.connect(lambda k, e: errors.append((k, e)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    window.setWidth(1392)

    def plain(value):
        return value.toVariant() if hasattr(value, "toVariant") else value

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

    def process_id(runner):
        proc = getattr(getattr(getattr(runner, "_harness", None), "client", None), "_proc", None)
        return proc.pid if proc else None

    try:
        settings, key, _work = harness_env(ops)
        summary.update(
            provider=settings.provider,
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
            network_idle_seconds=settings.timeout_seconds,
            runtime_config=effective_runtime_config(settings),
            graph_contract_version=ops.project.schema()["contract_version"],
            property_contract_version=ops.project.schema()["property_contract_version"],
        )
        # Native SDK restoration does not ask the model and never rebuilds history as prompt text.
        wrapper, restored_session = open_conversation_session(
            ops, settings, key, HarnessRunner, chat_id=chat["id"]
        )
        runner = wrapper.runner
        manager = wrapper.manager
        before_pid = process_id(runner)
        summary.update(
            native_session_restored=restored_session == session,
            pre_action_pid=before_pid,
            native_restoration_without_agent_turn=True,
            prior_turn_count=wrapper.manifest["turn_count"],
        )
        wrapper.close()
        assert restored_session == session and before_pid is not None and not manager.closed
        save("manifest.json", summary)
        before = ops.graph_snapshot()
        save("snapshot-before.json", before)
        save("records-before.json", ops.property_records())
        events = [n for n in before["nodes"] if n["node_type"] == "event"]
        parent = max(
            events, key=lambda n: sum(len(s["quote"]) for s in n.get("evidence_spans", []))
        )
        save("selected-parent-event.json", parent)
        bridge.openProject(str(layout(ops.project_info().path).entry))
        assert wait(lambda: any(k == "project" for k, _ in results))
        bridge.openChat(chat["id"])
        assert wait(lambda: bridge._assistant_chat == chat["id"] and not window.property("busy"))
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
        began = time.monotonic()
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
        QTest.mouseClick(
            window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint()
        )
        finished = wait(lambda: any(k == "assistant" for k, _ in results) or errors, 360)
        if not finished:
            for job in list(bridge._assistant_jobs.values()):
                if job.get("task_id"):
                    bridge.assistantStop(job["chat_id"], job["task_id"])
            wait(lambda: any(k == "assistant" for k, _ in results) or errors)
        result = next((p for k, p in results if k == "assistant"), None)
        metadata = []
        audit = {}
        if result and result.get("audit_directory"):
            directory = Path(result["audit_directory"])
            audit = json.loads((directory / "task.json").read_text(encoding="utf-8"))
            metadata = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(directory.glob("metadata-*.json"))
            ]
        after = ops.graph_snapshot()
        actions = [n for n in after["nodes"] if n["node_type"] == "action"]
        parents = {
            a["id"]: [n["id"] for n in after["nodes"] if a["id"] in n.get("events", [])]
            for a in actions
        }
        final_chat = store.load(chat["id"])
        after_pid = process_id(bridge._assistant_runner)
        diagnostics = ops.check_project()["issues"]
        save("snapshot-after.json", after)
        save("records-after.json", ops.property_records())
        save("chat-after.json", final_chat)
        save("static-check.json", diagnostics)
        unchanged_reports = all(
            hashlib.sha256((original / name).read_bytes()).hexdigest() == sha
            for name, sha in untouched.items()
        )
        panel_open = window.property("assistantOpen") and not editor.property("visible")
        parents_ok = bool(actions) and all(ids == [parent["id"]] for ids in parents.values())
        same_runtime = after_pid == before_pid and bridge._assistant_runner is runner
        summary.update(
            task_status=audit.get("status"),
            observation_expired=not finished,
            errors=errors,
            elapsed_seconds=time.monotonic() - began,
            model_requests=sum(len(m.get("model_requests") or []) for m in metadata),
            usage=[m.get("usage") for m in metadata],
            finish_reasons=[m.get("finish_reason") for m in metadata],
            audit_directory=result.get("audit_directory") if result else None,
            selected_parent=parent["id"],
            actions=[{"id": n["id"], "name": n["name"]} for n in actions],
            action_parents=parents,
            post_action_pid=after_pid,
            same_runner_and_pid_during_action_task=same_runtime,
            same_original_chat_and_session=final_chat["id"] == chat["id"]
            and final_chat["session"] == session,
            shared_panel_open=panel_open,
            prior_reports_unchanged=unchanged_reports,
            event_ids_before=[n["id"] for n in before["nodes"] if n["node_type"] == "event"],
            event_ids_after=[n["id"] for n in after["nodes"] if n["node_type"] == "event"],
        )
        summary["status"] = (
            "passed"
            if finished
            and audit.get("status") == "completed"
            and not errors
            and parents_ok
            and same_runtime
            and summary["same_original_chat_and_session"]
            and panel_open
            and unchanged_reports
            and not any(r["severity"] == "error" for r in diagnostics)
            else "not_passed"
        )
        window.grabWindow().save(str(out / "action-panel-after.png"))
    except Exception as exc:
        summary.update(status="not_passed", error_type=type(exc).__name__, error=str(exc))
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()
        summary["finished"] = datetime.now(UTC).isoformat()
        save("events.json", results)
        save("report.json", summary)
    print(json.dumps({"directory": str(out), "status": summary["status"]}, ensure_ascii=False))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
