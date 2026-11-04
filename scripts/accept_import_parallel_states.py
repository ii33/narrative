"""One bounded task imports source-authored parallel relationship states."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

TEXT = "甲吩咐乙和丙办事。"
TASK = """这是导入状态的最小验收。当前只有一份确定性原提取和一个空剧本，尚无改编数据。请实际完成：
1. import_adaptation_candidates找到“交办”scene，import_adaptation选择该scene导入改编（包含下属事件、相关人物及两条下属状态）；不要改写任何提取/属性定义或新建内容。
2. import_screenplay_timeline_candidates用给定screenplay_id读取候选，再append_screenplay_timeline选择同一个scene导入空剧本，使用当前真实修订。
3. 不需要再读大快照或写正文。两条源状态应忠实保留，不能为消除诊断删值、截断或将one改many；简述导入结果。仅这两次导入写入，不另开任务、不重试服务。"""


def seed(ops):
    source_id = ops.import_source("并列下属状态验收", TEXT)
    result = ops.submit_extraction(source_id, {"nodes": [
        *[{"type": "person", "name": name, "quotes": [name]} for name in ("甲", "乙", "丙")],
        {"type": "scene", "name": "交办", "quotes": [TEXT]},
        {"type": "event", "name": "交办任务", "scene": "交办", "participants": ["甲", "乙", "丙"], "quotes": [TEXT]}],
        "properties": [{"node": "甲", "property": "下属", "value": name, "value_type": "node", "scope": "world", "at_event": "交办任务", "quotes": [TEXT]} for name in ("乙", "丙")]})
    assert result["status"] == "accepted", result
    script = ops.create_screenplay("并列状态导入验收", reason="隔离验收空剧本") ["screenplay"]
    return {"source_id": source_id, "screenplay_id": script["id"],
        "ids": {row["name"]: row["id"] for row in ops.graph_snapshot()["nodes"]},
        "properties_before": ops.property_records()}


def validate(ops, fixture, screenplay, calls, report):
    from narrative.tool_api import TOOLS
    source = fixture["properties_before"]
    adaptation = ops.adaptation_snapshot()
    local = ops.read_screenplay_timeline(fixture["screenplay_id"])
    source_values = [row for row in source if row.get("kind") == "property_value"]
    expected = {(row["id"], row["node_id"], row["property_id"], row["value"], row["event_id"]) for row in source_values}
    def values(snapshot):
        return {(row["id"], row["node_id"], row["property_id"], row["value"], row["event_id"]) for row in snapshot["properties"] if row.get("kind") == "property_value"}
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    attempts = [step for step in audit["steps"] if step["tool"] in TOOLS and TOOLS[step["tool"]].writes]
    writes = [step for step in attempts if step.get("ok")]
    report["parallel_state_evidence"] = {"adaptation": adaptation, "local_timeline": local,
        "attempted_writes": attempts, "successful_writes": writes,
        "adaptation_static": ops.check_adaptation(), "script_static": ops.check_screenplay(fixture["screenplay_id"])}
    return {"source_parallel_one_fixture": len(expected) == 2 and next(row for row in source if row.get("kind") == "property_definition")["cardinality"] == "one",
        "both_values_in_adaptation": values(adaptation) == expected,
        "both_values_in_script": values(local) == expected,
        "source_properties_unchanged": ops.property_records() == source,
        "definition_not_rewritten": all(next(row for row in s["properties"] if row.get("kind") == "property_definition")["cardinality"] == "one" for s in (adaptation, local)),
        "open_intervals_preserved": all(not row.get("end_event_id") for s in (adaptation, local) for row in s["properties"] if row.get("kind") == "property_value"),
        "source_conflict_reported": any(row.get("code") == "source_interval_conflict" and row.get("severity") == "warning" for row in local.get("import_summary", {}).get("diagnostics", [])),
        "both_layers_static_ok": ops.check_adaptation()["ok"] and ops.check_screenplay(fixture["screenplay_id"])["ok"],
        "only_two_authorized_imports": [step["tool"] for step in writes] == ["import_adaptation", "append_screenplay_timeline"],
        "same_scene_selected": all(step["args"].get("moment_ids") == [fixture["ids"]["交办"]] for step in writes)}


def main():
    qa.seed, qa.TEXT, qa.TASK = seed, TEXT, TASK
    qa.PROMPT_VERSION = "parallel-source-state-import/1"
    qa.FIXTURE_ORIGIN = "Operations extraction stores two same-anchor node values under source one definition; real model imports existing scene into adaptation then empty screenplay"
    return qa.main(validate=validate, required_tools=["import_adaptation_candidates", "import_adaptation", "import_screenplay_timeline_candidates", "append_screenplay_timeline"],
        prompt_factory=lambda fixture: TASK + "\n目标screenplay_id=" + fixture["screenplay_id"], use_writing_target=False, allow_adaptation_change=True)


if __name__ == "__main__":
    raise SystemExit(main())
