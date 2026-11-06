"""Published extraction drafts cannot be confused with stored graph objects."""
import copy

import pytest
from agent_fixtures import TEXT, draft_result
from agent_fixtures import project as project

from narrative.tool_api import TOOLS


def test_graph_schema_publishes_same_complete_draft_contract_as_tools(project):
    stored = project.get_schema()
    contract = stored["extraction_submission"]
    assert contract["input_schema"] == TOOLS["submit_extraction"].schema() == TOOLS["preview_extraction"].schema()
    result = contract["input_schema"]["properties"]["result"]
    assert result["required"] == ["nodes", "properties"]
    assert result == contract["result_schema"]
    assert not result["additionalProperties"]
    assert "start" in contract["input_schema"]["properties"] and "start" not in result["properties"]
    assert "identity_note" in stored["fields"]["node"]["optional"]
    assert "identity_note" not in result["properties"]["nodes"]["items"]["properties"]
    assert not result["properties"]["nodes"]["items"]["additionalProperties"]


@pytest.mark.parametrize("mistake", ["storage_field", "nested_range"])
def test_storage_only_node_field_and_nested_range_reject_without_writes(project, mistake):
    sid = project.import_source("合同边界", TEXT)
    before = copy.deepcopy((project.graph_snapshot(), project.property_records()))
    draft = draft_result()
    if mistake == "storage_field":
        draft["nodes"][0]["identity_note"] = "此字段属于存储节点"
        expected = "未知字段"
    else:
        draft["start"] = 0
        expected = "未知草稿字段"
    staged = project.submit_extraction(sid, draft)
    assert staged['status'] == 'staged'
    assert expected in staged['problems'][0]['message']
    assert (project.graph_snapshot(), project.property_records()) == before


def test_semantic_batches_reuse_previous_nodes_and_close_later_interval(project):
    first, second = "甲在门口等乙。", "乙来到门口，两人一起离开。"
    sid = project.import_source("按语境增量", first + second)
    result = project.submit_extraction(sid, {"nodes": [
        {"type": "person", "name": "甲", "quotes": [first]},
        {"type": "person", "name": "乙", "quotes": [first]},
        {"type": "scene", "name": "门口", "quotes": [first]},
        {"type": "event", "name": "等候", "scene": "@3", "participants": ["@1"], "quotes": [first]}],
        "properties": [{"node": "@1", "property": "等候对象", "value": "@2", "value_type": "node", "op": "set", "at_event": "@4", "quotes": [first]}]},
        start=0, end=len(first))
    assert result["status"] == "accepted"
    ids = result["node_ids"]
    record = next(row for row in project.property_records() if row.get("kind") == "property_change")
    result = project.submit_extraction(sid, {"nodes": [
        {"type": "event", "name": "汇合离开", "scene": ids["@3"], "participants": [ids["@1"], ids["@2"]], "quotes": [second]}],
        "properties": [{"node": ids["@1"], "property": "等候对象", "op": "clear", "at_event": "@1", "quotes": [second]}]},
        start=len(first), end=len(first + second))
    assert result["status"] == "accepted"
    nodes = project.graph_snapshot()["nodes"]
    assert len(nodes) == 5 and {node["id"] for node in nodes if node["node_type"] == "person"} == {ids["@1"], ids["@2"]}
    assert project.property_state(ids["@1"], "等候对象")["state"] == {"kind": "absent"}
    assert next(row for row in project.property_records() if row["id"] == record["id"])["op"] == "set"
