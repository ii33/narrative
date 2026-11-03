"""One bounded task imports a container and a leaf in another source branch."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

TEXT = "甲走到桥头。甲停下等人。乙走入车站。乙穿着蓝衣坐下。"
TASK = """这是多级时间点选择导入的独立验收。请实际调用import_adaptation_candidates读取父子层级，从中选择“桥头等候”scene（应包含它的下属事件）及另一分支“乙坐下”event，按这两个真实ID通过import_adaptation一次导入。不要选顶层大事件，不导入另一分支“乙入站”事件，不调用sync_adaptation，不修改提取，不创建节点或动作。随后adaptation_snapshot与check_adaptation核对导入的子树、必要归属和相关状态。简述所选容器与叶节点ID，不开启新任务或重试服务。"""


def seed(ops):
    source_id = ops.import_source("分支选择合成原文", TEXT)
    result = ops.submit_extraction(source_id, {"nodes": [
        {"type": "person", "name": "甲", "quotes": ["甲走到桥头。"]},
        {"type": "person", "name": "乙", "quotes": ["乙走入车站。"]},
        {"type": "big_event", "name": "两地等候", "quotes": [TEXT]},
        {"type": "scene", "name": "桥头等候", "big_event": "两地等候", "quotes": ["甲走到桥头。甲停下等人。"]},
        {"type": "scene", "name": "车站等候", "big_event": "两地等候", "quotes": ["乙走入车站。乙穿着蓝衣坐下。"]},
        {"type": "event", "name": "甲到桥头", "scene": "桥头等候", "participants": ["甲"], "quotes": ["甲走到桥头。"]},
        {"type": "event", "name": "甲等待", "scene": "桥头等候", "participants": ["甲"], "quotes": ["甲停下等人。"]},
        {"type": "event", "name": "乙入站", "scene": "车站等候", "participants": ["乙"], "quotes": ["乙走入车站。"]},
        {"type": "event", "name": "乙坐下", "scene": "车站等候", "participants": ["乙"], "quotes": ["乙穿着蓝衣坐下。"]}],
        "properties": [{"node": "乙", "property": "服装", "value": "蓝衣", "value_type": "string",
            "at_event": "乙坐下", "quotes": ["乙穿着蓝衣坐下。"]}]})
    assert result["status"] == "accepted", result
    ids = {node["name"]: node["id"] for node in ops.graph_snapshot()["nodes"]}
    # The shared evidence runner also archives an empty screenplay container;
    # this is deterministic scaffolding and is not part of the model task.
    screenplay = ops.create_screenplay("验收资料容器", reason="统一验收采集器的确定性空容器")["screenplay"]
    return {"source_id": source_id, "screenplay_id": screenplay["id"], "ids": ids,
        "candidates": ops.import_adaptation_candidates(), "properties_before": ops.property_records()}


def validate(ops, fixture, screenplay, calls, report):
    from narrative.tool_api import TOOLS
    ids = fixture["ids"]
    snapshot = ops.adaptation_snapshot()
    nodes = {node["id"]: node for node in snapshot["nodes"]}
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    writes = [step for step in audit["steps"] if step["tool"] == "import_adaptation" and step.get("ok")]
    rows = {row["id"]: row for row in fixture["candidates"]}
    report["hierarchy_evidence"] = {"candidates": fixture["candidates"], "adaptation": snapshot,
        "successful_imports": writes, "static": ops.check_adaptation()}
    return {"source_hierarchy_available": rows[ids["甲等待"]]["parent_id"] == ids["桥头等候"]
            and rows[ids["乙坐下"]]["parent_id"] == ids["车站等候"],
        "one_selected_import": len(writes) == 1 and set(writes[0]["args"]["moment_ids"]) == {ids["桥头等候"], ids["乙坐下"]},
        "container_children_imported": {ids["桥头等候"], ids["甲到桥头"], ids["甲等待"]} <= nodes.keys(),
        "other_branch_leaf_imported": ids["乙坐下"] in nodes and ids["车站等候"] in nodes,
        "unselected_sibling_absent": ids["乙入站"] not in nodes,
        "related_state_imported": any(row.get("node_id") == ids["乙"] and row.get("property_id") == "服装" and row.get("value") == "蓝衣" for row in snapshot["properties"]),
        "properties_unchanged": ops.property_records() == fixture["properties_before"],
        "no_actions_invented": all(node["node_type"] != "action" for node in nodes.values()),
        "only_authorized_writes": all(step["tool"] == "import_adaptation" for step in audit["steps"]
            if step["tool"] not in TOOLS or TOOLS[step["tool"]].writes),
        "adaptation_static_ok": ops.check_adaptation()["ok"]}


def main():
    qa.seed = seed
    qa.TEXT = TEXT
    qa.TASK = TASK
    qa.PROMPT_VERSION = "adaptation-hierarchical-selection/1"
    qa.FIXTURE_ORIGIN = "Deterministic source extraction via Operations with two branches; real model only selects existing container+leaf and imports adaptation"
    return qa.main(validate=validate, required_tools=["import_adaptation_candidates", "import_adaptation", "adaptation_snapshot", "check_adaptation"],
        use_writing_target=False, allow_adaptation_change=True)


if __name__ == "__main__":
    raise SystemExit(main())
