"""Program-assigned record IDs preserve fact scope and exact provenance."""
from copy import deepcopy

import pytest
from agent_fixtures import TEXT, draft_result
from agent_fixtures import project as project


def friend_rows(project):
    return [row for row in project.property_records() if row.get("property_id") == "朋友"]


@pytest.mark.parametrize("scopes", [(("world", None), ("cognition", "甲")),
                                    (("claim", "甲"), ("claim", "乙"))])
def test_same_value_in_distinct_scope_or_holder_is_not_dropped(project, scopes):
    sid = project.import_source("原文", TEXT)
    data = draft_result(ended=False)
    original = next(row for row in data["properties"] if row["property"] == "朋友")
    data["properties"] = []
    for scope, holder in scopes:
        row = deepcopy(original) | {"scope": scope}
        if holder:
            row["holder"] = holder
        data["properties"].append(row)
    assert project.preview_extraction(sid, data)["valid"]
    result = project.submit_extraction(sid, data)
    assert result["status"] == "accepted" and result["property_count"] == 2
    records = friend_rows(project)
    assert len(records) == 2 and len({row["id"] for row in records}) == 2
    assert all(row["id"].startswith("PC") and row["id"][2:].isdigit() for row in records)
    before = project.graph_snapshot(), project.property_records()
    assert project.submit_extraction(sid, data)["status"] == "unchanged"
    assert (project.graph_snapshot(), project.property_records()) == before


def test_same_anchor_and_value_at_different_disclosure_positions_are_preserved(project):
    sid = project.import_source("原文", TEXT)
    data = draft_result(ended=False)
    original = next(row for row in data["properties"] if row["property"] == "朋友")
    data["properties"] = [original, original | {"quotes": ["甲接到乙后离开。"]}]
    assert project.submit_extraction(sid, data)["status"] == "accepted"
    records = friend_rows(project)
    assert len(records) == 2
    assert len({row["disclosure"]["start"] for row in records}) == 2


def test_typed_json_value_is_preserved_without_model_stringification(project):
    sid = project.import_source("原文", TEXT)
    data = draft_result(ended=False)
    data["properties"] = [{"node": "甲", "property": "备注", "op": "set", "value_type": "any",
                            "value": {"items": [1, False]}, "quotes": ["甲约好接乙。"], "at_event": "甲约好接乙"}]
    assert project.submit_extraction(sid, data)["status"] == "accepted"
    row = next(row for row in project.property_records() if row.get("property_id") == "备注")
    assert row["value"] == {"items": [1, False]}


def test_automatic_ids_skip_explicitly_reserved_definition_ids(project):
    from test_action_extraction import seed

    sid, event = seed(project)
    person = next(node for node in project.graph_snapshot()["nodes"] if node["node_type"] == "person")
    records = [{"kind": "property_definition", "id": "PC1", "value_type": "string", "cardinality": "one"},
               {"kind": "property_change", "op": "set", "sequence": 0, "node_id": person["id"], "property_id": "PC1", "value": "观察",
                "event_id": event["id"], "evidence": {"source_id": sid, "quote": "停下张望"}}]
    result = project.apply_changes([], records, project.graph_project_info()["revision"], "程序分配避开用户语义属性名")
    assert result["status"] == "accepted"
    value = next(row for row in project.property_records() if row["kind"] == "property_change")
    assert value["id"] == "PC2"
