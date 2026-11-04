"""One real directed scene-table task with full project reads and tool catalog."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

BASE_SEED = qa.seed
TEXT = qa.TEXT
TASK = """这是开放资料读取、限定写对象的独立验收。你处于scene_table定向任务，但应能查阅工程全部资料和完整工具目录。请实际完成：
1. read_source读取给定原文；node_material读取给定人物资料卡；list_nodes查询提取结构；adaptation_snapshot读取改编；read_screenplay读取既有场表与正文；read_script_segment读取非目标对照段落（只读）。再read_scene_table_context获取目标当前依据。
2. 这些读取用于核对资料，原作事实与改编冲突以当前改编为准，旧稿是待编辑内容而非新增事实。本次只编辑给定目标段落的第一场plan.conflict，准确改为“资料核对完成，交接必须保持隐秘”。使用apply_scene_table_changes一次update，完整保留其余plan字段、稳定场次ID和正文。不得修改非目标段落，不写原作、资料卡、改编或正文；不要刷新依据。
3. read_script_segment核对目标与非目标结果，回复资料卡中的核对口令及目标场次ID。不需要试写越权对象，也不要创建新内容。非法工具参数在同任务按诊断更正，不另开任务或重试服务。"""


def seed(ops):
    fixture = BASE_SEED(ops)
    person = next(node for node in ops.graph_snapshot()["nodes"] if node["name"] == "林岚")
    ops.material_add_card(person["id"], "人物核对资料", detail="核对口令：青铜雨燕。林岚在交接时保持克制。")
    context = ops.read_scene_table_context(fixture["segment_id"])
    table = ops.submit_scene_table(fixture["segment_id"], [{"plan": {"title": "既有交接场", "summary": "林岚接过钥匙后接近东仓。", "conflict": "旧冲突待编辑"}}],
        context["expected_outline_revision"], context["span_fingerprint"], context["context_fingerprint"], reason="隔离验收既有场表")
    unit = table["units"][0]
    context = ops.read_script_unit_context(unit["id"])
    ops.submit_script_unit(unit["id"], [{"type": "action", "payload": {"text": "保留正文哨兵：她握住铜钥匙。"}}],
        context["expected_plan_revision"], context["expected_body_revision"], context["span_fingerprint"], context["context_fingerprint"], context["plan_fingerprint"], reason="隔离验收已有正文")
    document = ops.read_screenplay(fixture["screenplay_id"])
    other = ops.create_script_segment(fixture["screenplay_id"], fixture["span_id"], "只读对照段", document["screenplay"]["structure_revision"], reason="隔离验收非目标对照")
    fixture.update(person_id=person["id"], unit_id=unit["id"], other_segment_id=other["id"],
        before_target=ops.read_script_segment(fixture["segment_id"]), before_other=ops.read_script_segment(other["id"]),
        before_material=ops.material_view(person["id"]), before_properties=ops.property_records(),
        before_timeline=ops.read_screenplay_timeline(fixture["screenplay_id"]))
    return fixture


def prompt(fixture):
    return TASK + "\n真实目标与只读资料IDs：" + json.dumps({key: fixture[key] for key in ("source_id", "screenplay_id", "segment_id", "person_id", "unit_id", "other_segment_id")}, ensure_ascii=False)


def validate(ops, fixture, screenplay, calls, report):
    from narrative.assistant import tool_catalog
    from narrative.tool_api import TOOLS
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    steps = audit["steps"]
    reads = {name: any(step["tool"] == name and step.get("ok") for step in steps) for name in (
        "read_source", "node_material", "list_nodes", "adaptation_snapshot", "read_screenplay", "read_script_segment", "read_scene_table_context")}
    current = ops.read_script_segment(fixture["segment_id"])
    before = fixture["before_target"]["units"][0]
    after = current["units"][0]
    attempts = [step for step in steps if step["tool"] in TOOLS and TOOLS[step["tool"]].writes]
    writes = [step for step in attempts if step.get("ok")]
    catalog = audit.get("task_tool_names", [])
    report["open_workflow_evidence"] = {"successful_reads": reads, "actual_task_tool_names": catalog, "writes": writes, "attempted_writes": attempts, "target_after": current,
        "other_after": ops.read_script_segment(fixture["other_segment_id"])}
    return {"full_business_catalog": set(catalog) == {tool["name"] for tool in tool_catalog()} and len(catalog) > 3,
        "all_material_reads_succeeded": all(reads.values()),
        "non_target_read_succeeded": any(step["tool"] == "read_script_segment" and step.get("ok") and step["args"].get("segment_id") == fixture["other_segment_id"] for step in steps),
        "one_target_scene_table_write": len(writes) == 1 and writes[0]["tool"] == "apply_scene_table_changes" and writes[0]["ok"] and writes[0]["args"]["segment_id"] == fixture["segment_id"],
        "requested_plan_saved": after["plan"] == before["plan"] | {"conflict": "资料核对完成，交接必须保持隐秘"},
        "stable_unit_and_body_preserved": len(current["units"]) == 1 and after["id"] == before["id"] and after["blocks"] == before["blocks"],
        "non_target_unchanged": ops.read_script_segment(fixture["other_segment_id"]) == fixture["before_other"],
        "materials_unchanged": ops.material_view(fixture["person_id"]) == fixture["before_material"],
        "properties_unchanged": ops.property_records() == fixture["before_properties"],
        "local_timeline_unchanged": ops.read_screenplay_timeline(fixture["screenplay_id"]) == fixture["before_timeline"]}


def main():
    qa.seed, qa.TEXT, qa.TASK = seed, TEXT, TASK
    qa.PROMPT_VERSION = "screenplay-open-directed-workflow/1"
    qa.FIXTURE_ORIGIN = "Operations source/adaptation/local timeline with existing plan/body/material card and a non-target segment; one real directed scene-table edit"
    return qa.main(validate=validate, required_tools=["read_source", "node_material", "list_nodes", "adaptation_snapshot", "read_screenplay", "read_scene_table_context", "apply_scene_table_changes"],
        target_factory=lambda f: {"kind": "scene_table", "ids": [f["segment_id"]], "screenplay_id": f["screenplay_id"]}, prompt_factory=prompt)


if __name__ == "__main__":
    raise SystemExit(main())
