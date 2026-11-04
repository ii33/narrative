"""Physical local timeline CRUD preserves authored work and source isolation."""
import copy

import pytest
from agent_fixtures import project as project
from test_screenplay_append import write_body
from test_screenplay_core import seed
from test_screenplay_timeline import create, import_points

from narrative.screenplay_store import read_context


def call(ops, script, name, **args):
    return getattr(ops, name)(script, expected_timeline_revision=ops.read_screenplay_timeline(script)["revision"], reason="本地时间点操作", **args)


def prepare(ops):
    ids = seed(ops)
    script = create(ops)
    import_points(ops, script, [ids["入屋"]])
    return ids, script


def test_local_create_edit_root_move_copy_and_stale_are_isolated(project):
    ids, script = prepare(project)
    other = create(project, "另案")
    import_points(project, other, [ids["入屋"]])
    source, original, unrelated = copy.deepcopy(project.adaptation_snapshot()), copy.deepcopy(project.graph_snapshot()), copy.deepcopy(project.read_screenplay_timeline(other))
    added = call(project, script, "edit_screenplay_timeline_node", changes={"node_type": "scene", "name": "新增场"}, parent_id=ids["入屋"])
    scene = added["created_node_id"]
    event = call(project, script, "edit_screenplay_timeline_node", changes={"node_type": "event", "name": "新事件"}, parent_id=scene)["created_node_id"]
    call(project, script, "edit_screenplay_timeline_node", node_id=event, changes={"name": "已改名"})
    rooted = call(project, script, "move_screenplay_timeline_node", node_id=scene, after_id=ids["甲坐下"], parent_id="")
    assert not any(scene in n.get("events", []) for n in rooted["nodes"])
    cloned = call(project, script, "copy_screenplay_timeline_node", node_id=scene)
    assert len(cloned["copied_node_ids"]) == 2
    copied = next(n for n in cloned["nodes"] if n["id"] == cloned["copied_node_id"])
    assert copied["events"] and copied["events"][0] != event
    before = copy.deepcopy(project.read_screenplay_timeline(script))
    with pytest.raises(ValueError, match="revision"):
        project.delete_screenplay_timeline_node(script, scene, 0, "旧修订")
    assert project.read_screenplay_timeline(script) == before
    assert project.adaptation_snapshot() == source and project.graph_snapshot() == original
    assert project.read_screenplay_timeline(other) == unrelated


def test_container_move_and_copy_preserve_authored_descendant_order(project):
    ids, script = prepare(project)
    call(project, script, "move_screenplay_timeline_node", node_id=ids["甲敲门"], after_id=ids["甲坐下"])
    before = project.read_screenplay_timeline(script)
    names = [n["name"] for n in before["nodes"] if n["node_type"] == "event"]
    moved = call(project, script, "move_screenplay_timeline_node", node_id=ids["门口与屋内"])
    assert [n["name"] for n in moved["nodes"] if n["node_type"] == "event"] == names
    copied = call(project, script, "copy_screenplay_timeline_node", node_id=ids["门口与屋内"])
    original_ids = {ids["门口与屋内"], *(n["id"] for n in moved["nodes"] if n["node_type"] == "event")}
    ordered_ids = [node["id"] for node in copied["nodes"]]
    assert max(ordered_ids.index(identifier) for identifier in original_ids) < min(ordered_ids.index(identifier) for identifier in copied["copied_node_ids"])
    assert [n["name"] for n in copied["nodes"] if n["node_type"] == "event"] == names + names
    assert [n["name"] for n in copied["nodes"] if n["id"] in copied["copied_node_ids"] and n["node_type"] == "event"] == names


def test_delete_removes_subtree_and_baselines_but_keeps_invalid_span_body(project):
    ids, script = prepare(project)
    segment, unit, span = write_body(project, script, ids["甲开门"], ids["甲"])
    body = copy.deepcopy(project.read_script_segment(segment)["units"][0]["blocks"])
    result = call(project, script, "delete_screenplay_timeline_node", node_id=ids["门口与屋内"])
    assert ids["甲开门"] in result["deleted_node_ids"]
    assert all(n["id"] not in result["deleted_node_ids"] for n in result["nodes"])
    assert project.read_script_segment(segment)["units"][0]["blocks"] == body
    assert project.read_adaptation_span(span)["status"] == "invalid"
    assert result["affected_spans"][0]["span_id"] == span
    state, _ = read_context(project)
    local = state["script_timelines"][result["timeline_id"]]
    assert not set(result["deleted_node_ids"]) & set(local["baseline_nodes"])


def test_delete_end_reopens_prior_value_and_clears_deleted_start_state(project):
    ids, script = prepare(project)
    first = call(project, script, "set_screenplay_timeline_property", node_id=ids["甲"], property_id="服装", value="青衣", event_id=ids["甲敲门"])
    row = next(r for r in first["properties"] if r.get("property_id") == "服装")
    call(project, script, "set_screenplay_timeline_property", node_id=ids["甲"], property_id="服装", value="白衣", event_id=ids["甲开门"], record_id=row["id"])
    result = call(project, script, "delete_screenplay_timeline_node", node_id=ids["甲开门"])
    remaining = [r for r in result["properties"] if r.get("property_id") == "服装"]
    assert len(remaining) == 1 and remaining[0]["value"] == "青衣" and remaining[0]["end_event_id"] is None
    assert result["opened_property_ids"] == [row["id"]]
    assert len(result["deleted_property_ids"]) == 1


def test_delete_rejects_newly_opened_single_value_overlap_atomically(project):
    ids, script = prepare(project)
    first = call(project, script, "set_screenplay_timeline_property", node_id=ids["甲"], property_id="服装", value="青衣", event_id=ids["甲敲门"])
    row = next(r for r in first["properties"] if r.get("property_id") == "服装")
    call(project, script, "set_screenplay_timeline_property", node_id=ids["甲"], property_id="服装", event_id=ids["甲开门"], record_id=row["id"], expire=True)
    call(project, script, "set_screenplay_timeline_property", node_id=ids["甲"], property_id="服装", value="白衣", event_id=ids["甲坐下"])
    before = copy.deepcopy(project.read_screenplay_timeline(script))
    with pytest.raises(ValueError, match="服装.*区间重叠"):
        call(project, script, "delete_screenplay_timeline_node", node_id=ids["甲开门"])
    assert project.read_screenplay_timeline(script) == before


def test_referenced_entity_delete_is_atomic_and_keeps_body(project):
    ids, script = prepare(project)
    write_body(project, script, ids["甲开门"], ids["甲"])
    before = copy.deepcopy(read_context(project))
    with pytest.raises(ValueError, match="仍被剧本引用"):
        call(project, script, "delete_screenplay_timeline_node", node_id=ids["甲"])
    assert read_context(project) == before


def test_delete_selected_event_prunes_unneeded_external_state_anchors(project):
    from test_adaptation_import import setup

    ids = setup(project)
    project.sync_adaptation()
    script = create(project)
    first = import_points(project, script, [ids["事件1"]])
    assert first["state_anchors"]
    result = call(project, script, "delete_screenplay_timeline_node", node_id=ids["事件1"])
    # Entity-owned state remains valid independently; only the event's own state is removed.
    surviving_properties = {r["id"] for r in result["properties"]}
    state, _ = read_context(project)
    local = state["script_timelines"][result["timeline_id"]]
    assert set(local["baseline_properties"]) <= surviving_properties
    assert all(ids["事件1"] not in a["preceding_ids"] + a["following_ids"] for a in result["state_anchors"])


def test_cli_create_and_mcp_delete_share_local_operations(project, tmp_path, capsys):
    import json

    from narrative import mcp_server, tool_cli

    ids, script = prepare(project)
    entry = tmp_path / "unified.nproj"
    params = {"screenplay_id": script, "changes": {"node_type": "scene", "name": "工具新场"},
              "parent_id": "", "expected_timeline_revision": project.read_screenplay_timeline(script)["revision"], "reason": "CLI新建"}
    assert tool_cli.main(["tool", "call", "edit_screenplay_timeline_node", json.dumps(params), "--project", str(entry), "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "delete_screenplay_timeline_node", "arguments": {
        "screenplay_id": script, "node_id": created["created_node_id"], "expected_timeline_revision": created["revision"], "reason": "MCP删除"}}}, default_project=str(entry))
    assert not reply["result"]["isError"], reply
    deleted = json.loads(reply["result"]["content"][0]["text"])
    assert deleted["deleted_node_ids"] == [created["created_node_id"]]
    assert ids["甲开门"] in {node["id"] for node in deleted["nodes"]}
