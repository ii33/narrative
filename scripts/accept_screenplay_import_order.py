"""One bounded real task imports rearranged adaptation without source mutation."""
from __future__ import annotations

import json
from pathlib import Path

import accept_adaptation_hierarchy as hierarchy
import accept_screenplay as qa

TEXT = hierarchy.TEXT
TASK = """这是剧本时间线顺序的独立验收。请实际用给定screenplay_id读取唯一空剧本“验收资料容器”，调用import_screenplay_timeline_candidates读取候选；顺序应沿用改编sort_id，不能按原文位置、编号或父子前序重排。选择“桥头等候”和“车站等候”两个scene的真实ID，一次import_screenplay_timeline导入两分支全部内容（含必要祖先）。使用刚读取的真实修订。再read_screenplay_timeline核对“乙入站”在“甲到桥头”之前，而“甲到桥头”的父scene仍为“桥头等候”。不得改写提取、改编或创建其他内容。简述候选及导入后的实际顺序。不重启任务或重试服务。"""


def moments(snapshot):
    return [row["id"] for row in snapshot["nodes"] if row["node_type"] in {"storyline", "big_event", "scene", "event", "action"}]


def seed(ops):
    fixture = hierarchy.seed(ops)
    ops.sync_adaptation()
    ids = fixture["ids"]
    ops.move_adaptation_node(ids["甲到桥头"], after_id=ids["乙入站"], reason="验收fixture跨分支重排，不改变结构归属")
    fixture["expected_order"] = moments(ops.adaptation_snapshot())
    fixture["candidate_before"] = ops.import_screenplay_timeline_candidates(fixture["screenplay_id"])
    fixture["properties_before"] = ops.property_records()
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    from narrative.tool_api import TOOLS
    ids = fixture["ids"]
    local = ops.read_screenplay_timeline(fixture["screenplay_id"])
    rows = {row["id"]: row for row in local["nodes"]}
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    writes = [step for step in audit["steps"] if step.get("ok") and step["tool"] == "import_screenplay_timeline"]
    report["import_order_evidence"] = {"candidates": fixture["candidate_before"], "local_timeline": local,
        "expected_order": fixture["expected_order"], "successful_imports": writes}
    return {
        "candidate_order_matches_adaptation": [row["id"] for row in fixture["candidate_before"]] == fixture["expected_order"],
        "rearranged_order_imported": moments(local) == fixture["expected_order"],
        "not_source_order": moments(local).index(ids["乙入站"]) < moments(local).index(ids["甲到桥头"]),
        "parent_unchanged": ids["甲到桥头"] in rows[ids["桥头等候"]]["events"],
        "two_containers_selected_once": len(writes) == 1 and set(writes[0]["args"]["moment_ids"]) == {ids["桥头等候"], ids["车站等候"]},
        "only_authorized_write": all(step["tool"] == "import_screenplay_timeline" for step in audit["steps"] if step["tool"] not in TOOLS or TOOLS[step["tool"]].writes),
        "source_properties_unchanged": ops.property_records() == fixture["properties_before"],
        "script_static_ok": ops.check_screenplay(fixture["screenplay_id"])["ok"],
    }


def main():
    qa.seed, qa.TEXT, qa.TASK = seed, TEXT, TASK
    qa.PROMPT_VERSION = "screenplay-adaptation-import-order/1"
    qa.FIXTURE_ORIGIN = "Operations synthetic source + cross-branch adaptation reorder; real task selects containers and imports independent script timeline"
    return qa.main(validate=validate, required_tools=["import_screenplay_timeline_candidates", "import_screenplay_timeline", "read_screenplay_timeline"],
        prompt_factory=lambda fixture: TASK + "\n目标screenplay_id=" + fixture["screenplay_id"], use_writing_target=False)


if __name__ == "__main__":
    raise SystemExit(main())
