"""Single minimal real body submission using a screenplay-owned imported timeline."""
from __future__ import annotations

import accept_screenplay as qa

PROMPT = """请在本任务授权的单个unit目标内完成一个最小真实写作验收。先调用read_script_unit_context，核对其timeline_id属于当前screenplay的独立副本；只根据本场plan和导入的本地范围，为本场写一个简短中文action正文块，实际调用submit_script_unit入库（blocks恰好一个type=action,payload={text:中文动作描述}，必须传刚读到的修订和三个指纹、dependency_refs及reason；任务与依据关联由程序内部完成，不传task_id/manifest_ref）。随后调用read_script_unit_context核对正文已在相同unit和timeline保存，最后一句话汇报。不要拆新场、改其他正文、修改源改编或导入新节点，不调用其他写工具，不开启新任务或服务重试。"""

_original_seed = qa.seed


def seed(ops):
    fixture = _original_seed(ops)
    context = ops.read_scene_table_context(fixture["segment_id"])
    table = ops.submit_scene_table(fixture["segment_id"], [{"plan": {
        "title": "仓门前", "summary": "林岚带着钥匙到东仓门口，听见屋内脚步，停住。",
        "location_text": "东仓门前"}}], context["expected_outline_revision"],
        context["span_fingerprint"], context["context_fingerprint"], dependency_refs=context["dependency_refs"], reason="合成场表fixture，不作为模型输出")
    fixture["unit_id"] = table["units"][0]["id"]
    fixture["timeline_id"] = ops.read_screenplay_timeline(fixture["screenplay_id"])["timeline_id"]
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    segment = screenplay["segments"][0]
    unit = segment["units"][0]
    context = ops.read_script_unit_context(fixture["unit_id"])
    names = {str(c.get("name") or (c.get("function") or {}).get("name") or "") for c in calls if isinstance(c, dict)}
    return {"task_completed": report["task_status"] == "completed", "no_errors": not report["errors"],
            "same_unit": unit["id"] == fixture["unit_id"], "one_real_action_block": len(unit["blocks"]) == 1 and unit["blocks"][0]["type"] == "action",
            "own_timeline": context["timeline_id"] == fixture["timeline_id"], "basis_current": unit["basis_state"] == "current",
            "actual_body_submit_tool": any(n == "submit_script_unit" or n.endswith(".submit_script_unit") or n.endswith("/submit_script_unit") for n in names),
            "screenplay_static_ok": ops.check_screenplay(fixture["screenplay_id"])["ok"]}


if __name__ == "__main__":
    qa.TASK = PROMPT
    qa.PROMPT_VERSION = "screenplay-import-body-acceptance/1"
    qa.FIXTURE_ORIGIN = "Source/adaptation/import/scene table prepared deterministically through Operations; only the single action body is real model output"
    qa.seed = seed
    raise SystemExit(qa.main(validate=validate, required_tools=["read_script_unit_context", "submit_script_unit"],
        target_factory=lambda fixture: {"kind": "unit", "ids": [fixture["unit_id"]], "screenplay_id": fixture["screenplay_id"]}))
