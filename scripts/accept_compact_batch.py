"""One isolated compact batch-location and direct-submit acceptance task."""

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
    from PySide6.QtWidgets import QApplication

    from narrative.assistant import ASSISTANT_VERSION
    from narrative.chat_store import ChatStore
    from narrative.extraction_draft import DRAFT_VERSION
    from narrative.harness import CONVERSATION_SYSTEM_PROMPT, effective_runtime_config, harness_env
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout

    def save(name, value):
        (directory / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    text = "甲遇见乙。甲向乙点头。"
    task = "请依据完整原文提取人物、场景、事件和明确属性并实际入库。把需要核对的多处引文一次批量定位，然后提交；不要只描述计划。"
    ops = Operations.create_graph_project(directory / "精简批写验收.nproj")
    sid = ops.import_source("合成相遇与点头原文", text)
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
        "external_qa_seconds": 360,
        "external_qa_previous_planned_seconds": 180,
        "external_qa_change_note": "发起前扩大外部观察窗口；不改产品任务时限或模型参数，不增加任务或重试。",
        "source_id": sid,
        "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
        "source_hashes": {
            name: hashlib.sha256(
                (Path(__file__).resolve().parent.parent / "src/narrative" / name).read_bytes()
            ).hexdigest()
            for name in ["assistant.py", "extraction.py", "property_core.py", "tool_api.py"]
        },
        "user_projects_modified": False,
    }
    save("manifest.json", summary)
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))

    def wait(check, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
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
        tool_calls = [
            c
            for m in metadata
            for req in m.get("model_requests", [])
            for c in req.get("tool_calls", [])
        ]
        located = []
        for tool_call in tool_calls:
            if tool_call.get("name") == "locate_source":
                try:
                    located.append(json.loads(tool_call.get("arguments", "{}")))
                except (ValueError, TypeError):
                    pass
        locate_ok = any(len(args.get("quotes", [])) >= 2 for args in located)
        submit_ok = any(c.get("name") == "submit_extraction" for c in tool_calls)
        save("tool-calls.json", tool_calls)
        schema_issues = ops.check_project()["issues"]
        save("static-check.json", schema_issues)
        with sqlite3.connect(ops.project.store.path) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        with sqlite3.connect(ops.project.store.path) as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        no_staging = not {"candidates", "property_candidates", "change_candidates"} & tables
        summary.update(
            status="passed"
            if locate_ok
            and submit_ok
            and graph["nodes"]
            and no_staging
            and not any(r["severity"] == "error" for r in schema_issues)
            else "not_passed",
            batch_locate_used=locate_ok,
            submit_called=submit_ok,
            locate_arguments=located,
            db_schema=version,
            no_staging_tables=no_staging,
            task_completed=True,
            graph_revision_token=graph["revision"],
            node_count=len(graph["nodes"]),
            property_record_count=len(records),
        )

    except Exception as exc:
        summary.update(status="not_passed", error_type=type(exc).__name__, error=str(exc))
    finally:
        bridge.close()
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
