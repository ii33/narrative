"""One isolated real-model closure for readable IDs and internal fingerprints."""
from __future__ import annotations

import json
import re
from pathlib import Path

import accept_screenplay as qa

_BASE_SEED = qa.seed
TASK = """这是独立合成工程的最小真实验收。请只完成指定场次的写作：
1. 调用node_material和get_asset只读核对指定人物资料卡、材质的可读编号，不修改资料或资产。
2. 调用read_script_unit_context读取指定场次，按本场plan与本地时间线写一个很短的中文action正文块，通过submit_script_unit实际入库。新块不要自编ID；预期修订、dependency_refs与三个fingerprint字段使用工具返回的值，依据编号由程序解析，不计算或猜测哈希。reason说明验收。
3. 再读本场context确认正文和稳定业务编号，最后简述资料卡、材质、场次、正文块编号。
只允许submit_script_unit这一类写操作，不拆场、不改其他对象，不修改原文、提取、改编、资料或资产。任务与生成依据关联由程序内部完成，不传task_id/manifest_ref。若工具参数格式有误可在同一任务按诊断修正，不重启任务或服务重试。
"""


def seed(ops):
    fixture = _BASE_SEED(ops)
    context = ops.read_scene_table_context(fixture["segment_id"])
    table = ops.submit_scene_table(fixture["segment_id"], [{"plan": {
        "title": "仓门前", "summary": "林岚握着钥匙停在东仓门口，听见里面脚步。"}}],
        context["expected_outline_revision"], context["span_fingerprint"], context["context_fingerprint"], reason="准备隔离场表")
    fixture["unit_id"] = table["units"][0]["id"]
    fixture["person_id"] = next(n["id"] for n in ops.graph_snapshot()["nodes"] if n["node_type"] == "person")
    fixture["card_id"] = ops.material_add_card(fixture["person_id"], "雨夜形象资料", "深色外套，衣摆沾雨")["id"]
    fixture["asset_id"] = ops.save_asset({"category": "material", "title": "湿木纹", "detail": "雨水浸润的旧木板"})["id"]
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    from narrative.screenplay_writing_models import BlockDraft
    from narrative.tool_api import TOOLS
    unit = screenplay["segments"][0]["units"][0]
    cards = ops.material_view(fixture["person_id"])["cards"]
    assets = ops.list_assets("material")["assets"]
    parsed = []
    for call in calls:
        name = str(call.get("name") or (call.get("function") or {}).get("name") or "").rsplit("/", 1)[-1].rsplit(".", 1)[-1]
        args = call.get("arguments", (call.get("function") or {}).get("arguments", {}))
        try:
            args = json.loads(args) if isinstance(args, str) else args
        except json.JSONDecodeError:
            continue  # Original malformed output remains in the audit, never executed.
        parsed.append((name, args))
    attempts = [args for name, args in parsed if name == "submit_script_unit"]
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    submitted = [step["args"] for step in audit.get("steps", [])
                 if step.get("tool") == "submit_script_unit" and step.get("ok") is True]
    allowed = {"submit_script_unit"}
    writes = [(name, args) for name, args in parsed if name not in TOOLS or TOOLS[name].writes]
    raw = json.dumps(attempts, ensure_ascii=False)
    handles_ok = all(isinstance(args, dict) and all(re.fullmatch(prefix + r"-[1-9][0-9]*@[a-z2-7]{12}", str(args.get(key, "")))
        for key, prefix in (("span_fingerprint", "span-basis"), ("context_fingerprint", "context-basis"), ("plan_fingerprint", "plan-basis"))) for args in submitted)
    blocks = []
    if len(submitted) == 1:
        for draft in submitted[0].get("blocks", []):
            normalized = BlockDraft.model_validate(draft).model_dump(mode="json")
            normalized.pop("id")
            blocks.append(normalized)
    from narrative.screenplay_store import read_context
    basis = read_context(ops)[0]["script_writing_bases"].get(unit.get("body_basis_id"), {})
    report["readable_id_evidence"] = {"cards": cards, "assets": assets, "unit": unit,
        "raw_submit_attempts": attempts, "successful_submit_arguments": submitted, "write_calls": writes,
        "rejected_submit_attempts": [step for step in audit.get("steps", [])
            if step.get("tool") == "submit_script_unit" and step.get("ok") is False]}
    return {"one_real_action": len(unit["blocks"]) == 1 and unit["blocks"][0]["type"] == "action",
        "readable_scene_and_block": unit["id"].startswith("unit-") and unit["blocks"][0]["id"].startswith("block-"),
        "material_card_readable": len(cards) == 1 and "-card-" in cards[0]["id"],
        "asset_readable": len(assets) == 1 and assets[0]["id"].startswith("material-"),
        "only_authorized_writes": all(name in allowed for name, _args in writes),
        "write_targets_authorized": all(isinstance(args, dict) and name == "submit_script_unit" and args.get("unit_id") == fixture["unit_id"]
            for name, args in writes),
        "only_target_scene": len(screenplay["segments"]) == 1 and len(screenplay["segments"][0]["units"]) == 1 and unit["id"] == fixture["unit_id"],
        "exact_target_submit": len(submitted) == 1 and submitted[0].get("unit_id") == fixture["unit_id"],
        "successful_submit_is_original_model_call": bool(submitted) and all(args in attempts for args in submitted),
        "submitted_body_matches_saved": len(submitted) == 1 and blocks == [{"type": b["type"], "payload": b["payload"]} for b in unit["blocks"]],
        "submitted_short_basis_refs": bool(submitted) and handles_ok,
        "provenance_internal": len(submitted) == 1 and "task_id" not in submitted[0] and "manifest_ref" not in submitted[0]
            and bool(basis.get("task_id")) and bool(basis.get("manifest_ref"))
            and basis["task_id"] == Path(report["audit_directory"]).name
            and basis["manifest_ref"] == str(Path(report["audit_directory"]) / "writing-task.json"),
        "no_raw_hash_in_submit": not re.search(r"\b[0-9a-f]{64}\b", raw),
        "body_basis_current": unit["basis_state"] == "current", "screenplay_static_ok": ops.check_screenplay(fixture["screenplay_id"])["ok"]}


def main():
    qa.seed = seed
    qa.TASK = TASK
    qa.PROMPT_VERSION = "readable-tools-internal-fingerprint/1"
    qa.FIXTURE_ORIGIN = "Deterministic source, extraction, adaptation, copied screenplay timeline and scene table via Operations; material card and asset prepared deterministically; only action body is model-generated"
    return qa.main(validate=validate, required_tools=["node_material", "get_asset", "read_script_unit_context", "submit_script_unit"],
        target_factory=lambda f: {"kind": "unit", "ids": [f["unit_id"]], "screenplay_id": f["screenplay_id"]},
        prompt_factory=lambda f: TASK + "\n人物ID=" + f["person_id"] + "；本场unit_id=" + f["unit_id"] + "；材质asset_id=" + f["asset_id"])


if __name__ == "__main__":
    raise SystemExit(main())
