"""One bounded real scene-table task over continuous beats and a hidden ending."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

TEXT = "林岚走到石台边。周川从雾中现身。林岚出言质问周川。周川挥掌，林岚侧身避开。林岚反击，周川退回石台边。周川在石台边掀起烟雾逃走。林岚查勘七个星球后回遥远故乡求援。"
QUOTES = TEXT.split("。")[:-1]
QUOTES = [value + "。" for value in QUOTES]
NAMES = ["林岚走近石台", "周川现身", "林岚质问", "周川进攻", "林岚反击", "周川逃走", "查勘七个星球后回遥远故乡求援"]
HIDDEN_PHRASES = ["七个星球", "遥远故乡", "慢镜哨兵", "特写哨兵"]
TASK = """请依据本段当前有效时间线重新编写场级场表并实际提交。先read_scene_table_context，遵守其中程序提供的拆场规则与有效事件来源；只根据有效范围的剧情决定场数，不把事件或招式逐条当作场，不写分镜、镜头或特效方案。旧场表只是待改输出，不是剧情依据。保留所有已有稳定ID，正文不变。用submit_scene_table提交完整场表，再read_scene_table_context和check_screenplay核对。不要写正文、读原文或全量时间线，不开启新任务，不重新请求服务。新建编号和单场来源由程序分配；依据编号和修订原样传回，不计算hash。"""


def seed(ops):
    source_id = ops.import_source("连续石台对峙", TEXT)
    nodes = [{"type": "person", "name": "林岚", "quotes": [QUOTES[0]]},
             {"type": "person", "name": "周川", "quotes": [QUOTES[1]]},
             {"type": "location", "name": "石台边", "quotes": ["石台边"]},
             {"type": "scene", "name": "石台对峙", "quotes": [TEXT]}]
    nodes += [{"type": "event", "name": name, "scene": "石台对峙", "participants": ["林岚", "周川"] if index else ["林岚"], "quotes": [quote]}
              for index, (name, quote) in enumerate(zip(NAMES, QUOTES, strict=True))]
    properties = [{"node": NAMES[index], "property": "地点", "value": "石台边", "value_type": "node", "scope": "world",
        "at_event": NAMES[index], "quotes": [QUOTES[index]]} for index in (0, 5)]
    accepted = ops.submit_extraction(source_id, {"nodes": nodes, "properties": properties})
    assert accepted["status"] == "accepted", accepted
    adaptation = ops.sync_adaptation()
    ids = {row["name"]: row["id"] for row in adaptation["nodes"]}
    document = ops.create_screenplay("连续场界验收", reason="准备确定性测试剧本")
    screenplay = document["screenplay"]
    local = ops.import_screenplay_timeline(screenplay["id"], [ids["石台对峙"]], 0, adaptation["revision"], "导入测试时间线")
    ops.suppress_screenplay_timeline_node(screenplay["id"], ids[NAMES[-1]], expected_timeline_revision=local["revision"], reason="隐藏后续求援剧情")
    local = ops.read_screenplay_timeline(screenplay["id"])
    span = ops.create_adaptation_span(screenplay["id"], "连续对峙选区", start_node_id=ids[NAMES[0]], end_node_id=ids[NAMES[-1]],
        expected_timeline_revision=local["revision"], reason="确定性有效范围")["span"]
    segment = ops.create_script_segment(screenplay["id"], span["id"], "开场", screenplay["structure_revision"], "确定性段落")
    old = ops.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {"plan": {
        "summary": "林岚查勘七个星球后回遥远故乡求援。", "production_notes": "慢镜哨兵，特写哨兵"}}}],
        segment["outline_revision"], reason="确定性模拟被旧稿污染的场表")
    return {"source_id": source_id, "screenplay_id": screenplay["id"], "span_id": span["id"], "segment_id": segment["id"],
        "old_unit_id": old["units"][0]["id"], "active_events": [ids[name] for name in NAMES[:-1]], "hidden_event": ids[NAMES[-1]]}


def validate(ops, fixture, screenplay, calls, report):
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    active = [unit for unit in screenplay["segments"][0]["units"] if unit["enabled"]]
    steps = audit.get("steps", [])
    successful = [step for step in steps if step["tool"] == "submit_scene_table" and step.get("ok")]
    visible = json.dumps([step.get("result") for step in steps if step.get("ok")], ensure_ascii=False)
    plans = json.dumps([row["plan"] for row in active], ensure_ascii=False)
    report["scene_partition_evidence"] = {"active_units": active, "successful_submissions": successful,
        "rejected_steps": [step for step in steps if not step.get("ok")], "task_tool_names": audit.get("task_tool_names")}
    return {"continuous_beats_one_scene": len(active) == 1,
        "old_identity_reused": len(active) == 1 and active[0]["id"] == fixture["old_unit_id"],
        "effective_sources_bound": len(active) == 1 and active[0]["plan"]["source_event_ids"] == fixture["active_events"],
        "hidden_source_excluded": all(fixture["hidden_event"] not in row["plan"]["source_event_ids"] for row in active),
        "old_plot_not_in_any_tool_result": not any(value in visible for value in HIDDEN_PHRASES),
        "old_plot_not_regenerated": not any(value in plans for value in HIDDEN_PHRASES),
        "no_production_plan": all(not row["plan"]["production_notes"] for row in active),
        "one_actual_scene_commit": len(successful) == 1,
        "only_scene_submit_writes": all(step["tool"] in {"read_scene_table_context", "submit_scene_table", "check_screenplay"} for step in steps),
        "scoped_catalog": set(audit.get("task_tool_names", [])) == {"read_scene_table_context", "submit_scene_table", "check_screenplay"},
        "no_body_generated": all(not row["blocks"] for row in screenplay["segments"][0]["units"]),
        "scene_static_ok": ops.check_screenplay(fixture["screenplay_id"])["ok"]}


def main():
    qa.seed = seed
    qa.TEXT = TEXT
    qa.TASK = TASK
    qa.PROMPT_VERSION = "scene-standards-effective-input/2"
    qa.FIXTURE_ORIGIN = "Deterministic Operations extraction/adaptation/script and deliberately contaminated old scene plan; only replacement scene table is real model output"
    return qa.main(validate=validate, required_tools=["read_scene_table_context", "submit_scene_table", "check_screenplay"],
        target_factory=lambda f: {"kind": "scene_table", "ids": [f["segment_id"]], "screenplay_id": f["screenplay_id"]})


if __name__ == "__main__":
    raise SystemExit(main())
