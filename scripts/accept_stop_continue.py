"""One <=360s real stop/continue scenario; never restarts or retries a failed first task."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    out = parser.parse_args().output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF, Qt, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from narrative import tool_api
    from narrative.assistant import ASSISTANT_VERSION
    from narrative.chat_store import ChatStore
    from narrative.conversation_runtime import get_existing_runtime
    from narrative.extraction_draft import DRAFT_VERSION
    from narrative.harness import effective_runtime_config, harness_env
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    def save(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def plain(value):
        return value.toVariant() if hasattr(value, "toVariant") else value

    text = "甲在雨中等车。乙递给甲一把伞。"
    marker = "QA-UMBRELLA-" + uuid4().hex[:12]
    first_task = f"请仅在本聊天记住校验词“{marker}”，不要把它写入工程。请先提取完整原文并实际入库，完成后解释提取结果。"
    continuation = "刚才我让你仅在本聊天记住的校验词是什么？只回答该词，不使用工具或修改工程。"
    ops = Operations.create_graph_project(out / "停止继续验收.nproj")
    source_id = ops.import_source("合成雨中递伞原文", text)
    (out / "input.txt").write_bytes(text.encode())
    (out / "first-task.txt").write_bytes(first_task.encode())
    (out / "continuation-task.txt").write_bytes(continuation.encode())
    report = {
        "status": "prepared",
        "started": datetime.now(UTC).isoformat(),
        "agent_version": ASSISTANT_VERSION,
        "draft_version": DRAFT_VERSION,
        "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "source_id": source_id,
        "task_budget": 2,
        "scenario_budget": 1,
        "external_qa_seconds_total": 360,
        "product_task_deadline": None,
        "automatic_retry": False,
        "model_parameters_overridden": False,
        "marker": marker,
        "marker_not_in_graph_required": True,
        "user_projects_modified": False,
        "source_hashes": {
            name: hashlib.sha256((Path("src/narrative") / name).read_bytes()).hexdigest()
            for name in [
                "assistant.py",
                "harness.py",
                "model_gateway.py",
                "messages_bridge.py",
                "native_bridge.py",
                "conversation_runtime.py",
                "conversation_session.py",
                "task_tool_host.py",
                "task_tools.mjs",
                "qml/Main.qml",
                "qml/AssistantPanel.qml",
            ]
        },
    }
    save(out / "manifest.json", report)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    QQuickStyle.setStyle("Fusion")
    bridge = NativeBridge()
    results = []
    errors = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    window.setWidth(1392)
    window.setHeight(1000)
    deadline = None

    def wait(check, seconds=None):
        end = (
            deadline
            if seconds is None
            else min(deadline or float("inf"), time.monotonic() + seconds)
        )
        while time.monotonic() < end:
            app.processEvents()
            if check():
                return True
            time.sleep(0.01)
        return False

    def replies():
        return [p for k, p in results if k == "assistant"]

    def current_runtime():
        return get_existing_runtime(ops)

    def gateway_activity():
        manager = current_runtime()
        if manager is None:
            return {"connection_count": 0, "connected_socket_count": 0, "https_connection_count": 0}
        gateway = manager.runner._gateway
        with gateway._lock:
            epoch = gateway._turn_epoch
            connections = [
                c for c in gateway._connections if gateway._connection_epochs.get(c) == epoch
            ]
            return {
                "observed_at": datetime.now(UTC).isoformat(),
                "current_epoch": epoch,
                "connection_count": len(connections),
                "connected_socket_count": sum(
                    c in gateway._connection_sockets for c in connections
                ),
                "https_connection_count": sum(
                    isinstance(c, http.client.HTTPSConnection) for c in connections
                ),
                "handler_count": gateway._handler_counts.get(epoch, 0),
                "gateway_error_present": bool(gateway.error),
            }

    def capture_identity():
        manager = current_runtime()
        chat = ChatStore(ops.project_info().path).active(create=False)
        process = manager.runner._harness.client._proc if manager else None
        return (
            manager,
            process,
            {
                "manager_identity": id(manager) if manager else None,
                "runner_identity": id(manager.runner) if manager else None,
                "pid": process.pid if process else None,
                "process_alive": process is not None and process.poll() is None,
                "manager_closed": manager.closed if manager else True,
                "chat_id": chat["id"] if chat else None,
                "session_id": chat.get("session") if chat else None,
            },
        )

    def stage(name, payload):
        directory = out / name
        directory.mkdir()
        audit = {}
        metadata = []
        if payload.get("audit_directory"):
            audit_dir = Path(payload["audit_directory"])
            audit = json.loads((audit_dir / "task.json").read_text(encoding="utf-8"))
            metadata = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(audit_dir.glob("metadata-*.json"))
            ]
        calls = [
            c
            for m in metadata
            for q in m.get("model_requests", [])
            for c in q.get("tool_calls", [])
        ]
        record = {
            "result": payload,
            "audit": audit,
            "provider_requests": sum(len(m.get("model_requests", [])) for m in metadata),
            "usage": [m.get("usage") for m in metadata],
            "finish_reasons": [m.get("finish_reason") for m in metadata],
            "tool_names": [c["name"] for c in calls],
            "model_write_calls": [c["name"] for c in calls if tool_api.TOOLS[c["name"]].writes],
        }
        save(directory / "report.json", record)
        save(directory / "tool-calls.json", calls)
        return record

    try:
        settings, _key, _work = harness_env(ops)
        report.update(
            provider=settings.provider,
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
            network_idle_seconds=settings.timeout_seconds,
            config=effective_runtime_config(settings),
        )
        save(out / "manifest.json", report)
        bridge.openProject(str(out / "停止继续验收.nproj"))
        assert wait(lambda: any(k == "project" for k, _ in results), 20)
        window.setProperty("assistantOpen", True)
        assert wait(lambda: not window.property("busy"), 20)
        initial_revision = ops.graph_project_info()["revision"]
        began = time.monotonic()
        deadline = began + 360
        bridge.assistantTurn(first_task, "")
        panel = window.findChild(object, "assistantPanel")
        stop = panel.findChild(object, "assistantStop")
        assert wait(
            lambda: (
                replies()
                or (
                    ops.graph_project_info()["revision"] > initial_revision
                    and stop.property("visible")
                    and stop.property("enabled")
                    and gateway_activity()["connection_count"] > 0
                    and gateway_activity()["connected_socket_count"] > 0
                )
            )
        ), "No writable stop window within scenario budget"
        if replies():
            raise AssertionError(
                "First task naturally completed before stop; no retry or second task issued"
            )
        manager, process, before_stop = capture_identity()
        report["before_stop"] = before_stop
        assert before_stop["process_alive"] and not before_stop["manager_closed"]
        report["revision_on_stop_click"] = ops.graph_project_info()["revision"]
        report["gateway_on_stop_click"] = gateway_activity()
        assert (
            report["gateway_on_stop_click"]["connection_count"] > 0
            and report["gateway_on_stop_click"]["connected_socket_count"] > 0
        ), "Upstream window ended before click; HTTPS cancellation not covered"
        point = stop.mapToScene(QPointF(stop.width() / 2, stop.height() / 2))
        QTest.mouseClick(
            window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint()
        )
        report["stop_clicked_at"] = datetime.now(UTC).isoformat()
        assert wait(lambda: len(replies()) >= 1), "Cancelled result was not delivered"
        cancelled = replies()[0]
        first = stage("task-1-stopped", cancelled)
        assert (
            cancelled.get("status") == "cancelled" and first["audit"].get("status") == "cancelled"
        ), "Stop displayed as a failure"
        assert wait(lambda: not window.property("busy")), "Cancelled task kept UI busy"
        assert wait(
            lambda: any(
                row.get("kind") == "assistant_cancelled"
                for row in plain(panel.property("messages"))
            )
        ), "Cancelled UI row not shown"
        report["cancelled_UI_messages"] = plain(panel.property("messages"))
        window.grabWindow().save(str(out / "after-stop.png"))
        retained, retained_process, after_stop = capture_identity()
        report["after_stop"] = after_stop
        report["gateway_after_stop"] = gateway_activity()
        assert (
            retained is manager
            and retained_process is process
            and after_stop["process_alive"]
            and not after_stop["manager_closed"]
        ), "Stop closed/replaced resident runtime"
        assert (
            before_stop["session_id"] == after_stop["session_id"]
            and before_stop["chat_id"] == after_stop["chat_id"]
        )
        settled_revision = ops.graph_project_info()["revision"]
        settled_records = ops.property_records()
        settled_graph = ops.graph_snapshot()
        report["revision_after_stop_settled"] = settled_revision
        report["cancelled_result_revision"] = cancelled.get("revision")
        report["partial_commit_preserved"] = settled_revision > initial_revision
        report["cancelled_refresh_flag"] = cancelled.get("graph_reset_required")
        assert marker not in json.dumps([settled_records, settled_graph], ensure_ascii=False), (
            "Marker was written into graph"
        )
        bridge.assistantTurn(continuation, "")
        assert wait(lambda: len(replies()) >= 2), (
            "Continuation did not complete within scenario budget"
        )
        second = stage("task-2-continued", replies()[1])
        same, same_process, after_continue = capture_identity()
        report["after_continue"] = after_continue
        report["gateway_after_continue"] = gateway_activity()
        chat = ChatStore(ops.project_info().path).load(before_stop["chat_id"])
        save(out / "chat.json", chat)
        cancelled_messages = [m for m in chat["messages"] if m.get("kind") == "assistant_cancelled"]
        restart_messages = [m for m in chat["messages"] if m.get("kind") == "runtime_restart"]
        conditions = {
            "same_manager_and_process": same is manager
            and same_process is process
            and after_continue["process_alive"],
            "same_chat_and_session": after_continue["chat_id"] == before_stop["chat_id"]
            and after_continue["session_id"] == before_stop["session_id"],
            "continuation_completed": second["audit"].get("status") == "completed",
            "private_marker_recalled": marker in str(replies()[1].get("answer", "")),
            "no_runtime_restart_message": not restart_messages,
            "HTTPS_inflight_observed_on_click": report["gateway_on_stop_click"][
                "https_connection_count"
            ]
            > 0
            and report["gateway_on_stop_click"]["connected_socket_count"] > 0,
            "upstream_drained_after_stop": report["gateway_after_stop"]["connection_count"] == 0
            and report["gateway_after_stop"]["handler_count"] == 0,
            "no_old_gateway_error_after_continue": not report["gateway_after_continue"][
                "gateway_error_present"
            ],
            "partial_commit_preserved_and_reported": settled_revision > initial_revision
            and cancelled.get("revision") == settled_revision
            and cancelled.get("graph_reset_required") is True,
            "stop_text_not_failure": bool(cancelled_messages)
            and all(
                not any(word in m.get("text", "") for word in ["任务失败", "stdout closed", "超时"])
                for m in cancelled_messages
            ),
            "cancelled_not_failure": bool(cancelled_messages) and not errors,
            "no_continuation_write_calls": not second["model_write_calls"],
            "no_late_old_writes": ops.graph_project_info()["revision"] == settled_revision
            and ops.property_records() == settled_records
            and ops.graph_snapshot() == settled_graph,
        }
        report.update(
            status="passed" if all(conditions.values()) else "not_passed",
            conditions=conditions,
            provider_requests=first["provider_requests"] + second["provider_requests"],
            elapsed_seconds=time.monotonic() - began,
        )
        window.grabWindow().save(str(out / "after-continue.png"))
    except Exception as exc:
        report.update(status="not_completed", error_type=type(exc).__name__, error=str(exc))
    finally:
        for job in list(bridge._assistant_jobs.values()):
            if job.get("task_id") and not job["cancel"].is_set():
                bridge.assistantStop(job["chat_id"], job["task_id"])
        save(out / "events.json", results)
        save(out / "errors.json", errors)
        save(out / "final-graph.json", ops.graph_snapshot())
        save(out / "final-records.json", ops.property_records())
        available_audits = []
        all_requests = []
        from narrative.project_package import layout

        for audit_path in sorted(
            (layout(ops.project_info().path).extraction / "assistant" / "tasks").glob("*/task.json")
        ):
            task = json.loads(audit_path.read_text(encoding="utf-8"))
            files = sorted(audit_path.parent.glob("metadata-*.json"))
            for file in files:
                meta = json.loads(file.read_text(encoding="utf-8"))
                all_requests.extend(meta.get("model_requests", []))
            available_audits.append(
                {
                    "path": str(audit_path),
                    "status": task.get("status"),
                    "metadata_files": [str(f) for f in files],
                }
            )
        known_usage = [q["usage"] for q in all_requests if isinstance(q.get("usage"), dict)]
        report["available_task_audits"] = available_audits
        report["provider_requests_observed"] = len(all_requests)
        report["known_usage"] = {
            key: sum(u.get(key, 0) for u in known_usage)
            for key in ["prompt_tokens", "completion_tokens", "total_tokens"]
        }
        report["unknown_usage_requests"] = len(all_requests) - len(known_usage)
        report["finished"] = datetime.now(UTC).isoformat()
        report["teardown_closes_project_runtime"] = True
        save(out / "report.json", report)
        bridge.close()
        window.close()
        engine.deleteLater()
        app.processEvents()
    print(json.dumps({"output": str(out), "status": report["status"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
