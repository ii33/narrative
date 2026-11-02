"""Two isolated tasks exercise local ordinal references then verified project-ID reuse."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    out = parser.parse_args().output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from narrative.assistant import ASSISTANT_VERSION
    from narrative.chat_store import ChatStore
    from narrative.extraction_draft import DRAFT_VERSION
    from narrative.harness import CONVERSATION_SYSTEM_PROMPT, effective_runtime_config, harness_env
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout

    def save(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    text = "甲遇见乙。甲说：“我三十岁，是邮差。”"
    tasks = [
        "请依据完整原文提取人物、场景和事件并实际入库。属性本次只提取年龄，职业等其余属性暂不提取；自述按原文语境判断scope与holder。本批关联及年龄属性使用工具支持的@序号，序号对应实际提交的nodes数组，不用节点名字作关联。",
        "请读取已有节点和原文，补充刚才暂未提取的职业属性并实际提交，自述按原文语境判断scope与holder。本次nodes必须为空数组；属性归属、认知或自述holder、at_event都使用刚查询到的正式短ID，明确指定at_event，不重列或新建节点，不用名字猜引用。",
    ]

    ops = Operations.create_graph_project(out / "节点引用验收.nproj")
    sid = ops.import_source("合成相遇与自述原文", text)
    (out / "input.txt").write_bytes(text.encode())
    manifest = {
        "status": "prepared",
        "started": datetime.now(UTC).isoformat(),
        "input_characters": len(text),
        "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "source_id": sid,
        "agent_version": ASSISTANT_VERSION,
        "draft_version": DRAFT_VERSION,
        "schema": ops.project.schema(),
        "task_budget": 2,
        "automatic_retry": False,
        "model_parameters_overridden": False,
        "product_task_deadline": None,
        "external_qa_seconds_per_task": 360,
        "user_projects_modified": False,
        "system_prompt_sha256": hashlib.sha256(CONVERSATION_SYSTEM_PROMPT.encode()).hexdigest(),
        "source_hashes": {
            name: hashlib.sha256(
                (Path(__file__).resolve().parents[1] / "src/narrative" / name).read_bytes()
            ).hexdigest()
            for name in (
                "assistant.py",
                "extraction.py",
                "extraction_draft.py",
                "extraction_refs.py",
                "tool_api.py",
            )
        },
    }
    save(out / "manifest.json", manifest)
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results, errors, stages = [], [], []
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

    def actual_pid():
        runner = bridge._assistant_runner
        process = getattr(getattr(getattr(runner, "_harness", None), "client", None), "_proc", None)
        return process.pid if process else None

    def run(index, task):
        directory = out / f"task-{index}"
        directory.mkdir()
        (directory / "user-task.txt").write_bytes(task.encode())
        offset, error_offset = len(results), len(errors)
        for name, expected in manifest["source_hashes"].items():
            assert (
                hashlib.sha256(
                    (Path(__file__).resolve().parents[1] / "src/narrative" / name).read_bytes()
                ).hexdigest()
                == expected
            ), "code changed between tasks"
        audit_root = layout(ops.project_info().path).extraction / "assistant" / "tasks"
        previous_audits = set(audit_root.glob("*/task.json"))
        began = time.monotonic()
        bridge.assistantTurn(task, "")
        finished = wait(
            lambda: (
                any(k == "assistant" for k, _ in results[offset:]) or len(errors) > error_offset
            ),
            360,
        )
        if not finished:
            for job in list(bridge._assistant_jobs.values()):
                if job.get("task_id"):
                    bridge.assistantStop(job["chat_id"], job["task_id"])
            wait(
                lambda: (
                    any(k == "assistant" for k, _ in results[offset:]) or len(errors) > error_offset
                )
            )
        reply = next((p for k, p in results[offset:] if k == "assistant"), {})
        audit, metadata = {}, []
        audit_path = reply.get("audit_directory")
        if not audit_path:
            available = sorted(set(audit_root.glob("*/task.json")) - previous_audits)
            audit_path = str(available[-1].parent) if available else None
        if audit_path:
            audit_dir = Path(audit_path)
            audit = json.loads((audit_dir / "task.json").read_text(encoding="utf-8"))
            metadata = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(audit_dir.glob("metadata-*.json"))
            ]
        calls = [
            c
            for m in metadata
            for request in m.get("model_requests", [])
            for c in request.get("tool_calls", [])
        ]
        submissions = []
        for call in calls:
            if call.get("name") == "submit_extraction":
                try:
                    submissions.append(json.loads(call.get("arguments", "{}")))
                except (ValueError, TypeError):
                    pass
        graph = ops.graph_snapshot()
        chat_store = ChatStore(ops.project_info().path)
        chat = chat_store.active(create=False)
        stage = {
            "task": index,
            "completed": finished
            and audit.get("status") == "completed"
            and len(errors) == error_offset,
            "task_status": audit.get("status"),
            "observation_expired": not finished,
            "elapsed_seconds": time.monotonic() - began,
            "actual_pid": actual_pid(),
            "chat_id": chat["id"] if chat else None,
            "audit_directory": audit_path,
            "code_hashes": manifest["source_hashes"],
            "provider_requests": sum(len(m.get("model_requests", [])) for m in metadata),
            "usage": [m.get("usage") for m in metadata],
            "finish_reasons": [m.get("finish_reason") for m in metadata],
            "errors": errors[error_offset:],
            "node_ids": [n["id"] for n in graph["nodes"]],
            "event_ids": [n["id"] for n in graph["nodes"] if n["node_type"] == "event"],
            "tool_names": [c.get("name") for c in calls],
            "submissions": submissions,
            "value_records": [
                r for r in ops.property_records() if r.get("kind") == "property_value"
            ],
        }
        save(directory / "report.json", stage)
        save(directory / "snapshot.json", graph)
        save(directory / "property-records.json", ops.property_records())
        save(directory / "tool-calls.json", calls)
        save(directory / "chat.json", chat_store.load(chat["id"]) if chat else {})
        check = ops.check_project()
        stage["static_error_count"] = sum(r["severity"] == "error" for r in check["issues"])
        save(directory / "static-check.json", check)
        save(directory / "report.json", stage)
        stages.append(stage)
        save(out / "stages.json", stages)
        return stage

    try:
        settings, _key, _work = harness_env(ops)
        manifest.update(
            provider=settings.provider,
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
            network_idle_seconds=settings.timeout_seconds,
            config=effective_runtime_config(settings),
        )
        save(out / "manifest.json", manifest)
        bridge.openProject(str(layout(ops.project_info().path).entry))
        assert wait(lambda: any(k == "project" for k, _ in results)), "project did not open"
        first = run(1, tasks[0])
        assert first["completed"], "first task incomplete; second task not issued"
        second = run(2, tasks[1])
        assert second["completed"], "second task incomplete"
        manifest.update(
            status="observed",
            same_chat=first["chat_id"] == second["chat_id"],
            same_pid=bool(first["actual_pid"]) and first["actual_pid"] == second["actual_pid"],
            event_ids_preserved=first["event_ids"] == second["event_ids"],
        )
        first_properties = [
            p
            for submission in first["submissions"]
            for p in submission.get("result", {}).get("properties", [])
        ]
        first_local = bool(first_properties) and all(
            re.fullmatch(r"@[1-9]\d*", str(p.get("node", ""))) for p in first_properties
        )
        known_ids = set(first["node_ids"])
        second_payloads = [submission.get("result", {}) for submission in second["submissions"]]
        second_properties = [
            p for payload in second_payloads for p in payload.get("properties", [])
        ]
        second_direct = (
            bool(second_properties)
            and all(payload.get("nodes") == [] for payload in second_payloads)
            and all(
                p.get("node") in known_ids
                and p.get("at_event") in known_ids
                and (not p.get("holder") or p["holder"] in known_ids)
                and (
                    p.get("value_type") not in {"node", "either"}
                    or p.get("value") in known_ids
                    or p.get("value") in {"无", "未知"}
                )
                for p in second_properties
            )
        )
        first_values, second_values = first["value_records"], second["value_records"]
        new_value_ids = {r["id"] for r in second_values} - {r["id"] for r in first_values}
        no_duplicate_nodes = set(first["node_ids"]) == set(second["node_ids"])
        manifest.update(
            first_property_local_reference=first_local,
            second_empty_nodes_direct_reference=second_direct,
            second_new_value_ids=sorted(new_value_ids),
            no_duplicate_nodes=no_duplicate_nodes,
        )
        passed = (
            first_local
            and second_direct
            and bool(first_values)
            and bool(new_value_ids)
            and no_duplicate_nodes
            and manifest["same_chat"]
            and manifest["same_pid"]
            and not first["static_error_count"]
            and not second["static_error_count"]
        )
        manifest["status"] = "passed" if passed else "not_passed"

    except Exception as exc:
        manifest.update(status="not_passed", error_type=type(exc).__name__, error=str(exc))
    finally:
        bridge.close()
        app.processEvents()
        manifest["finished"] = datetime.now(UTC).isoformat()
        save(out / "events.json", results)
        save(out / "report.json", manifest)
    print(json.dumps({"directory": str(out), "status": manifest["status"]}, ensure_ascii=False))
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
