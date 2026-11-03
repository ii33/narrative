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
import time
from datetime import UTC, datetime
from pathlib import Path

PROMPT_VERSION = "adaptation-selected-import-acceptance/2"
TEXT = "甲来到松桥报亭。值班老人替甲指明去车站的方向。"
TASK = """这是本次验收的一个统一任务。请实际调用工具完成以下闭环，不只给建议：
1. 用read_source读取工程内完整原文，按工程默认精细粒度提取人物、地点、场景、事件及有依据的属性；通过submit_extraction自动入库。不提取动作，不创建未结束的主要故事。
2. 调用import_adaptation_candidates查看可导入时间点，从中选择一个event节点，通过import_adaptation(moment_ids=[该event的实际ID])仅导入选中时间点及其相关状态。不要调用sync_adaptation。随后调用adaptation_browse_view和adaptation_snapshot，确认导入的节点、状态及改编修订号。
3. 选择一个改编event节点，用edit_adaptation_node把name改为“改编问路”。保留其他内容，不修改原始提取节点，不重新提取来实现改名。
4. 调用save_asset新增材质，payload为category=material、title=报亭木纹、detail=旧报亭的木质表面、prompt=旧木纹，柔和日光、domain=adaptation；材质不关联节点或时间点。
5. 实际调用check_adaptation、list_assets(category=material)和adaptation_snapshot确认结果。简短汇报改名后的改编节点ID及材质ID；存疑保留，不修补原始模型输出，不重复开启任务。
"""


def digest(value):
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, choices=(240, 360), default=360)
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from narrative.assistant import ASSISTANT_VERSION
    from narrative.chat_store import ChatStore
    from narrative.extraction_draft import DRAFT_VERSION
    from narrative.harness import (
        CONVERSATION_SYSTEM_PROMPT,
        HarnessSettings,
        effective_runtime_config,
        harness_env,
    )
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout

    def save(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    (directory / "input.txt").write_text(TEXT, encoding="utf-8")
    (directory / "user-task.txt").write_text(TASK, encoding="utf-8")
    (directory / "system-prompt.txt").write_text(CONVERSATION_SYSTEM_PROMPT, encoding="utf-8")
    report = {
        "status": "running", "started": datetime.now(UTC).isoformat(),
        "prompt_version": PROMPT_VERSION, "agent_version": ASSISTANT_VERSION,
        "draft_version": DRAFT_VERSION, "input_characters": len(TEXT),
        "input_sha256": digest(TEXT.encode()), "task_sha256": digest(TASK.encode()),
        "system_prompt_sha256": digest(CONVERSATION_SYSTEM_PROMPT.encode()),
        "task_budget": 1, "tasks_started": 0, "automatic_retry": False,
        "external_qa_seconds": args.seconds, "product_task_deadline": None,
        "model_parameters_overridden": False, "user_projects_modified": False,
        "model_requests": 0, "usage": [], "finish_reasons": [],
    }
    save("manifest.json", report)
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))
    ops = None
    began = None
    audit_root = None
    previous_audits = set()

    def wait(check, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return True
            time.sleep(0.01)
        return False

    try:
        ops = Operations.create_graph_project(directory / "改编传输验收.nproj")
        original = ops.load_project_settings()
        ops.save_project_settings({"extraction_granularity": "fine", "profile": original["profile"]})
        assert ops.load_project_settings()["profile"] == original["profile"]
        sid = ops.import_source("合成报亭问路原文", TEXT)
        report.update(source_id=sid, extraction_granularity="fine", model_profile_unchanged=True,
                      project_entry=str(layout(ops.project_info().path).entry))
        # This preflight reads credentials but never serializes the key or key_env.
        settings = HarnessSettings.model_validate(ops.harness_settings())
        report.update(provider=settings.provider, model=settings.model,
                      reasoning_effort=settings.reasoning_effort,
                      network_idle_seconds=settings.timeout_seconds,
                      settings=effective_runtime_config(settings))
        save("manifest.json", report)
        settings, _key, _workdir = harness_env(ops)
        bridge.openProject(report["project_entry"])
        if not wait(lambda: any(k == "project" for k, _ in results) or errors, 20):
            raise RuntimeError("isolated project did not open")
        if errors:
            raise RuntimeError("isolated project initialization failed")
        audit_root = layout(ops.project_info().path).extraction / "assistant" / "tasks"
        previous_audits = set(audit_root.glob("*/task.json"))
        began = time.monotonic()
        report["tasks_started"] = 1
        bridge.assistantTurn(TASK, "")
        finished = wait(lambda: any(k == "assistant" for k, _ in results) or errors, args.seconds)
        report["observation_expired"] = not finished
        if not finished:
            for job in list(bridge._assistant_jobs.values()):
                if job.get("task_id"):
                    bridge.assistantStop(job["chat_id"], job["task_id"])
            wait(lambda: any(k == "assistant" for k, _ in results) or errors, 20)
        if not finished:
            raise RuntimeError("external QA observation budget expired; task stopped, not retried")
    except Exception as exc:
        report.update(status="not_passed", error_type=type(exc).__name__, error=str(exc))
    finally:
        if began is not None:
            report["model_elapsed_seconds"] = time.monotonic() - began
        # Collect available evidence even when credentials, service or task fail.
        try:
            result = next((p for k, p in results if k == "assistant"), None)
            audit_path = result.get("audit_directory") if isinstance(result, dict) else None
            if not audit_path and audit_root:
                available = sorted(set(audit_root.glob("*/task.json")) - previous_audits)
                audit_path = str(available[-1].parent) if available else None
            report["audit_directory"] = audit_path
            metadata, audit = [], {}
            if audit_path:
                audit_dir = Path(audit_path)
                metadata = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(audit_dir.glob("metadata-*.json"))]
                if (audit_dir / "task.json").is_file():
                    audit = json.loads((audit_dir / "task.json").read_text(encoding="utf-8"))
                report["raw_response_files"] = [str(p) for p in sorted(audit_dir.rglob("*"))
                                                if p.is_file() and ("raw" in p.name or "response" in p.name)]
                save("audit-files.json", [{"path": str(p), "sha256": digest(p.read_bytes())}
                                          for p in sorted(audit_dir.rglob("*")) if p.is_file()])
            save("model-metadata.json", metadata)
            report.update(task_status=audit.get("status"), errors=errors,
                          model_requests=sum(len(m.get("model_requests") or []) for m in metadata),
                          usage=[m.get("usage") for m in metadata],
                          finish_reasons=[m.get("finish_reason") for m in metadata])
            tool_calls = [c for m in metadata for req in m.get("model_requests", []) for c in req.get("tool_calls", [])]
            save("tool-calls.json", tool_calls)
            if ops:
                store = ChatStore(ops.project_info().path)
                chat = store.active(create=False)
                save("chat-visible-output.json", store.load(chat["id"]) if chat else {})
                graph, properties = ops.graph_snapshot(), ops.property_records()
                adaptation, assets = ops.adaptation_snapshot(), ops.list_assets("material")
                checks = {"extraction": ops.check_project(), "adaptation": ops.check_adaptation()}
                save("extraction-snapshot.json", graph)
                save("extraction-properties.json", properties)
                save("adaptation-snapshot.json", adaptation)
                save("material-assets.json", assets)
                save("static-checks.json", checks)
                # Read-only audit export; every model write itself uses Operations.
                with sqlite3.connect(ops.project.store.path) as db:
                    db.row_factory = sqlite3.Row
                    save("graph-actions.json", [dict(r) for r in db.execute("SELECT * FROM actions ORDER BY rowid")])
                asset_db = layout(ops.project_info().path).material / "assets" / "assets.sqlite3"
                if asset_db.is_file():
                    with sqlite3.connect(f"{asset_db.as_uri()}?mode=ro", uri=True) as db:
                        db.row_factory = sqlite3.Row
                        save("asset-actions.json", [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY revision")])
                originals = {n["id"]: n for n in graph["nodes"]}
                renamed = [n for n in adaptation["nodes"] if n["name"] == "改编问路" and n["node_type"] == "event"]
                isolated_rename = bool(renamed) and all(n["id"] in originals and originals[n["id"]]["name"] != n["name"] for n in renamed)
                material_ok = any(a["title"] == "报亭木纹" and a["prompt"] == "旧木纹，柔和日光" for a in assets["assets"])
                called_names = {str(c.get("name") or (c.get("function") or {}).get("name") or "")
                                for c in tool_calls if isinstance(c, dict)}
                required = ["read_source", "submit_extraction", "import_adaptation_candidates", "import_adaptation", "adaptation_browse_view", "adaptation_snapshot",
                            "edit_adaptation_node", "save_asset", "check_adaptation", "list_assets"]
                tool_presence = {name: any(called == name or called.endswith("." + name) or called.endswith("/" + name)
                                          for called in called_names) for name in required}
                report["checks"] = {
                    "task_completed": audit.get("status") == "completed", "source_nonempty": bool(graph["nodes"]),
                    "isolated_rename": isolated_rename, "material_prompt_saved": material_ok,
                    "extraction_static_ok": not any(i["severity"] == "error" for i in checks["extraction"]["issues"]),
                    "adaptation_static_ok": checks["adaptation"]["ok"],
                    "model_called_required_tools": all(tool_presence.values()),
                    "no_errors": not errors, "within_external_budget": not report.get("observation_expired", False),
                }
                report["required_tool_presence"] = tool_presence
                report["renamed_node_ids"] = [n["id"] for n in renamed]
                report["material_asset_ids"] = [a["id"] for a in assets["assets"]]
                if report["status"] == "running":
                    report["status"] = "passed" if all(report["checks"].values()) else "not_passed"
        except Exception as exc:
            report.update(status="not_passed", evidence_error_type=type(exc).__name__, evidence_error=str(exc))
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
