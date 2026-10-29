"""Facade boundaries: narrow services, thin delegation, no god-object back-reference."""

from __future__ import annotations

import ast
import inspect

import pytest

from narrative import graph_facades
from narrative.graph_operations import GraphOperations
from narrative.operations import Operations


def _project(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "graph.nproj")
    ops.import_source("短章", "甲把信交给乙。")
    return ops


def test_facades_do_not_hold_the_project_facade() -> None:
    """A facade must depend on its service, not on GraphOperations itself."""
    for name, member in inspect.getmembers(graph_facades, inspect.isclass):
        if not name.startswith("Graph") or not name.endswith("Facade"):
            continue
        params = list(inspect.signature(member.__init__).parameters)[1:]
        assert "ops" not in params, f"{name} still takes the whole project object"
        assert params, f"{name} must depend on a service"


def test_facade_groups_are_separate_objects(tmp_path) -> None:
    ops = _project(tmp_path)
    groups = {
        "project": ops.project, "review": ops.review, "properties": ops.properties,
        "changes": ops.changes, "query": ops.query, "profiles": ops.profiles,
        "graphrag": ops.graphrag,
    }
    assert len({id(item) for item in groups.values()}) == len(groups)
    for facade in groups.values():
        assert not hasattr(facade, "ops")


def test_compatibility_methods_delegate_to_facades(tmp_path, monkeypatch) -> None:
    ops = _project(tmp_path)
    calls: list[tuple[str, tuple]] = []

    def record(name: str, result):
        def inner(*args, **kwargs):
            calls.append((name, args))
            return result
        return inner

    monkeypatch.setattr(ops.review, "precheck", record("review.precheck", []))
    monkeypatch.setattr(ops.changes, "candidates", record("changes.candidates", []))
    monkeypatch.setattr(ops.query, "view", record("query.view", {}))
    monkeypatch.setattr(ops.profiles, "settings", record("profiles.settings", {}))

    assert ops.graph_precheck([], 1) == []
    assert ops.change_candidates() == []
    assert ops.graph_view({"view": "graph"}) == {}
    assert ops.harness_settings() == {}

    assert [name for name, _ in calls] == [
        "review.precheck", "changes.candidates", "query.view", "profiles.settings",
    ]


def test_query_facade_exposes_projection_groups(tmp_path) -> None:
    ops = _project(tmp_path)
    for name in (
        "query", "view", "display_map", "candidate_preview",
        "timeline_view", "event_compare", "event_lines",
    ):
        assert callable(getattr(ops.query, name)), name


def test_property_facade_reuses_one_property_store(tmp_path) -> None:
    ops = _project(tmp_path)
    assert ops.properties.properties is ops._properties
    assert ops.changes.changes is ops._changes


FACADE_GROUPS = {
    "project", "review", "properties", "changes", "query", "profiles", "graphrag",
}


def _delegation_target(method: str) -> tuple[str, str]:
    """Return (facade, method) for a one-line forwarding method, else fail."""
    source = inspect.getsource(getattr(GraphOperations, method))
    tree = ast.parse(source.lstrip())
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
    body = [
        node for node in function.body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
    ]
    assert len(body) == 1, f"{method} has {len(body)} statements"
    statement = body[0]
    assert isinstance(statement, (ast.Return, ast.Expr)), f"{method} has unexpected statements"
    value = statement.value
    assert isinstance(value, ast.Call), f"{method} does not forward to a facade"
    target = value.func
    assert isinstance(target, ast.Attribute), f"{method} calls a free function"
    owner = target.value
    assert isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name)
    # Facade attribute, or a private service the facade is built from.
    assert owner.value.id == "self" and (
        owner.attr in FACADE_GROUPS or owner.attr.startswith("_")
    ), f"{method} does not go through a facade"
    return owner.attr, target.attr


@pytest.mark.parametrize(
    "method",
    [
        "graph_schema", "graph_view", "graph_precheck", "graph_propose",
        "graph_candidates", "graph_candidate", "graph_accept", "graph_reject",
        "graph_timeline",
        "graph_event_compare", "graph_event_lines", "graph_query",
        "graph_display_map", "graph_candidate_preview",
        "graph_edit_candidate", "graph_select_operations",         "property_precheck", "property_propose", "property_state",
        "property_disclosure_state", "property_history", "property_world_state",
        "change_precheck", "change_propose", "change_candidates", "change_accept",
        "harness_settings", "provider_catalog", "save_harness_settings",
        "graphrag_preview", "graphrag_stage_sources",
    ],
)
def test_compatibility_methods_are_thin_delegations(method: str) -> None:
    facade, name = _delegation_target(method)
    assert facade and name
