"""Relationships retain independent counterparts at each disclosure position."""

import pytest
from agent_fixtures import project as project

from narrative import tool_api
from narrative.project_package import layout

TEXT = "甲认识朋友乙。甲认识朋友丙。甲与乙绝交。甲仍与丙来往。"


def draft():
    events = ["甲认识朋友乙", "甲认识朋友丙", "甲与乙绝交", "甲仍与丙来往"]
    nodes = [{"type": "person", "name": name, "quotes": [TEXT]} for name in "甲乙丙"]
    nodes += [
        {"type": "big_event", "name": "结交与绝交", "quotes": [TEXT]},
        {"type": "scene", "name": "朋友往来", "big_event": "结交与绝交", "quotes": [TEXT]},
    ]
    nodes += [
        {
            "type": "event",
            "name": name,
            "scene": "朋友往来",
            "participants": ["甲", "乙", "丙"],
            "quotes": [name + "。"],
        }
        for name in events
    ]
    props = [
        {
            "node": "甲",
            "property": "朋友",
            "value": name,
            "value_type": "node",
            "category": "关系",
            "quotes": [events[i] + "。"],
            "at_event": events[i],
        }
        for i, name in enumerate("乙丙")
    ]
    return {
        "nodes": nodes,
        "properties": props,
        "expirations": [],
        "uncertainties": [],
        "exclusions": [],
    }


def setup_relations(project):
    sid = project.import_source("原文", TEXT)
    assert project.submit_extraction(sid, draft())["status"] == "accepted"
    return {n["name"]: n["id"] for n in project.graph_snapshot()["nodes"]}


def values(project, node, cut=None):
    return {
        x["value"]
        for x in project.property_node_summary(node, cut=cut)["properties"]
        if x["property_id"] == "朋友"
    }


def test_multiple_counterparts_survive_submit_state_and_browse(project):
    ids = setup_relations(project)
    assert values(project, ids["甲"]) == {ids["乙"], ids["丙"]}
    state = project.property_state(ids["甲"], "朋友")
    assert set(state["state"]["value"]) == {ids["乙"], ids["丙"]}
    assert len(state["record_ids"]) == 2
    assert values(project, ids["甲"], ids["甲认识朋友乙"]) == {ids["乙"]}
    assert not any(r["kind"] == "property_retraction" for r in project.property_records())


def test_one_counterpart_expires_without_erasing_other_or_past_map(project):
    ids = setup_relations(project)
    old = next(
        r
        for r in project.property_records()
        if r.get("node_id") == ids["甲"] and r.get("value") == ids["乙"]
    )
    source = old["valid_from"]["source_id"]
    project.expire_property(
        old["id"], source, TEXT.index("甲与乙绝交"), reason="原文已绝交", event_id=ids["甲与乙绝交"]
    )
    assert values(project, ids["甲"]) == {ids["丙"]}
    assert values(project, ids["甲"], ids["甲认识朋友丙"]) == {ids["乙"], ids["丙"]}
    latest = tool_api.call("relation_map", {}, project=layout(project.project_info().path).entry)
    assert {e["to"] for e in latest["edges"]} == {ids["丙"]}
    past = tool_api.call(
        "relation_map",
        {"cut": ids["甲认识朋友丙"]},
        project=layout(project.project_info().path).entry,
    )
    assert {e["to"] for e in past["edges"]} == {ids["乙"], ids["丙"]}


def test_nonrelationship_node_values_close_previous_holder_interval(project):
    from test_current_state_contract import item_transfer

    sid, _cut = item_transfer(project)
    rows = {row["id"]: row for row in project.property_records() if row["kind"] == "property_value"}
    assert rows["holder0"]["valid_to"]["source_id"] == sid
    assert rows["holder0"]["valid_to"]["start"] == rows["holder1"]["valid_from"]["start"]
    assert rows["holder1"].get("valid_to") is None


def test_person_id_shaped_text_is_not_a_relationship(project):
    ids = setup_relations(project)
    records = [
        {"kind": "property_definition", "id": "代号", "value_type": "string", "cardinality": "one"},
        {
            "kind": "property_value",
            "id": "text-id",
            "node_id": ids["甲"],
            "property_id": "代号",
            "value": ids["乙"],
            "event_id": ids["甲认识朋友乙"],
            "category": "基本信息",
        },
    ]
    assert (
        project.apply_changes(
            [], records, project.graph_project_info()["revision"], reason="合成关系状态"
        )["status"]
        == "accepted"
    )
    mapped = tool_api.call("relation_map", {}, project=layout(project.project_info().path).entry)
    assert {edge["kind"] for edge in mapped["edges"]} == {"朋友"}
    assert project.property_state(ids["甲"], "代号")["state"]["value"] == ids["乙"]


@pytest.mark.parametrize("value_type", ["node", "either"])
def test_named_relationship_interval_expiry_preserves_other_counterpart(project, value_type):
    sid = project.import_source("原文", TEXT)
    data = draft()
    for prop in data["properties"]:
        prop["value_type"] = value_type
    assert project.submit_extraction(sid, data)["status"] == "accepted"
    ids = {n["name"]: n["id"] for n in project.graph_snapshot()["nodes"]}
    target = next(
        r
        for r in project.property_records()
        if r.get("property_id") == "朋友" and r["value"] == ids["乙"]
    )
    project.expire_property(
        target["id"],
        sid,
        TEXT.index("甲与乙绝交"),
        event_id=ids["甲与乙绝交"],
        reason="明确结束乙这一对象",
    )
    assert values(project, ids["甲"]) == {ids["丙"]}
    assert values(project, ids["甲"], ids["甲认识朋友丙"]) == {ids["乙"], ids["丙"]}
    ended = next(r for r in project.property_records() if r["id"] == target["id"])
    assert ended["value"] == ids["乙"] and ended["valid_to"]["start"] == TEXT.index("甲与乙绝交")


@pytest.mark.parametrize("value_type", ["string", "any"])
def test_expiry_does_not_convert_id_shaped_opaque_value(project, value_type):
    ids = setup_relations(project)
    source = next(
        r["valid_from"]["source_id"]
        for r in project.property_records()
        if r["kind"] == "property_value"
    )
    rows = [
        {
            "kind": "property_definition",
            "id": "记号",
            "value_type": value_type,
            "cardinality": "one",
        },
        {
            "kind": "property_value",
            "id": "opaque_id_text",
            "node_id": ids["甲"],
            "property_id": "记号",
            "value": ids["乙"],
            "event_id": ids["甲认识朋友乙"],
            "evidence": {"source_id": source, "quote": "甲认识朋友乙。"},
            "disclosure": {"source_id": source, "start": 0},
        },
    ]
    assert (
        project.apply_changes(
            [], rows, project.graph_project_info()["revision"], reason="字面量不按人物引用解释"
        )["status"]
        == "accepted"
    )
    project.expire_property(
        "opaque_id_text",
        source,
        TEXT.index("甲与乙绝交"),
        event_id=ids["甲与乙绝交"],
        reason="结束指定记录而非转换值",
    )
    ended = next(r for r in project.property_records() if r["id"] == "opaque_id_text")
    assert ended["value"] == ids["乙"] and ended["valid_to"]["start"] == TEXT.index("甲与乙绝交")
