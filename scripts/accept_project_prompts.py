"""Two planned real turns test project prompt edits in one genuine SDK chat."""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import accept_screenplay as qa

TEXT = "常用便签：窗台放着蓝杯，便签校验码为QZ-4827。"
TASKS = ["请read_source读取给定短原文，记住其中窗台放着的物品和便签校验码，简短确认。这是当前聊天稍后会继续使用的事实，不修改工程业务数据。",
         "请project_info读取当前工程概况，再回忆上一轮原文中窗台放着的物品和便签校验码。不要重新读取原文，也不要搜索聊天文件；用当前真实会话记忆简短作答，不修改业务数据。"]
MARKERS = ["工程提示甲已生效", "工程提示乙已生效"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--token-budget", type=int, default=250000)
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from narrative.assistant import tool_catalog
    from narrative.chat_store import ChatStore
    from narrative.harness import effective_runtime_config, harness_env
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout
    from narrative.tool_api import TOOLS

    def save(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {"status": "running", "started": datetime.now(UTC).isoformat(), "prompt_version": "project-prompts-two-turns/1",
        "task_budget": 2, "tasks_started": 0, "automatic_retry": False, "planned_turns": 2,
        "external_qa_seconds": args.seconds, "external_usage_budget_tokens": args.token_budget,
        "product_task_deadline": None, "user_projects_modified": False, "turns": [],
        "input_sha256": qa.digest(TEXT.encode()), "implementation_sha256": {str(path): qa.digest(path.read_bytes()) for path in sorted({
            *Path("src/narrative").glob("*prompt*.py"), *Path("src/narrative").glob("conversation*.py"),
            *Path("src/narrative").glob("screenplay*.py"), *Path("src/narrative").glob("tool*.py"),
            *(Path("src/narrative") / name for name in ("assistant.py", "harness.py", "model_gateway.py", "native_bridge.py", "graph_operations.py")),
            *Path("src/narrative/qml").glob("*.qml"), Path(__file__), Path(qa.__file__),
        }) if path.is_file()}}
    save("tool-catalog.json", tool_catalog())
    (directory / "input.txt").write_text(TEXT, encoding="utf-8")
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))
    observer = watchdog = ops = other = None

    def wait(check, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return True
            time.sleep(0.01)
        return False

    def stop():
        with bridge._assistant_lock:
            jobs = list(bridge._assistant_jobs.values())
        for job in jobs:
            if job.get("task_id"):
                bridge.assistantStop(job["chat_id"], job["task_id"])

    try:
        ops = Operations.create_graph_project(directory / "工程提示验收.nproj")
        other = Operations.create_graph_project(directory / "提示隔离对照.nproj")
        source_id = ops.import_source("短便签", TEXT)
        baseline, isolation = ops.load_prompt_settings(), other.load_prompt_settings()
        base_system = next(row["default"] for row in baseline["prompts"] if row["id"] == "system")
        before = ops.graph_snapshot()
        before_properties = ops.property_records()
        report["fixture_origin"] = "Operations isolated project with one short source; two preplanned real chat turns separated by project system prompt edit"
        save("prompt-settings-before.json", baseline)
        settings, _key, _workdir = harness_env(ops)
        report.update(provider=settings.provider, model=settings.model, reasoning_effort=settings.reasoning_effort,
            configuration=effective_runtime_config(settings))
        bridge.openProject(str(layout(ops.project_info().path).entry))
        if not wait(lambda: any(kind == "project" for kind, _ in results) or errors, 20) or errors:
            raise RuntimeError("isolated project did not open")
        audit_root = layout(ops.project_info().path).extraction / "assistant" / "tasks"
        observer = qa.TaskUsageObserver(lambda: bridge._assistant_runner.usage_metadata() if bridge._assistant_runner else {}, audit_root)
        watchdog = qa.ExternalBudgetWatchdog(observer.total, stop, args.seconds, args.token_budget).start()
        for index, task in enumerate(TASKS):
            override = base_system + "\n工程自定义格式要求：最终回复的第一行必须逐字为：" + MARKERS[index] + "。该行不加引号、句号或Markdown标记。换行后简洁作答。"
            current = ops.load_prompt_settings()
            configured = ops.save_prompt_override("system", override, current["revision"], "预先规划的第" + str(index + 1) + "轮提示配置验收")
            save(f"prompt-settings-turn-{index + 1}.json", configured)
            message = task + ("\nsource_id=" + source_id if index == 0 else "")
            (directory / f"user-task-{index + 1}.txt").write_text(message, encoding="utf-8")
            start = len(results)
            report["tasks_started"] += 1
            bridge.assistantTurn(message, "")
            if not wait(lambda start=start: any(kind == "assistant" for kind, _ in results[start:]) or errors or watchdog.triggered.is_set(), args.seconds):
                stop()
                raise RuntimeError("external QA time budget reached; no retry")
            if watchdog.triggered.is_set():
                wait(lambda: not bridge._assistant_jobs, 20)
                raise RuntimeError("external QA observation budget reached; no retry")
            if errors:
                raise RuntimeError("real task failed; no retry")
            result = next(payload for kind, payload in results[start:] if kind == "assistant")
            audit_dir = Path(result["audit_directory"])
            audit = json.loads((audit_dir / "task.json").read_text(encoding="utf-8"))
            metadata = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(audit_dir.glob("metadata-*.json"))]
            raw = "\n".join(path.read_text(encoding="utf-8") for path in sorted(audit_dir.glob("raw-*.txt")))
            store = ChatStore(ops.project_info().path)
            chat = store.active(create=False)
            turn = {"index": index + 1, "task_sha256": qa.digest(message.encode()), "configured_system_sha256": qa.digest(override.encode()),
                "expected_prompt_snapshot": {"revision": configured["revision"], "versions": configured["effective_versions"], "texts": {row["id"]: row["effective"] for row in configured["prompts"]}},
                "audit_directory": str(audit_dir), "audit": audit, "metadata": metadata,
                "response_text": raw, "chat_id": chat["id"], "sdk_session_id": audit.get("session_id"),
                "sdk_directory": str(store.sdk_directory()), "business_steps": audit.get("steps", [])}
            report["turns"].append(turn)
            save(f"turn-{index + 1}.json", turn)
            if audit.get("status") != "completed":
                raise RuntimeError("planned task incomplete; do not launch another task")
            if not wait(lambda: not bridge._assistant_jobs, 10):
                raise RuntimeError("completed task did not settle; do not launch another task")
            if other.load_prompt_settings() != isolation:
                raise RuntimeError("prompt settings leaked to another project")
        restored = ops.save_prompt_override("system", None, ops.load_prompt_settings()["revision"], "验收恢复默认，不发第三轮请求")
        save("prompt-settings-restored.json", restored)
        one, two = report["turns"]
        def called(turn, name):
            return any(step["tool"] == name and step.get("ok") for step in turn["business_steps"])
        report["checks"] = {"both_planned_tasks_completed": all(turn["audit"].get("status") == "completed" for turn in report["turns"]),
            "first_custom_system_effective": one["response_text"].strip().startswith(MARKERS[0]),
            "updated_system_effective_in_same_chat": two["response_text"].strip().startswith(MARKERS[1]) and MARKERS[0] not in two["response_text"],
            "first_source_read_succeeded": called(one, "read_source"), "second_project_read_succeeded": called(two, "project_info"),
            "second_did_not_reread_source": not called(two, "read_source"),
            "second_tools_cannot_recover_fact_elsewhere": bool(two["business_steps"]) and all(step["tool"] == "project_info" for step in two["business_steps"]),
            "sdk_context_recalled_fact": "蓝杯" in two["response_text"] and "QZ-4827" in two["response_text"],
            "same_chat_and_sdk_session": one["chat_id"] == two["chat_id"] and bool(one["sdk_session_id"]) and one["sdk_session_id"] == two["sdk_session_id"] and one["sdk_directory"] == two["sdk_directory"],
            "both_prompt_snapshots_exact": all(turn["audit"].get("prompt_config") == turn["expected_prompt_snapshot"] for turn in report["turns"]),
            "project_config_isolated": other.load_prompt_settings() == isolation,
            "default_restored": next(row for row in restored["prompts"] if row["id"] == "system")["effective"] == base_system and not next(row for row in restored["prompts"] if row["id"] == "system")["overridden"],
            "source_graph_unchanged": ops.graph_snapshot() == before and ops.property_records() == before_properties,
            "no_business_writes": all(not step.get("ok") or step["tool"] in TOOLS and not TOOLS[step["tool"]].writes for turn in report["turns"] for step in turn["business_steps"])}
        report["status"] = "passed" if all(report["checks"].values()) else "not_passed"
    except Exception as exc:
        report.update(status="not_passed", error_type=type(exc).__name__, error=str(exc))
    finally:
        if watchdog:
            watchdog.close()
            report["observer"] = {"trigger_reason": watchdog.reason, "max_observed_tokens": watchdog.observed_tokens, "error": watchdog.observer_error}
        report["errors"] = errors
        report["actual_total_tokens"] = sum(usage.get("total_tokens", 0) for turn in report["turns"] for metadata in turn["metadata"] for usage in (metadata.get("usage") or []) if isinstance(usage, dict))
        try:
            observed = observer.total() if observer else 0
        except Exception as exc:
            report.update(status="not_passed", observer_final_error=type(exc).__name__ + ": " + str(exc))
            observed = 0
        report["actual_total_tokens"] = max(report["actual_total_tokens"], observed)
        report["model_requests"] = sum(len(metadata.get("model_requests", [])) for turn in report["turns"] for metadata in turn["metadata"])
        save("events.json", results)
        if ops:
            store = ChatStore(ops.project_info().path)
            chat = store.active(create=False)
            save("chat-visible-output.json", store.load(chat["id"]) if chat else {})
            audit_root = layout(ops.project_info().path).extraction / "assistant" / "tasks"
            save("audit-files.json", [{"path": str(path), "sha256": qa.digest(path.read_bytes())} for path in sorted(audit_root.rglob("*")) if path.is_file()])
            metadata = [{"path": str(path), "metadata": json.loads(path.read_text(encoding="utf-8"))} for path in sorted(audit_root.glob("*/metadata-*.json"))]
            save("all-model-metadata.json", metadata)
            save("all-task-audits.json", [{"path": str(path), "audit": json.loads(path.read_text(encoding="utf-8"))} for path in sorted(audit_root.glob("*/task.json"))])
            report["model_requests"] = sum(len(item["metadata"].get("model_requests", [])) for item in metadata)
            report["actual_total_tokens"] = max(report["actual_total_tokens"], sum(usage.get("total_tokens", 0) for item in metadata for usage in (item["metadata"].get("usage") or []) if isinstance(usage, dict)))
        report["usage_budget_compliant"] = report["actual_total_tokens"] <= args.token_budget
        if not report["usage_budget_compliant"]:
            report["status"] = "not_passed"
        bridge.close()
        app.processEvents()
        report["finished"] = datetime.now(UTC).isoformat()
        save("report.json", report)
    print(json.dumps({"directory": str(directory), "status": report["status"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
