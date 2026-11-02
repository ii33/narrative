"""Relation direction comes from explicit semantics, never the displayed word."""
from agent_fixtures import project as project
from test_relationship_state import setup_relations

from narrative import tool_api
from narrative.project_package import layout


def test_map_uses_explicit_symmetry_without_creating_reverse_values(project):
    setup_relations(project)
    entry = layout(project.project_info().path).entry
    before = project.property_records()
    definition = next(row for row in before if row["id"] == "朋友")
    revision = project.graph_project_info()["revision"]
    result = project.apply_changes([], [definition | {"symmetric": True}], revision, "明确该定义是对称关系")
    assert result["status"] == "accepted"
    edges = tool_api.call("relation_map", {}, project=entry)["edges"]
    assert len(edges) == 2 and all(edge["symmetric"] for edge in edges)
    assert [row for row in project.property_records() if row["kind"] == "property_value"] == [
        row for row in before if row["kind"] == "property_value"]


def test_known_relation_name_without_symmetry_is_not_guessed(project):
    setup_relations(project)
    edges = tool_api.call("relation_map", {}, project=layout(project.project_info().path).entry)["edges"]
    assert edges and all(edge["symmetric"] is False for edge in edges)
