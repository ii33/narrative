"""A definition's usage is structural history, never just the latest active state."""
import copy

from agent_fixtures import project as project

from narrative.property_audit import audit_properties
from narrative.temporal_state import projected_changes

TEXT = "甲与乙成为朋友。甲与乙明确绝交。"
FIRST, LAST = "甲与乙成为朋友。", "甲与乙明确绝交。"


def seed(ops):
    sid = ops.import_source("关系历史", TEXT)
    draft = {"nodes": [
        {"type": "person", "name": name, "quotes": [FIRST]} for name in ("甲", "乙")
    ] + [{"type": "scene", "name": "往来", "quotes": [TEXT]},
         {"type": "event", "name": "建立关系", "scene": "往来", "participants": ["甲", "乙"], "quotes": [FIRST]},
         {"type": "event", "name": "结束关系", "scene": "往来", "participants": ["甲", "乙"], "quotes": [LAST]}],
        "properties": [{"node": "甲", "property": "朋友", "value": "乙", "value_type": "node", "at_event": "建立关系", "op": "set", "quotes": [FIRST]}]}
    assert ops.submit_extraction(sid, draft)["status"] == "accepted"
    ids = {node["name"]: node["id"] for node in ops.graph_snapshot()["nodes"]}
    row = next(record for record in ops.property_records() if record.get("kind") == "property_change")
    ops.record_property_change(ids["甲"], "朋友", ids["结束关系"], "remove", value=ids["乙"],
        expected_revision=ops.graph_project_info()["revision"], reason="原文明示关系结束")
    assert ops.apply_changes([], [{"kind": "property_definition", "id": "未录入性质", "value_type": "string", "cardinality": "one"}],
        ops.graph_project_info()["revision"], reason="尚无取值的定义")["status"] == "accepted"
    return sid, ids, row["id"]


def unused(issues):
    return {row["property_id"] for row in issues if row["code"] == "definition_unused"}


def test_project_check_counts_ended_values_as_used_without_reviving_them(project):
    _sid, ids, row_id = seed(project)
    before = copy.deepcopy((project.graph_snapshot(), project.property_records()))
    checked = project.check_project()
    assert "朋友" not in unused(checked["issues"])
    assert unused(checked["issues"]) == {"未录入性质"}
    hint = next(row for row in checked["issues"] if row["code"] == "definition_unused")
    assert "不表示漏提" in hint["suggestion"]
    assert next(row for row in project.property_records() if row["id"] == row_id)["op"] == "set"
    assert project.property_state(ids["甲"], "朋友")["state"] == {"kind": "absent"}
    assert (project.graph_snapshot(), project.property_records()) == before


def test_node_subset_audit_counts_other_nodes_and_closed_history_usage(project):
    _sid, ids, _row = seed(project)
    issues = project.property_audit(node_ids={ids["乙"]})["issues"]
    assert unused(issues) == {"未录入性质"}
    assert not any(row["code"] == "duplicate_value" for row in issues)


def test_explicit_cut_projection_uses_full_history_for_definition_usage(project):
    sid, ids, row_id = seed(project)
    records = project.property_records()
    graph = project.graph_snapshot()
    definitions = [r for r in records if r['kind'] == 'property_definition']
    for event in (ids['建立关系'], ids['结束关系']):
        projected = definitions + projected_changes(graph, records, event)
        issues = audit_properties(projected, graph['nodes'], usage_records=records)['issues']
        assert unused(issues) == {'未录入性质'}
        current = [r for r in projected if r.get('state_kind') == 'known']
        assert any(r['id'] == row_id for r in current) == (event == ids['建立关系'])


def test_active_duplicate_detection_is_unchanged_with_historical_usage():
    nodes = [{"id": "P1", "node_type": "person", "name": "甲"}]
    definition = {"kind": "property_definition", "id": "描述", "value_type": "string", "cardinality": "one"}
    value = {"kind": "property_change", "id": "PV1", "node_id": "P1", "property_id": "描述", "value": "记录",
             "event_id": "E1", "op": "set", "sequence": 0, "disclosure": {"source_id": "S", "start": 0}, "evidence": {"source_id": "S", "quote": "甲留下记录。"}}
    active = [definition, value, {**value, "id": "PV2"}]
    history = active + [{**value, "id": "closed", "property_id": "旧描述", "event_id": "E2", "op": "clear"}]
    duplicates = [row for row in audit_properties(active, nodes, usage_records=history)["issues"] if row["code"] == "duplicate_value"]
    assert len(duplicates) == 1 and set(duplicates[0]["record_ids"]) == {"PV1", "PV2"}
