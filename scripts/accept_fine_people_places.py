"""One isolated fine-granularity people and locations acceptance task."""

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
    parser.add_argument("--sample", choices=["full", "minimal"], default="full")
    args = parser.parse_args()
    directory = args.output.resolve()
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
    from narrative.tool_api import call

    def save(name, value):
        (directory / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    text = "甲来到青石书店门口，门卫替他推开玻璃门。一名清洁工正在门口扫台阶。甲走进店内阅读区，女店员指给他一个靠窗的座位。随后甲来到北街公交站，站务员告诉他末班车已经开走。"
    if args.sample == "minimal":
        text = "甲来到松桥报亭。值班老人替甲指明去车站的方向。"
    task = "请按本工程设置的精细粒度，依据完整原文提取人物、地点、叙事结构和有原文依据的属性并实际入库。完成后自审查漏、重复、矛盾与失效，按证据修正并补齐，不只描述计划。"

    ops = Operations.create_graph_project(directory / "精细人物地点验收.nproj")
    original_settings = ops.load_project_settings()
    ops.save_project_settings(
        {"extraction_granularity": "fine", "profile": original_settings["profile"]}
    )
    assert ops.load_project_settings()["profile"] == original_settings["profile"]
    sid = ops.import_source(
        "合成报亭问路原文" if args.sample == "minimal" else "合成书店与公交站原文", text
    )
    (directory / "input.txt").write_bytes(text.encode())
    (directory / "user-task.txt").write_bytes(task.encode())
    schema = ops.project.schema()
    summary = {
        "status": "running",
        "extraction_granularity": "fine",
        "qa_sample": args.sample,
        "model_profile_unchanged": True,
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
        "source_id": sid,
        "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
        "source_hashes": {
            name: hashlib.sha256(
                (Path(__file__).resolve().parent.parent / "src/narrative" / name).read_bytes()
            ).hexdigest()
            for name in [
                "assistant.py",
                "extraction.py",
                "extraction_refs.py",
                "extraction_draft.py",
                "property_core.py",
                "property_store.py",
                "temporal_state.py",
                "node_involvement.py",
                "property_categories.py",
                "property_labels.py",
                "graph_display.py",
                "native_bridge.py",
                "tool_api.py",
                "qml/BrowsePage.qml",
                "qml/Main.qml",
            ]
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
        audit_root = layout(ops.project_info().path).extraction / "assistant" / "tasks"
        previous_audits = set(audit_root.glob("*/task.json"))
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
        audit_path = result.get("audit_directory") if result else None
        if not audit_path:
            available = sorted(set(audit_root.glob("*/task.json")) - previous_audits)
            audit_path = str(available[-1].parent) if available else None
        summary["audit_directory"] = audit_path
        if audit_path:
            audit_dir = Path(audit_path)
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
        save("tool-calls.json", tool_calls)
        people = {n["name"]: n["id"] for n in graph["nodes"] if n["node_type"] == "person"}
        locations = {n["name"]: n["id"] for n in graph["nodes"] if n["node_type"] == "location"}
        upper_views = {
            n["id"]: call(
                "get_node", {"node_id": n["id"]}, project=layout(ops.project_info().path).entry
            )
            for n in graph["nodes"]
            if n["node_type"] in {"scene", "big_event", "storyline"}
        }
        event_views = {
            n["id"]: ops.property_node_summary(n["id"])
            for n in graph["nodes"]
            if n["node_type"] == "event"
        }
        save("upper-node-views.json", upper_views)
        upper_summaries = {
            n["id"]: ops.property_node_summary(n["id"])
            for n in graph["nodes"]
            if n["node_type"] in {"scene", "big_event", "storyline"}
        }
        save("upper-property-views.json", upper_summaries)
        save("event-property-views.json", event_views)
        schema_issues = ops.check_project()["issues"]
        save("static-check.json", schema_issues)
        with sqlite3.connect(ops.project.store.path) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        with sqlite3.connect(ops.project.store.path) as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        no_staging = not {"candidates", "property_candidates", "change_candidates"} & tables
        program_valid = (
            bool(graph["nodes"])
            and no_staging
            and not any(row["severity"] == "error" for row in schema_issues)
        )
        summary.update(
            status="model_completed_needs_people_place_assessment"
            if program_valid
            else "not_passed",
            db_schema=version,
            no_staging_tables=no_staging,
            task_completed=True,
            graph_revision_token=graph["revision"],
            person_ids=people,
            location_ids=locations,
            action_count=sum(n["node_type"] == "action" for n in graph["nodes"]),
            node_count=len(graph["nodes"]),
            property_record_count=len(records),
            static_error_count=sum(row["severity"] == "error" for row in schema_issues),
            noT_gate_used=False,
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
    return 0 if summary["status"] == "model_completed_needs_people_place_assessment" else 1


if __name__ == "__main__":
    raise SystemExit(main())
