"""Isolated adaptation edits, synchronization, ordering and interval checks."""
import copy

import pytest
from agent_fixtures import TEXT, draft_result
from agent_fixtures import project as project


def seed(project):
    sid = project.import_source("合成", TEXT)
    assert project.submit_extraction(sid, draft_result())["status"] == "accepted"
    return project.sync_adaptation()


def named(state, name):
    return next(n for n in state["nodes"] if n["name"] == name)


def test_workspace_is_independent_and_persistent(project):
    state = seed(project)
    before = copy.deepcopy(project.graph_snapshot())
    node = named(state, "甲约好接乙")
    state = project.edit_adaptation_node(node["id"], {"name": "改为送别"}, expected_revision=state["revision"])
    state = project.copy_adaptation_node(node["id"])
    clone = next(n for n in state["nodes"] if n["id"].startswith("adapt-event-"))
    state = project.move_adaptation_node(clone["id"], after_id=None)
    assert state["nodes"][0]["id"] == clone["id"]
    state = project.suppress_adaptation_node(node["id"])
    assert next(n for n in state["nodes"] if n["id"] == node["id"])["suppressed"]
    assert project.graph_snapshot() == before
    assert project.check_adaptation()["ok"]
    from narrative.graph_operations import GraphOperations
    assert GraphOperations(project.project_info().path).adaptation_snapshot() == project.adaptation_snapshot()


def test_sync_preserves_local_fields_and_reports_conflicts(project):
    state = seed(project)
    node = named(state, "甲")
    project.edit_adaptation_node(node["id"], {"name": "改编甲"})
    original = next(n for n in project.graph_snapshot()["nodes"] if n["id"] == node["id"])
    original["name"] = "提取甲"
    result = project.apply_changes([original], [], project.graph_project_info()["revision"], reason="合成勘误")
    assert result["status"] == "accepted"
    state = project.sync_adaptation()
    assert named(state, "改编甲")["id"] == node["id"]
    assert any(c["id"] == node["id"] and c["field"] == "name" for c in state["conflicts"])
    repeated = project.sync_adaptation()
    assert repeated["conflicts"] == state["conflicts"]
    assert repeated["conflicts"][0]["local"] == "改编甲"
    assert repeated["conflicts"][0]["source"] == "提取甲"


def test_properties_and_concurrency_are_atomic(project):
    state = seed(project)
    person = named(state, "甲")["id"]
    first = named(state, "甲约好接乙")["id"]
    last = named(state, "甲接到乙后离开")["id"]
    state = project.set_adaptation_property(person, "服装", "青衣", event_id=first)
    record = next(p for p in state["properties"] if p.get("property_id") == "服装")
    state = project.set_adaptation_property(person, "服装", "白衣", event_id=last, record_id=record["id"])
    assert next(p for p in state["properties"] if p["id"] == record["id"])["end_event_id"] == last
    before = project.adaptation_snapshot()
    with pytest.raises(ValueError, match="修订"):
        project.edit_adaptation_node(person, {"name": "过期写入"}, expected_revision=0)
    with pytest.raises(ValueError, match="失效时点"):
        project.set_adaptation_property(person, "服装", event_id=first, record_id=record["id"], expire=True)
    with pytest.raises(ValueError):
        project.edit_adaptation_node(person, {"participants": ["missing"]})
    assert project.adaptation_snapshot() == before


def test_insert_and_copy_subtree_use_unique_order_ids(project):
    state = seed(project)
    scene = named(state, "甲约定并完成接乙")
    state = project.edit_adaptation_node(changes={"name": "新场景", "node_type": "scene"})
    target = named(state, "新场景")
    state = project.edit_adaptation_node(changes={"name": "新事件", "node_type": "event"}, parent_id=target["id"])
    assert project.check_adaptation()["ok"]
    state = project.copy_adaptation_node(scene["id"])
    ids = [n["sort_id"] for n in state["nodes"]]
    assert len(set(ids)) == len(ids)
    assert project.check_adaptation()["ok"]


def test_invalid_structure_items_and_values_cannot_leak_partial_edits(project):
    state = seed(project)
    before = project.adaptation_snapshot()
    with pytest.raises(ValueError, match="归属场景"):
        project.edit_adaptation_node(changes={"node_type": "event", "name": "孤立事件"})
    with pytest.raises(ValueError, match="持有者"):
        project.edit_adaptation_node(changes={"node_type": "item", "name": "刀"})
    assert project.adaptation_snapshot() == before
    state = project.edit_adaptation_node(changes={"node_type": "item", "name": "刀", "initial_holder": "未知"})
    item = named(state, "刀")
    before = project.adaptation_snapshot()
    with pytest.raises(ValueError, match="持有者"):
        project.set_adaptation_property(item["id"], "持有者", "missing")
    assert project.adaptation_snapshot() == before
    person = named(state, "甲")
    event = named(state, "甲约好接乙")
    with pytest.raises(ValueError, match="时点"):
        project.edit_adaptation_node(event["id"], {"point_id": person["id"]})
    with pytest.raises(ValueError):
        project.edit_adaptation_node(event["id"], {"events": [event["id"]]})
    assert project.adaptation_snapshot() == before

def test_incremental_sync_merges_new_children_with_local_children(project):
    state = seed(project)
    scene = named(state, "甲约定并完成接乙")
    state = project.edit_adaptation_node(changes={"node_type": "event", "name": "改编插入"}, parent_id=scene["id"])
    local = named(state, "改编插入")
    source = project.graph_snapshot()
    source_scene = next(n for n in source["nodes"] if n["id"] == scene["id"])
    source_event = copy.deepcopy(next(n for n in source["nodes"] if n["node_type"] == "event"))
    source_event.update(id="E-new-sync", name="提取新增事件")
    source_scene["events"].append(source_event["id"])
    result = project.apply_changes([source_scene, source_event], [], project.graph_project_info()["revision"], reason="合成增量")
    assert result["status"] == "accepted"
    state = project.sync_adaptation()
    adapted_scene = named(state, "甲约定并完成接乙")
    assert local["id"] in adapted_scene["events"]
    assert "E-new-sync" in adapted_scene["events"]
    assert project.check_adaptation()["ok"]

def test_suppress_and_restore_container_cover_subtree(project):
    state = seed(project)
    scene = named(state, "甲约定并完成接乙")
    state = project.suppress_adaptation_node(scene["id"])
    subtree = {scene["id"], *scene["events"]}
    assert all(n["suppressed"] for n in state["nodes"] if n["id"] in subtree)
    assert project.check_adaptation()["ok"]
    state = project.suppress_adaptation_node(scene["id"], False)
    assert all(not n["suppressed"] for n in state["nodes"] if n["id"] in subtree)
    assert project.check_adaptation()["ok"]

def test_source_expiration_uses_adaptation_order_after_sync(project):
    state = seed(project)
    first = named(state, "甲约好接乙")
    last = named(state, "甲接到乙后离开")
    expired = next(p for p in state["properties"] if p.get("property_id") == "计划")
    assert expired["event_id"] == first["id"]
    assert expired["end_event_id"] == last["id"]
    assert expired["source_valid_to"]
    assert "valid_to" not in expired
    before = project.adaptation_snapshot()
    with pytest.raises(ValueError, match="失效时点"):
        project.move_adaptation_node(first["id"], after_id=last["id"])
    assert project.adaptation_snapshot() == before


def test_source_boundary_without_event_anchor_is_mapped_with_diagnostic(project):
    seed(project)
    row = copy.deepcopy(next(p for p in project.property_records() if p.get("property_id") == "计划"))
    row["valid_to"].pop("event_id", None)
    result = project.apply_changes([], [row], project.graph_project_info()["revision"], reason="合成坐标失效边界")
    assert result["status"] == "accepted"
    state = project.sync_adaptation()
    last = named(state, "甲接到乙后离开")
    expired = next(p for p in state["properties"] if p["id"] == row["id"])
    assert expired["end_event_id"] == last["id"]
    assert any(d["field"] == "valid_to" and d["event_id"] == last["id"] for d in state["sync_diagnostics"])
    assert project.sync_adaptation()["properties"] == state["properties"]

def test_insert_without_reference_is_first_and_explicit_last_reference_is_last(project):
    state = seed(project)
    state = project.edit_adaptation_node(changes={"node_type": "person", "name": "开头新增"})
    assert state["nodes"][0]["name"] == "开头新增"
    previous_last = state["nodes"][-1]["id"]
    state = project.edit_adaptation_node(changes={"node_type": "person", "name": "末尾新增"}, after_id=previous_last)
    assert state["nodes"][-1]["name"] == "末尾新增"
    ranks = [n["sort_id"] for n in state["nodes"]]
    assert ranks == sorted(set(ranks))
