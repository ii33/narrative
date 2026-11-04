"""Interval dependencies never become unselected narrative scenes/events."""
import copy

import pytest
from agent_fixtures import project as project
from test_adaptation_import import setup
from test_screenplay_append import append
from test_screenplay_timeline import create, import_points

from narrative.state_anchors import anchor_nodes


def events(state):
    return {node["id"] for node in state["nodes"] if node["node_type"] in {"event", "action"}}


def test_exact_selection_through_both_domains_and_boundary_promotion(project):
    ids = setup(project)
    extracted = copy.deepcopy(project.graph_snapshot())
    adaptation = project.import_adaptation([ids["事件1"]])
    assert events(adaptation) == {ids["事件1"]}
    assert {node["id"] for node in adaptation["state_anchors"]} == {ids["事件0"], ids["事件2"]}
    assert all(set(row) <= {"id", "node_type", "context_id", "source_position", "offset", "preceding_ids", "following_ids"} for row in adaptation["state_anchors"])
    script = create(project)
    local = import_points(project, script, [ids["事件1"]])
    assert events(local) == {ids["事件1"]}
    assert set(anchor_nodes(local)) == {ids["事件0"], ids["事件2"]}
    assert project.graph_snapshot() == extracted
    # Explicit import promotes only that endpoint to a normal node.
    adaptation = project.import_adaptation([ids["事件2"]])
    assert ids["事件2"] not in anchor_nodes(adaptation)
    assert events(adaptation) == {ids["事件1"], ids["事件2"]}
    local = append(project, script, [ids["事件2"]])
    assert events(local) == {ids["事件1"], ids["事件2"]}
    assert ids["事件2"] not in anchor_nodes(local)
    coat = next(row for row in local["properties"] if row.get("property_id") == "服装" and row["value"] == "青衣")
    assert coat["end_event_id"] == ids["事件2"]
    assert len({node["id"] for node in local["nodes"]}) == len(local["nodes"])


def test_append_promoted_start_after_its_end_is_rejected_atomically(project):
    ids = setup(project)
    project.sync_adaptation()
    script = create(project)
    import_points(project, script, [ids["事件1"]])
    before = copy.deepcopy(project.read_screenplay_timeline(script))
    with pytest.raises(ValueError, match="失效时点必须晚于生效时点|重叠"):
        append(project, script, [ids["事件0"]])
    assert project.read_screenplay_timeline(script) == before


def test_boundary_events_do_not_introduce_their_own_participants_or_scenes(project):
    ids = setup(project)
    source = next(node for node in project.graph_snapshot()["nodes"] if node["id"] == ids["事件0"])
    source = copy.deepcopy(source)
    source["participants"].append(ids["丙"])
    assert project.apply_changes([source], [], project.graph_project_info()["revision"], reason="边界事件独有人物")["status"] == "accepted"
    selected = project.import_adaptation([ids["事件1"]])
    assert ids["丙"] not in {node["id"] for node in selected["nodes"]}
    assert ids["场景0"] not in {node["id"] for node in selected["nodes"]}
    assert ids["场景2"] not in {node["id"] for node in selected["nodes"]}
    assert events(selected) == {ids["事件1"]}
