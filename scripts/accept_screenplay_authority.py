"""One real rewrite honors current local plot and style over source/old draft."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

FIRST = "陈宁在办公室把预算表交给方岳。"
REMOVED = "方岳随后在大厅播放辞职录音，宣布离职。"
TEXT = FIRST + REMOVED
STYLE = "现代办公室场景，克制的冷幽默。用桌面物件、空白签名处和停顿形成视觉表达；对白短而有潜台词，让陈宁的催促与方岳的拖延通过行为呈现。无旁白、无直接内心独白，不复述小说解说；围绕17:30前交回预算表这一约定推进。"
TASK = """请重写给定目标场次正文。先read_source读取这份短原文作为参考，再read_script_unit_context读取当前剧本范围、场表和项目风格。以本剧本当前独立事件线的取舍及本场仍一致的内容为准；旧场表字段或旧稿若与当前事件线冲突，应指出而不把它当作恢复情节的授权。只重写正文，不修改场表、事件线、风格或源数据。
请按项目风格写约6至10个正文块，以具体可演动作与有潜台词的对白形成一段完整场景，双方都有对白，不把小说叙述拆成动作块。可在既定剧情内设计人物行为、停顿和场景细节。使用submit_script_unit一次原子替换本场旧正文，使用刚读取的真实修订/依据，不能手填程序内部任务档案。最后简述当前依据和实际完成情况，不另开任务或重试服务。"""


def seed(ops):
    source = ops.import_source("预算交接原作", TEXT)
    result = ops.submit_extraction(source, {"nodes": [
        {"type": "person", "name": "陈宁", "quotes": ["陈宁"]}, {"type": "person", "name": "方岳", "quotes": ["方岳"]},
        {"type": "location", "name": "办公室", "quotes": ["办公室"]}, {"type": "location", "name": "大厅", "quotes": ["大厅"]},
        {"type": "scene", "name": "办公室工作", "quotes": [TEXT]},
        {"type": "event", "name": "交接预算表", "scene": "办公室工作", "participants": ["陈宁", "方岳"], "quotes": [FIRST]},
        {"type": "event", "name": "播放录音宣布离职", "scene": "办公室工作", "participants": ["方岳"], "quotes": [REMOVED]}],
        "properties": [{"node": event, "property": "地点", "value": place, "value_type": "node", "scope": "world", "at_event": event, "quotes": [quote]} for event, place, quote in (("交接预算表", "办公室", FIRST), ("播放录音宣布离职", "大厅", REMOVED))]})
    assert result["status"] == "accepted", result
    ids = {node["name"]: node["id"] for node in ops.graph_snapshot()["nodes"]}
    adaptation = ops.import_adaptation([ids["办公室工作"]], reason="隔离原作改编")
    script = ops.create_screenplay("预算表", creative_brief={"tone": "用可见行为展现办公室关系"}, reason="隔离目标剧本")["screenplay"]
    local = ops.import_screenplay_timeline(script["id"], [ids["办公室工作"]], 0, adaptation["revision"], "导入后作独立删改")
    span = ops.create_adaptation_span(script["id"], "仅预算交接", selection_ids=[ids["交接预算表"]], expected_timeline_revision=local["revision"], reason="当前创作范围")["span"]
    segment = ops.create_script_segment(script["id"], span["id"], "下班前的交接", script["structure_revision"], reason="隔离目标段")
    context = ops.read_scene_table_context(segment["id"])
    table = ops.submit_scene_table(segment["id"], [{"plan": {"title": "空白签名处", "interior_exterior": "INT", "location_id": ids["办公室"], "location_text": "办公室", "time_label": "傍晚", "cast_ids": [ids["陈宁"], ids["方岳"]], "summary": "陈宁将预算表交给方岳，两人确认17:30前交回。", "goal": "陈宁确认方岳接手预算表", "conflict": "陈宁催促，方岳想拖延", "outcome": "方岳接手预算表，随后在大厅播放辞职录音并宣布离职。", "source_event_ids": [ids["交接预算表"]]}}], context["expected_outline_revision"], context["span_fingerprint"], context["context_fingerprint"], reason="故意保留旧结果字段，测试重写取舍")
    unit = table["units"][0]
    context = ops.read_script_unit_context(unit["id"])
    ops.submit_script_unit(unit["id"], [{"type": "action", "payload": {"text": "方岳走进大厅，将辞职录音接到广播设备。"}}, {"type": "dialogue", "payload": {"speaker_id": ids["方岳"], "speaker_label": "方岳", "delivery": "on_screen", "text": "今天我就离职。"}}], context["expected_plan_revision"], context["expected_body_revision"], context["span_fingerprint"], context["context_fingerprint"], context["plan_fingerprint"], reason="故意保留待重写旧稿")
    ops.delete_screenplay_timeline_node(script["id"], ids["播放录音宣布离职"], ops.read_screenplay_timeline(script["id"])["revision"], "当前剧本明确删去原作独立情节")
    style = ops.save_project_style(STYLE, ops.load_project_style()["revision"], "设置本次项目艺术风格")
    return {"source_id": source, "screenplay_id": script["id"], "segment_id": segment["id"], "unit_id": unit["id"], "ids": ids,
        "project_style": style, "before_unit": ops.read_script_segment(segment["id"])["units"][0], "before_timeline": ops.read_screenplay_timeline(script["id"]), "properties_before": ops.property_records()}


def validate(ops, fixture, screenplay, calls, report):
    from narrative.tool_api import TOOLS
    unit = ops.read_script_segment(fixture["segment_id"])["units"][0]
    blocks = unit["blocks"]
    text = "\n".join(block["payload"].get("text", "") for block in blocks)
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    steps = audit["steps"]
    attempts = [step for step in steps if step["tool"] in TOOLS and TOOLS[step["tool"]].writes]
    writes = [step for step in attempts if step.get("ok")]
    contexts = [step["result"] for step in steps if step["tool"] == "read_script_unit_context" and step.get("ok")]
    report["authority_evidence"] = {"body_text": text, "unit_after": unit, "contexts_read": contexts, "project_style": ops.load_project_style(),
        "attempted_writes": attempts, "successful_writes": writes, "task_style_snapshot": audit.get("project_style"), "semantic_review_required": "Read the actual blocks for plot scope, performable visual action, character-specific subtext and office style; lexical screening alone is insufficient."}
    return {"source_reference_actually_read": any(step["tool"] == "read_source" and step.get("ok") for step in steps),
        "creation_scope_only_current_event": bool(contexts) and all(context["creation_scope"]["effective_event_ids"] == [fixture["ids"]["交接预算表"]] for context in contexts),
        "old_outcome_and_draft_exposed_as_trap": bool(contexts) and "辞职录音" in json.dumps(contexts[0]["plan_input"], ensure_ascii=False) and "辞职录音" in json.dumps(contexts[0]["unit"]["blocks"], ensure_ascii=False),
        "exactly_one_target_body_commit": len(writes) == 1 and writes[0]["tool"] == "submit_script_unit" and writes[0]["args"]["unit_id"] == fixture["unit_id"],
        "current_plan_preserved": unit["plan"] == fixture["before_unit"]["plan"],
        "current_timeline_preserved": ops.read_screenplay_timeline(fixture["screenplay_id"]) == fixture["before_timeline"],
        "style_unchanged_and_snapshotted": ops.load_project_style() == fixture["project_style"] and audit.get("project_style") == fixture["project_style"] and audit.get("writing_task", {}).get("project_style") == fixture["project_style"],
        "context_style_matches": bool(contexts) and all(context["project_style"] == fixture["project_style"] for context in contexts),
        "literal_removed_plot_not_reintroduced": not any(term in text for term in ("辞职", "离职", "录音", "广播设备")),
        "script_action_and_two_speakers": any(block["type"] == "action" for block in blocks) and {block["payload"].get("speaker_id") for block in blocks if block["type"] == "dialogue"} == {fixture["ids"]["陈宁"], fixture["ids"]["方岳"]},
        "source_properties_unchanged": ops.property_records() == fixture["properties_before"]}


def main():
    qa.seed, qa.TEXT, qa.TASK = seed, TEXT, TASK
    qa.PROMPT_VERSION = "current-script-authority-visual-style/1"
    qa.FIXTURE_ORIGIN = "Operations office source has two events; local script deletes the second, while old plan.outcome and old body intentionally retain it; project style directs visible performable expression"
    return qa.main(validate=validate, required_tools=["read_source", "read_script_unit_context", "submit_script_unit"],
        target_factory=lambda f: {"kind": "unit", "ids": [f["unit_id"]], "screenplay_id": f["screenplay_id"], "include_existing": True},
        prompt_factory=lambda f: TASK + "\nsource_id=" + f["source_id"] + "；unit_id=" + f["unit_id"])


if __name__ == "__main__":
    raise SystemExit(main())
