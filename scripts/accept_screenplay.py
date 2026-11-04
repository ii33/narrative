"""One real unified-task acceptance of extraction → adaptation → visual assets.

Never retries or repairs model results. The observation budget is external QA,
not a product task deadline. Run only with an explicit fresh output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

PROMPT_VERSION = "screenplay-writing-acceptance/2"
FIXTURE_ORIGIN = "deterministic Operations fixture; real model output only for scene table and screenplay body"
TEXT = "林岚走到旧车站门口。周川把铜钥匙交给林岚。林岚带着钥匙走向东仓，停下听见门内脚步。"
TASK = """这是本次剧本功能验收的一个统一任务。原文、改编与绑定段落已经由合成fixture准备好，不能改写原文或改编。请在已授权目标段落内实际调用工具完成全部步骤，不只建议：
1. 读取目标段落及read_scene_table_context，按输入范围拆成2场scene，完整plan至少含title/summary/location_text/time_label，可引用真实cast_ids/location_id。用submit_scene_table及刚读到的修订和指纹原子入库。
2. 通过apply_scene_table_changes对第1场plan做一次明确修改（补充conflict“交接必须保持隐秘”）；完整plan其余字段保留。再adopt_scene_table记录工作标记（不是审批）。读取场表确认稳定场次ID。
3. 先read_script_unit_context，再submit_script_unit为第1场写中文正文，包含action与dialogue，各块使用type/payload合同。speaker引用输入中真实人物ID，delivery=on_screen等合法值；不得自己伪造ID。
4. 调用prepare_script_writing_targets，以本任务screenplay_id及当前场次显式unit_ids准备剩余空scene目标（不要把已完成场纳入）。read_script_writing_targets核对清单，按该清单逐场read_script_unit_context和submit_script_unit完成余下场，每场用其当前context指纹及plan/body修订，不整段覆盖。后场可参考已提交前场正文，依赖由context显式给出。
5. 读取第1场当前正文，选一个action块，调用apply_script_block_changes只更新该块payload.text，在原叙述后增加“她握紧钥匙。”，target_block_ids只传该块ID。保留其他块和其他场正文。
6. read_screenplay/check_screenplay/export_screenplay(format=text)核对完成，保留依据变化提示，不能为消除提示自动刷新依据。简述场次ID和完成结果。
所有写入传真实revision与reason，计划和正文都应来自本任务真实输出，不把示例当模型结果。非法提交在同任务按诊断更正，不重新开任务、不自动重试请求。"""



def digest(value):
    return hashlib.sha256(value).hexdigest()


class TaskUsageObserver:
    """Accumulate unique completed provider requests across same-task SDK repairs."""
    def __init__(self, live_metadata, metadata_root):
        self.live_metadata, self.metadata_root = live_metadata, Path(metadata_root)
        self.observed = {}
        self.lock = threading.Lock()

    def total(self):
        with self.lock:
            return self._collect()

    def _collect(self):
        documents = [self.live_metadata() or {}]
        for path in self.metadata_root.glob("*/metadata-*.json"):
            try:
                documents.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                # Metadata is written by the worker; a partial write is read next tick.
                continue
        for document in documents:
            for request in document.get("model_requests", []):
                usage = request.get("usage")
                if not request.get("complete") or not isinstance(usage, dict):
                    continue
                call_ids = [call.get("id") for call in request.get("tool_calls", []) if call.get("id")]
                # Live snapshots and final archives enrich raw/fragments after
                # usage arrives. Provider tool-call IDs identify the same turn.
                raw = ("tool-calls:" + json.dumps(call_ids)) if call_ids else (
                    request.get("raw") or json.dumps(request, sort_keys=True, ensure_ascii=False))
                key = digest(raw.encode("utf-8"))
                self.observed[key] = max(self.observed.get(key, 0), usage.get("total_tokens", 0))
        return sum(self.observed.values())


class ExternalBudgetWatchdog:
    """QA-only observer, independent of Qt event-loop progress."""
    def __init__(self, usage, stop_task, seconds, token_budget):
        self.usage, self.stop_task = usage, stop_task
        self.deadline, self.token_budget = time.monotonic() + seconds, token_budget
        self.done = threading.Event()
        self.triggered = threading.Event()
        self.reason = None
        self.observed_tokens = 0
        self.observer_error = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def run(self):
        while not self.done.wait(0.1):
            try:
                self.observed_tokens = max(self.observed_tokens, self.usage())
                if self.observed_tokens > self.token_budget:
                    self.reason = "usage_budget"
                elif time.monotonic() >= self.deadline:
                    self.reason = "external_time_budget"
            except Exception as exc:
                self.observer_error = type(exc).__name__ + ": " + str(exc)
                self.reason = "observer_error"
            if self.reason:
                self.triggered.set()
                self.stop_task()
                return

    def close(self):
        self.done.set()
        self.thread.join(2)


def seed(ops):
    """Controlled synthetic input; never presented as a real extraction output."""
    sid = ops.import_source("合成钥匙交接", TEXT)
    first, second, third = "林岚走到旧车站门口。", "周川把铜钥匙交给林岚。", "林岚带着钥匙走向东仓，停下听见门内脚步。"
    draft = {"nodes": [
        {"type": "person", "name": "林岚", "quotes": [first]},
        {"type": "person", "name": "周川", "quotes": [second]},
        {"type": "location", "name": "旧车站门口", "quotes": ["旧车站门口"]},
        {"type": "location", "name": "东仓", "quotes": ["东仓"]},
        {"type": "item", "name": "铜钥匙", "quotes": ["铜钥匙"]},
        {"type": "scene", "name": "交接钥匙后接近东仓", "quotes": [TEXT]},
        {"type": "event", "name": "林岚来到旧车站门口", "scene": "@6", "participants": ["@1"], "quotes": [first]},
        {"type": "event", "name": "周川把铜钥匙交给林岚", "scene": "@6", "participants": ["@1", "@2"], "quotes": [second]},
        {"type": "event", "name": "林岚到东仓听见脚步", "scene": "@6", "participants": ["@1"], "quotes": [third]},
    ], "properties": [
        {"node": "@7", "property": "地点", "value": "@3", "value_type": "node", "scope": "world", "at_event": "@7", "quotes": ["旧车站门口"]},
        {"node": "@9", "property": "地点", "value": "@4", "value_type": "node", "scope": "world", "at_event": "@9", "quotes": ["东仓"]},
        {"node": "@5", "property": "持有者", "value": "@1", "value_type": "either", "scope": "world", "at_event": "@8", "quotes": [second]},
    ]}
    result = ops.submit_extraction(sid, draft)
    if result["status"] != "accepted":
        raise ValueError(str(result))
    events = [n["id"] for n in ops.graph_snapshot()["nodes"] if n["node_type"] == "event"]
    imported = ops.import_adaptation(events, expected_revision=0, reason="合成改编输入fixture")
    screenplay = ops.create_screenplay("雨夜交接", creative_brief={"style": "克制悬疑", "audience": "成年观众"}, reason="验收剧本fixture")["screenplay"]
    local = ops.import_screenplay_timeline(screenplay["id"], events, expected_timeline_revision=0,
            expected_adaptation_revision=imported["revision"], reason="独立剧本导入fixture")
    span = ops.create_adaptation_span(screenplay["id"], "钥匙交接与仓门悬念", selection_ids=events,
            expected_timeline_revision=local["revision"], reason="验收范围fixture")["span"]
    segment = ops.create_script_segment(screenplay["id"], span["id"], "交接与接近",
            screenplay["structure_revision"], "验收段落fixture", brief={"constraint": "两场，保留钥匙交接及仓门脚步的悬念"})
    return {"source_id": sid, "screenplay_id": screenplay["id"], "segment_id": segment["id"], "span_id": span["id"]}


def main(validate=None, required_tools=None, target_factory=None, use_writing_target=True,
         prompt_factory=None, fixture_resolver=None, ops_resolver=None, allow_adaptation_change=False,
         allow_extraction_change=False):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--token-budget", type=int, default=800000)
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from narrative.assistant import ASSISTANT_VERSION, tool_catalog
    from narrative.chat_store import ChatStore
    from narrative.harness import CONVERSATION_SYSTEM_PROMPT, effective_runtime_config, harness_env
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout

    def save(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {"status": "running", "started": datetime.now(UTC).isoformat(), "prompt_version": PROMPT_VERSION,
        "agent_version": ASSISTANT_VERSION, "task_budget": 1, "tasks_started": 0, "automatic_retry": False,
        "external_qa_seconds": args.seconds, "external_usage_budget_tokens": args.token_budget,
        "product_task_deadline": None, "user_projects_modified": False,
        "fixture_origin": FIXTURE_ORIGIN,
        "expected_adaptation_mutation": allow_adaptation_change,
        "expected_extraction_mutation": allow_extraction_change,
        "input_sha256": digest(TEXT.encode()), "task_sha256": digest(TASK.encode()),
        "system_prompt_sha256": digest(CONVERSATION_SYSTEM_PROMPT.encode()),
        "implementation_sha256": {str(p): digest(p.read_bytes()) for p in sorted({
            *Path("src/narrative").glob("screenplay*.py"),
            *Path("src/narrative").glob("tool*.py"),
            *Path("src/narrative").glob("*prompt*.py"),
            *Path("src/narrative").glob("conversation*.py"),
            Path("src/narrative/harness.py"),
            Path("src/narrative/project_style.py"), Path("src/narrative/graph_operations.py"),
            *Path("src/narrative/qml").glob("*.qml"),
            *(Path("src/narrative") / name for name in (
                "readable_ids.py", "tool_fingerprint_refs.py", "tool_execution_context.py", "adaptation_operations.py",
                "asset_operations.py", "node_material.py", "tool_api.py", "native_bridge.py", "assistant.py", "model_gateway.py", "adaptation_import.py",
                "adaptation_views.py", "state_anchors.py")),
            *(Path("src/narrative") / name for name in ("project_copy.py", "project_package.py", "project_catalog.py", "operations.py", "provider_store.py")),
            *(Path("src/narrative/qml") / name for name in ("ScreenplayPage.qml", "ScriptPlanEditor.qml", "BrowsePage.qml",
                "ScriptTimelineEditor.qml", "ScriptRangePicker.qml")),
            Path("src/narrative/qml/LauncherPage.qml"),
            Path("src/narrative/qml/TimelineImportColumns.qml"),
            Path(__file__), Path(sys.argv[0]),
        }) if p.is_file()}}
    for name, value in [("input.txt", TEXT), ("user-task.txt", TASK), ("system-prompt.txt", CONVERSATION_SYSTEM_PROMPT)]:
        (directory / name).write_text(value, encoding="utf-8")
    save("tool-catalog.json", tool_catalog())
    save("manifest.json", report)
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))
    ops = None
    fixture = None
    before_graph = before_adaptation = None
    audit_root = None
    began = None
    watchdog = None

    def wait(check, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return True
            time.sleep(0.01)
        return False

    try:
        ops = Operations.create_graph_project(directory / "剧本闭环验收.nproj")
        fixture = seed(ops)
        if ops_resolver is not None:
            ops = ops_resolver(ops, fixture)
        save("fixture.json", fixture)
        before_graph, before_adaptation = ops.graph_snapshot(), ops.adaptation_snapshot()
        save("fixture-source.json", before_graph)
        save("fixture-adaptation.json", before_adaptation)
        settings, _key, _workdir = harness_env(ops)
        report.update(provider=settings.provider, model=settings.model, reasoning_effort=settings.reasoning_effort,
                      configuration=effective_runtime_config(settings), fixture=fixture)
        save("manifest.json", report)
        bridge.openProject(str(layout(ops.project_info().path).entry))
        if not wait(lambda: any(k == "project" for k, _ in results) or errors, 20) or errors:
            raise RuntimeError("isolated screenplay project did not open")
        audit_root = layout(ops.project_info().path).extraction / "assistant" / "tasks"
        began = time.monotonic()
        report["tasks_started"] = 1
        task_message = prompt_factory(fixture) if prompt_factory else TASK
        (directory / "user-task.txt").write_text(task_message, encoding="utf-8")
        report["task_sha256"] = digest(task_message.encode())
        if use_writing_target:
            target = target_factory(fixture) if target_factory else {"kind": "segment", "ids": [fixture["segment_id"]], "screenplay_id": fixture["screenplay_id"], "include_existing": True}
            bridge.screenplayWritingTurn(task_message, target)
        else:
            bridge.assistantTurn(task_message, "")
        usage_observer = TaskUsageObserver(
            lambda: bridge._assistant_runner.usage_metadata() if bridge._assistant_runner else {}, audit_root)

        def observed_usage():
            return usage_observer.total()

        def stop_current_task():
            with bridge._assistant_lock:
                jobs = list(bridge._assistant_jobs.values())
            for job in jobs:
                if job.get("task_id"):
                    bridge.assistantStop(job["chat_id"], job["task_id"])

        watchdog = ExternalBudgetWatchdog(observed_usage, stop_current_task, args.seconds, args.token_budget).start()
        deadline = began + args.seconds
        while not any(k == "assistant" for k, _ in results) and not errors:
            app.processEvents()
            observed = observed_usage()
            if watchdog.triggered.is_set() or time.monotonic() >= deadline or observed > args.token_budget:
                report.update(observation_expired=time.monotonic() >= deadline, observed_usage_budget_exceeded=observed > args.token_budget)
                for job in list(bridge._assistant_jobs.values()):
                    if job.get("task_id"):
                        bridge.assistantStop(job["chat_id"], job["task_id"])
                wait(lambda: any(k == "assistant" for k, _ in results) or errors, 20)
                raise RuntimeError("external QA budget reached; no retry; completed scene commits retained")
            time.sleep(0.01)
        if errors:
            raise RuntimeError("real screenplay task returned an error; inspect saved evidence")
    except Exception as exc:
        report.update(status="not_passed", error_type=type(exc).__name__, error=str(exc))
    finally:
        if watchdog:
            watchdog.close()
            report["observer"] = {"trigger_reason": watchdog.reason, "max_observed_tokens": watchdog.observed_tokens, "error": watchdog.observer_error}
        try:
            result = next((p for k, p in results if k == "assistant"), None)
            audit_path = result.get("audit_directory") if isinstance(result, dict) else None
            if not audit_path and audit_root:
                candidates = sorted(audit_root.glob("*/task.json"))
                audit_path = str(candidates[-1].parent) if candidates else None
            metadata, audit, calls = [], {}, []
            if audit_path:
                audit_dir = Path(audit_path)
                metadata = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(audit_dir.glob("metadata-*.json"))]
                audit = json.loads((audit_dir / "task.json").read_text(encoding="utf-8"))
                calls = [c for m in metadata for request in m.get("model_requests", []) for c in request.get("tool_calls", [])]
                save("audit-files.json", [{"path": str(p), "sha256": digest(p.read_bytes())} for p in sorted(audit_dir.rglob("*")) if p.is_file()])
            report.update(audit_directory=audit_path, task_status=audit.get("status"), errors=errors,
                model_requests=sum(len(m.get("model_requests") or []) for m in metadata),
                usage=[m.get("usage") for m in metadata], finish_reasons=[m.get("finish_reason") for m in metadata],
                model_elapsed_seconds=time.monotonic() - began if began else None)
            save("model-metadata.json", metadata)
            save("tool-calls.json", calls)
            if ops:
                store = ChatStore(ops.project_info().path)
                chat = store.active(create=False)
                save("chat-visible-output.json", store.load(chat["id"]) if chat else {})
                with sqlite3.connect(ops.project.store.path) as db:
                    db.row_factory = sqlite3.Row
                    save("actions.json", [dict(r) for r in db.execute("SELECT * FROM actions ORDER BY rowid")])
                if fixture:
                    if fixture_resolver is not None:
                        fixture = fixture_resolver(ops, fixture)
                        save("fixture-resolved.json", fixture)
                        report["resolved_fixture"] = fixture
                    screenplay = ops.read_screenplay(fixture["screenplay_id"])
                    check = ops.check_screenplay(fixture["screenplay_id"])
                    save("screenplay.json", screenplay)
                    save("static-check.json", check)
                    exported = ops.export_screenplay(fixture["screenplay_id"], "text")
                    (directory / "screenplay.txt").write_text(exported["text"], encoding="utf-8")
                    units = [unit for segment in screenplay["segments"] for unit in segment["units"]]
                    called = {str(c.get("name") or (c.get("function") or {}).get("name") or "") for c in calls if isinstance(c, dict)}
                    required = ["read_scene_table_context", "submit_scene_table", "apply_scene_table_changes", "adopt_scene_table", "read_script_unit_context", "submit_script_unit", "prepare_script_writing_targets", "read_script_writing_targets", "apply_script_block_changes", "check_screenplay", "export_screenplay"]
                    required = required_tools if required_tools is not None else required
                    presence = {name: any(value == name or value.endswith("." + name) or value.endswith("/" + name) for value in called) for name in required}
                    checks = {"task_completed": audit.get("status") == "completed", "scene_count_2_to_3": 2 <= len(units) <= 3,
                        "all_scene_bodies_present": bool(units) and all(unit["body_presence"] == "present" for unit in units),
                        "plan_edit_saved": bool(units) and "隐秘" in units[0]["plan"].get("conflict", ""),
                        "local_block_edit_saved": any("她握紧钥匙" in block["payload"].get("text", "") for unit in units for block in unit["blocks"]),
                        "all_required_tools_called": all(presence.values()), "screenplay_static_ok": check["ok"],
                        "extraction_unchanged": ops.graph_snapshot() == before_graph,
                        "adaptation_unchanged": ops.adaptation_snapshot() == before_adaptation, "no_errors": not errors}
                    if validate is not None:
                        preserved = ("task_completed", "no_errors")
                        if not allow_extraction_change:
                            preserved += ("extraction_unchanged",)
                        if not allow_adaptation_change:
                            preserved += ("adaptation_unchanged",)
                        checks = {key: checks[key] for key in preserved} | validate(ops, fixture, screenplay, calls, report)
                        checks["all_required_tools_called"] = all(presence.values())
                    report.update(checks=checks, required_tool_presence=presence, unit_ids=[u["id"] for u in units])
                    if report["status"] == "running":
                        report["status"] = "passed" if all(checks.values()) else "not_passed"
        except Exception as exc:
            report.update(status="not_passed", evidence_error_type=type(exc).__name__, evidence_error=str(exc))
        actual_tokens = sum(u.get("total_tokens", 0) for group in report.get("usage", [])
                            if isinstance(group, list) for u in group if isinstance(u, dict))
        report["actual_total_tokens"] = actual_tokens
        report["usage_budget_compliant"] = actual_tokens <= args.token_budget
        if report["status"] == "passed" and not report["usage_budget_compliant"]:
            report["functional_status"] = "passed"
            report["status"] = "functional_passed_budget_overrun"
        bridge.close()
        app.processEvents()
        report["finished"] = datetime.now(UTC).isoformat()
        save("events.json", results)
        save("report.json", report)
        save("manifest.json", report)
    print(json.dumps({"directory": str(directory), "status": report["status"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
