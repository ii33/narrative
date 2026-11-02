"""One isolated typed add_property behavioral-role relationship acceptance task."""

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
    from narrative.tool_api import call

    def save(name, value):
        (directory / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    text = "甲与乙是邻居。甲设局骗走乙的钱。丙袭击乙，乙挡开后离去。"
    task = "请依据完整原文先提取人物与叙事结构并实际提交，可带必要的非人物关系属性。人物关系部分随后全部通过add_property入库，新增定义按工具schema明确值类型和对称性；再按原文自审查漏、重复、矛盾及失效并实际修正。关系性质、对象与方向依据原文，不把共现或单次行为推成无证据的长期关系，不只描述计划。"

    ops = Operations.create_graph_project(directory / "行为关系类型验收.nproj")
    sid = ops.import_source("合成邻居诈骗与袭击原文", text)
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
            for name in [
                "assistant.py",
                "extraction.py",
                "extraction_refs.py",
                "extraction_draft.py",
                "property_core.py",
                "property_store.py",
                "property_audit.py",
                "temporal_state.py",
                "tool_api.py",
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
        relation_map = call("relation_map", {}, project=layout(ops.project_info().path).entry)
        save("relation-map.json", relation_map)
        person_ids = {n["id"]: n["name"] for n in graph["nodes"] if n["node_type"] == "person"}
        readable_edges = [
            dict(e, from_name=person_ids.get(e["from"]), to_name=person_ids.get(e["to"]))
            for e in relation_map["edges"]
        ]
        save("readable-relationships.json", readable_edges)
        submissions = [
            json.loads(c["arguments"]) for c in tool_calls if c.get("name") == "submit_extraction"
        ]
        add_calls = [
            json.loads(c["arguments"]) for c in tool_calls if c.get("name") == "add_property"
        ]
        save("original-submissions.json", submissions)
        save("original-add-property-arguments.json", add_calls)
        definitions = {v["id"]: v for v in records if v.get("kind") == "property_definition"}
        typed_adds = [
            a
            for a in add_calls
            if a.get("value_type") in {"node", "either"}
            and definitions.get(a.get("property_id"), {}).get("value_type") in {"node", "either"}
        ]
        first_draft = submissions[0].get("result", {}) if submissions else {}
        first_nodes = first_draft.get("nodes", [])

        def ref_type(ref):
            if not isinstance(ref, str):
                return None
            if ref.startswith("@") and ref[1:].isdigit():
                i = int(ref[1:]) - 1
                return first_nodes[i].get("type") if 0 <= i < len(first_nodes) else None
            matches = [
                n for n in first_nodes if n.get("name") == ref or n.get("existing_id") == ref
            ]
            if len(matches) == 1:
                return matches[0].get("type")
            return "person" if ref in person_ids else None

        initial_interpersonal = [
            v
            for v in first_draft.get("properties", [])
            if ref_type(v.get("node")) == "person" and ref_type(v.get("value")) == "person"
        ]
        deferred_interpersonal = bool(submissions) and not initial_interpersonal
        schema_issues = ops.check_project()["issues"]
        save("static-check.json", schema_issues)
        with sqlite3.connect(ops.project.store.path) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        with sqlite3.connect(ops.project.store.path) as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        no_staging = not {"candidates", "property_candidates", "change_candidates"} & tables
        current_errors = [row for row in schema_issues if row["severity"] == "error"]
        unfinished_without_major_story = not any(
            n["node_type"] == "storyline" for n in graph["nodes"]
        )
        program_valid = (
            bool(graph["nodes"])
            and no_staging
            and not current_errors
            and unfinished_without_major_story
            and deferred_interpersonal
            and bool(typed_adds)
        )
        summary.update(
            status="model_completed_needs_semantic_assessment" if program_valid else "not_passed",
            db_schema=version,
            no_staging_tables=no_staging,
            task_completed=True,
            graph_revision_token=graph["revision"],
            node_count=len(graph["nodes"]),
            person_count=len(person_ids),
            property_record_count=len(records),
            unfinished_without_major_story=unfinished_without_major_story,
            relation_edges=readable_edges,
            first_submit_interpersonal_properties=initial_interpersonal,
            first_submit_noninterpersonal_properties=first_draft.get("properties", []),
            interpersonal_deferred_to_tool=deferred_interpersonal,
            typed_add_property_arguments=typed_adds,
            add_property_call_count=len(add_calls),
            static_error_count=len(current_errors),
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
    return 0 if summary["status"] == "model_completed_needs_semantic_assessment" else 1


if __name__ == "__main__":
    raise SystemExit(main())
