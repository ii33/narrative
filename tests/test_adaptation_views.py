"""Read-only adaptation browsing uses independent order and time intervals."""
from __future__ import annotations

import copy
import json

import pytest
from agent_fixtures import TEXT, draft_result

from narrative.operations import Operations


def named(state, name):
    return next(node for node in state["nodes"] if node["name"] == name)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "projection.nproj")
    text = TEXT.replace("甲约好接乙。", "甲在门口约好接乙。")
    raw = json.dumps(draft_result(ended=False), ensure_ascii=False)
    draft = json.loads(raw.replace(TEXT, text).replace("甲约好接乙。", "甲在门口约好接乙。"))
    draft["nodes"].append({"type": "location", "name": "门口", "quotes": ["甲在门口约好接乙。"]})
    draft["properties"].append({"node": "甲约好接乙", "property": "发生地点", "value": "门口",
        "value_type": "node", "scope": "world", "category": "空间与位置", "quotes": ["甲在门口约好接乙。"],
        "at_event": "甲约好接乙", "op": "set"})
    source = ops.import_source("合成投影", text)
    assert ops.submit_extraction(source, draft)["status"] == "accepted"
    ops.sync_adaptation()
    return ops


def test_browse_steps_cut_and_children_follow_adaptation_order_not_source_or_ids(project):
    state = project.adaptation_snapshot()
    first = named(state, "甲约好接乙")
    last = named(state, "甲接到乙后离开")
    scene = named(state, "甲约定并完成接乙")
    source = copy.deepcopy(project.graph_snapshot())
    # Original disclosure and event IDs still say the opposite order.
    project.move_adaptation_node(last["id"], after_id=None)
    view = project.adaptation_browse_view(granularity="event", cut_id=scene["id"])
    assert [row["id"] for row in view["steps"]] == [last["id"], first["id"]]
    assert view["cut"]["event_id"] == first["id"]
    assert [row["id"] for row in project.adaptation_node_properties(scene["id"])["children"]] == [last["id"], first["id"]]
    assert "source_id" not in view["cut"]
    assert project.graph_snapshot() == source
    assert view["all_nodes"] == project.adaptation_snapshot()["nodes"]


def test_point_introduction_structured_refs_and_participants_obey_current_cut(project):
    state = project.adaptation_snapshot()
    first = named(state, "甲约好接乙")["id"]
    last = named(state, "甲接到乙后离开")["id"]
    scene = named(state, "甲约定并完成接乙")["id"]
    gate = named(state, "门口")["id"]
    state = project.edit_adaptation_node(changes={"node_type": "person", "name": "迟来者", "point_id": last})
    late = named(state, "迟来者")["id"]
    for event in (first, last):
        old = next(node for node in state["nodes"] if node["id"] == event)
        state = project.edit_adaptation_node(event, {"participants": old["participants"] + [late]})
    before = project.adaptation_browse_view(cut_id=first, time_filter=True, related_only=True)
    after = project.adaptation_browse_view(cut_id=last, time_filter=True, related_only=True)
    assert late not in {row["id"] for row in before["nodes"]}
    assert {"甲", "乙", "门口"} <= {row["name"] for row in before["nodes"]}
    assert late in {row["id"] for row in after["nodes"]}
    assert late not in project.adaptation_node_detail(first, cut=first)["node"]["participants"]
    assert late in project.adaptation_node_detail(last, cut=last)["node"]["participants"]
    locations = project.adaptation_node_properties(scene, cut=first)["involvement"]["locations"]
    assert locations[0]["id"] == gate and locations[0]["event_ids"] == [first]
    assert {row["name"] for row in project.adaptation_node_properties(scene, cut=first)["involvement"]["people"]} == {"甲", "乙"}
    props = project.adaptation_node_properties(first, cut=first)
    location = next(row for row in props["properties"] if row["property_id"] == "发生地点")
    assert location["value_node"]["id"] == gate
    assert any(row["node_id"] == first for row in project.adaptation_node_properties(gate, cut=first)["inbound"])
    project.suppress_adaptation_node(gate)
    assert not project.adaptation_node_properties(scene, cut=first)["involvement"]["locations"]
    assert not any(row["property_id"] == "发生地点" for row in project.adaptation_node_properties(first, cut=first)["properties"])


def test_property_intervals_false_zero_and_editor_shape(project):
    state = project.adaptation_snapshot()
    person = named(state, "甲")["id"]
    first = named(state, "甲约好接乙")["id"]
    last = named(state, "甲接到乙后离开")["id"]
    state = project.set_adaptation_property(person, "开关", first, "set", value=False)
    state = project.set_adaptation_property(person, "开关", last, "set", value=0)
    old = next(row for row in project.adaptation_node_properties(person, cut=first)["properties"] if row["property_id"] == "开关")
    assert old["value"] is False
    new = next(row for row in project.adaptation_node_properties(person, cut=last)["properties"] if row["property_id"] == "开关")
    assert type(new["value"]) is int and new["value"] == 0
    assert len(new["records"]) == 2
    editor = project.adaptation_node_editor(person, cut=first)
    assert editor["at_cut"] and editor["cut_name"] == "甲约好接乙"
    assert editor["later_count"] == 1
    assert editor["anchor_steps"] and editor["categories"]
    assert next(row for row in editor["properties"] if row["property_id"] == "开关")["records"][0]["is_current"]


def test_suppressed_nodes_are_manageable_but_never_contribute_effective_state(project):
    state = project.adaptation_snapshot()
    person = named(state, "甲")["id"]
    first = named(state, "甲约好接乙")["id"]
    project.suppress_adaptation_node(person)
    view = project.adaptation_browse_view()
    managed = project.adaptation_browse_view(include_suppressed=True)
    assert person not in {node["id"] for node in view["nodes"]}
    assert person in {node["id"] for node in managed["nodes"]}
    assert project.adaptation_node_editor(person)["node"]["suppressed"]
    assert project.adaptation_node_properties(person)["properties"] == []
    assert person not in {node["id"] for node in project.adaptation_node_properties(first)["involvement"]["people"]}
    assert not project.adaptation_node_detail(person)["related_nodes"]
    project.suppress_adaptation_node(first)
    assert first not in {node["id"] for node in project.adaptation_browse_view()["anchor_steps"]}
    with pytest.raises(ValueError, match="屏蔽"):
        project.adaptation_browse_view(cut_id=first)


def test_projections_and_readonly_asset_cards_never_create_or_write_project_state(project):
    state = project.adaptation_snapshot()
    person = named(state, "甲")["id"]
    first = named(state, "甲约好接乙")["id"]
    root = project._asset_root()
    source = project.graph_snapshot()
    assert not root.exists()
    for action in (lambda: project.adaptation_browse_view(), lambda: project.adaptation_node_properties(person, first),
                   lambda: project.adaptation_node_editor(person, first), lambda: project.adaptation_node_detail(person, first),
                   lambda: project.adaptation_node_assets(person, first)):
        action()
    assert not root.exists()
    assert project.adaptation_snapshot() == state and project.graph_snapshot() == source
    card = project.save_asset({"category": "appearance", "node_id": person, "title": "青衣", "valid_from": first})
    before = (root / "assets.sqlite3").read_bytes()
    material = project.adaptation_node_assets(person, first)
    assert material["readonly"] and material["domain"] == "adaptation"
    assert material["cards"][0]["id"] == card["id"]
    assert (root / "assets.sqlite3").read_bytes() == before
    assert project.adaptation_snapshot() == state and project.graph_snapshot() == source


def test_orphan_moment_picker_paths_are_complete_without_fake_source_positions(project):
    tree = project.adaptation_browse_view()["moments"]
    by_id = {row["id"]: row for row in tree["nodes"]}
    for row in tree["nodes"]:
        if row["rank"]:
            assert row["parent_id"] in by_id
            assert by_id[row["parent_id"]]["rank"] == row["rank"] - 1
    assert any(row["synthetic"] and row["level"] == "storyline" for row in tree["nodes"])
    assert all("disclosure" not in row for row in tree["nodes"])
