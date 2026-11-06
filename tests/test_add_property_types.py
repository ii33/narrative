"""Tool additions explicitly type new definitions and never infer IDs from text."""
import pytest
from agent_fixtures import project as project
from test_relationship_state import setup_relations

from narrative.project_package import layout
from narrative.tool_api import ToolError, call


def setup(project):
    ids = setup_relations(project)
    return layout(project.project_info().path).entry, {"node_id": ids["甲"], "value": ids["乙"], "event_id": ids["甲认识朋友乙"], "quote": "甲认识朋友乙。", "category": "关系"}


def test_explicit_node_new_role_is_drawn_and_any_same_id_remains_literal(project):
    entry, args = setup(project)
    assert call("record_property_change", args | {"op": "set", "expected_revision": project.graph_project_info()["revision"], "reason": "定向补录"} | {"property_id": "诈骗对象", "value_type": "node", "symmetric": False}, project=entry)["status"] == "accepted"
    assert call("record_property_change", args | {"op": "set", "expected_revision": project.graph_project_info()["revision"], "reason": "定向补录"} | {"property_id": "字面编号", "value_type": "any"}, project=entry)["status"] == "accepted"
    edges = call("relation_map", {}, project=entry)["edges"]
    assert any(e["kind"] == "诈骗对象" and e["from"] == args["node_id"] and e["to"] == args["value"] for e in edges)
    assert not any(e["kind"] == "字面编号" for e in edges)
    assert call("record_property_change", args | {"op": "set", "expected_revision": project.graph_project_info()["revision"], "reason": "定向补录"} | {"property_id": "诈骗对象"}, project=entry)["status"] in {"accepted", "unchanged"}


@pytest.mark.parametrize("changes", [{}, {"value_type": "node", "symmetric": "false"}, {"value_type": []}])
def test_new_definition_requires_strict_explicit_type_and_symmetry_without_write(project, changes):
    entry, args = setup(project)
    before = project.graph_snapshot(), project.property_records()
    with pytest.raises(ToolError):
        call("record_property_change", args | {"op": "set", "expected_revision": project.graph_project_info()["revision"], "reason": "定向补录"} | {"property_id": "新定义"} | changes, project=entry)
    assert (project.graph_snapshot(), project.property_records()) == before


@pytest.mark.parametrize("changes", [{"value_type": "string"}, {"symmetric": True}])
def test_existing_definition_conflicts_do_not_change_definition_or_values(project, changes):
    entry, args = setup(project)
    before = project.graph_snapshot(), project.property_records()
    with pytest.raises(ToolError, match="不一致"):
        call("record_property_change", args | {"op": "set", "expected_revision": project.graph_project_info()["revision"], "reason": "定向补录"} | {"property_id": "朋友"} | changes, project=entry)
    assert (project.graph_snapshot(), project.property_records()) == before
