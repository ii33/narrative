"""One real evidence-based relationship review and precise single-event import."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

QUOTES = ("甲和乙是朋友，丙和丁也是朋友。", "四人在药铺争执，甲乙决定继续结伴送药，丙丁也约好合作。", "甲发现乙偷走药款，乙承认背叛。甲说：绝交，从此不再来往。两人的友谊至此结束。", "丙丁为路线争吵后仍并肩送药。")
TEXT = "".join(QUOTES)
TASK = """请完成一次最小原文复核与精准导入，不创建新人物/事件或重写原文：
1. read_source读取这份短原文，查已有两条朋友关系及事件ID。根据建立、后续变化和存续证据判断哪条应失效、哪条继续；不能把争吵机械等同断交。确认终止的只关闭旧值区间（submit_extraction.expirations或expire_properties），保留此前真实朋友历史，不删除旧值或自动添加敌人。存续的关系原样保留。
2. import_adaptation_candidates找到“争执后合作”event，仅以该单事件ID import_adaptation，不选父scene、不选其余事件。相关状态可以带入，但起止边界事件不是选中剧情。
3. import_screenplay_timeline_candidates读取给定空剧本候选，仅以同一“争执后合作”event ID import_screenplay_timeline。不要改编重排、不要写场表正文；仅这三项必要业务写入。无需再读大快照。简述关系判断、实际关闭记录和导入单事件结果，不另开任务或重试服务。"""


def seed(ops):
    source_id = ops.import_source("朋友关系变化与存续对照", TEXT)
    result = ops.submit_extraction(source_id, {"nodes": [
        *[{"type": "person", "name": name, "quotes": [name]} for name in "甲乙丙丁"],
        {"type": "scene", "name": "送药风波", "quotes": [TEXT]},
        *[{"type": "event", "name": name, "scene": "送药风波", "participants": list("甲乙丙丁"), "quotes": [quote]} for name, quote in zip(("建立友谊", "争执后合作", "明确断交", "继续送药"), QUOTES, strict=True)]],
        "properties": [{"node": a, "property": "朋友", "value": b, "value_type": "node", "symmetric": True, "at_event": "建立友谊", "quotes": [QUOTES[0]]} for a, b in (("甲", "乙"), ("丙", "丁"))]})
    assert result["status"] == "accepted", result
    document = ops.create_screenplay("关系复核精准导入", reason="独立验收空剧本") ["screenplay"]
    return {"source_id": source_id, "screenplay_id": document["id"], "ids": {row["name"]: row["id"] for row in ops.graph_snapshot()["nodes"]},
        "nodes_before": ops.graph_snapshot()["nodes"], "properties_before": ops.property_records(), "source_before": ops.source(source_id)}


def validate(ops, fixture, screenplay, calls, report):
    from narrative.project_package import layout
    from narrative.state_anchors import anchor_nodes
    from narrative.tool_api import TOOLS, call
    ids = fixture["ids"]
    before = fixture["properties_before"]
    current = ops.property_records()
    ended = next(row for row in before if row.get("node_id") == ids["甲"])
    boundary = {"source_id": fixture["source_id"], "start": TEXT.index(QUOTES[2]), "event_id": ids["明确断交"]}
    expected = [row | {"valid_to": boundary} if row["id"] == ended["id"] else row for row in before]
    adaptation, local = ops.adaptation_snapshot(), ops.read_screenplay_timeline(fixture["screenplay_id"])
    def narrative(snapshot):
        return {node["id"] for node in snapshot["nodes"] if node["node_type"] in {"event", "action"}}
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    attempts = [step for step in audit["steps"] if step["tool"] in TOOLS and TOOLS[step["tool"]].writes]
    writes = [step for step in attempts if step.get("ok")]
    def friend_edges(cut):
        return {edge["record_id"] for edge in call("relation_map", {"cut": cut}, project=layout(ops.project_info().path).entry)["edges"]}
    original_values = {row["id"] for row in before if row.get("kind") == "property_value"}
    def interval_geometry(snapshot):
        points = {**anchor_nodes(snapshot), **{node["id"]: node for node in snapshot["nodes"]}}
        relation = next(row for row in snapshot["properties"] if row["id"] == ended["id"])
        return (relation.get("event_id") == ids["建立友谊"] and relation.get("end_event_id") == ids["明确断交"]
            and points[ids["建立友谊"]]["sort_id"] < points[ids["争执后合作"]]["sort_id"] < points[ids["明确断交"]]["sort_id"])
    report["relationship_evidence"] = {"source_properties_after": current, "adaptation": adaptation, "local_timeline": local,
        "attempted_writes": attempts, "successful_writes": writes,
        "relations_at_selected": sorted(friend_edges(ids["争执后合作"])), "relations_after_end": sorted(friend_edges(ids["明确断交"]))}
    return {"only_evidenced_relationship_expired": {row["id"]: row for row in current} == {row["id"]: row for row in expected},
        "historical_and_continuing_relations_preserved": friend_edges(ids["争执后合作"]) == original_values and friend_edges(ids["明确断交"]) == original_values - {ended["id"]},
        "source_nodes_and_text_unchanged": ops.graph_snapshot()["nodes"] == fixture["nodes_before"] and ops.source(fixture["source_id"]) == fixture["source_before"],
        "only_one_event_in_adaptation": narrative(adaptation) == {ids["争执后合作"]},
        "only_one_event_in_script": narrative(local) == {ids["争执后合作"]},
        "related_values_retained": all({row["id"] for row in snap["properties"] if row.get("kind") == "property_value"} == original_values for snap in (adaptation, local)),
        "interval_boundaries_retained_outside_narrative": all({ids["建立友谊"], ids["明确断交"]} <= {anchor["id"] for anchor in snap.get("state_anchors", [])} and next(row for row in snap["properties"] if row["id"] == ended["id"]).get("end_event_id") == ids["明确断交"] for snap in (adaptation, local)),
        "boundary_metadata_contains_no_story": all(not ({"name", "participants", "evidence", "quotes"} & set(anchor)) for snap in (adaptation, local) for anchor in snap.get("state_anchors", [])),
        "interval_geometry_preserved": all(interval_geometry(snap) for snap in (adaptation, local)),
        "only_required_writes": len(writes) == 3 and writes[0]["tool"] in {"expire_properties", "submit_extraction"} and [step["tool"] for step in writes[1:]] == ["import_adaptation", "import_screenplay_timeline"],
        "exact_event_selection": all(step["args"].get("moment_ids") == [ids["争执后合作"]] for step in writes[1:]),
        "both_layers_static_ok": ops.check_adaptation()["ok"] and ops.check_screenplay(fixture["screenplay_id"])["ok"]}


def main():
    qa.seed, qa.TEXT, qa.TASK = seed, TEXT, TASK
    qa.PROMPT_VERSION = "evidence-relationship-expiry-precise-import/1"
    qa.FIXTURE_ORIGIN = "Operations synthetic true historical friendship plus continuing conflict control; real review closes one relationship and imports a single middle event"
    return qa.main(validate=validate, required_tools=["read_source", "import_adaptation_candidates", "import_adaptation", "import_screenplay_timeline_candidates", "import_screenplay_timeline"],
        prompt_factory=lambda fixture: TASK + "\nsource_id=" + fixture["source_id"] + "；目标screenplay_id=" + fixture["screenplay_id"],
        use_writing_target=False, allow_adaptation_change=True, allow_extraction_change=True)


if __name__ == "__main__":
    raise SystemExit(main())
