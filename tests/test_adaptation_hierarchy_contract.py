"""Hierarchical selection contract shared by Operations, CLI and MCP."""
import json

from agent_fixtures import project as project

from narrative import mcp_server, tool_cli
from narrative.adaptation_import import candidates


def seed(ops):
    text = "甲开门。甲入屋。乙过桥。乙停步。"
    sid = ops.import_source("层级选择", text)
    nodes = []
    for index, (name, quote) in enumerate(zip(("开门", "入屋", "过桥", "停步"), ("甲开门。", "甲入屋。", "乙过桥。", "乙停步。"), strict=True)):
        branch = "屋内" if index < 2 else "桥上"
        if index in (0, 2):
            nodes.extend([{"type": "big_event", "name": branch, "quotes": [text[:8] if index == 0 else text[8:]]},
                          {"type": "scene", "name": branch + "场", "big_event": branch, "quotes": [text[:8] if index == 0 else text[8:]]}])
        nodes.append({"type": "event", "name": name, "scene": branch + "场", "quotes": [quote]})
    result = ops.submit_extraction(sid, {"nodes": nodes, "properties": []})
    assert result["status"] == "accepted", result
    ids = {n["name"]: n["id"] for n in ops.graph_snapshot()["nodes"]}
    assert ops.submit_extraction(sid, {"nodes": [{"type": "action", "name": "开门动作", "event": ids["开门"], "quotes": ["甲开门"]}], "properties": []})["status"] == "accepted"
    return {n["name"]: n["id"] for n in ops.graph_snapshot()["nodes"]}


def mcp(entry, name, arguments=None):
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}, default_project=str(entry))
    assert not reply["result"]["isError"], reply
    return json.loads(reply["result"]["content"][0]["text"])


def test_cli_candidates_mcp_cross_branch_import_preserves_real_hierarchy(project, capsys, tmp_path):
    ids = seed(project)
    original = project.graph_snapshot()
    entry = tmp_path / "unified.nproj"
    assert tool_cli.main(["adaptation-import-candidates", "--project", str(entry), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["moments"]
    assert rows == mcp(entry, "import_adaptation_candidates")["moments"]
    by_id = {r["id"]: r for r in rows}
    assert not any(r["node_type"] == "storyline" for r in rows)
    assert [r["name"] for r in rows if r["node_type"] == "event"] == ["开门", "入屋", "过桥", "停步"]
    assert by_id[ids["开门动作"]]["parent_id"] == ids["开门"]
    assert by_id[ids["开门动作"]]["depth"] == 3
    state = mcp(entry, "import_adaptation", {"moment_ids": [ids["屋内场"], ids["停步"]]})
    names = {n["name"] for n in state["nodes"]}
    assert names == {"屋内", "屋内场", "开门", "开门动作", "入屋", "桥上", "桥上场", "停步"}
    assert project.check_adaptation()["ok"]
    assert project.graph_snapshot() == original


def test_candidate_roots_use_earliest_member_not_completion_disclosure():
    def node(identifier, kind, start, children=()):
        return {"id": identifier, "name": identifier, "node_type": kind, "context_id": "canon", "disclosure": {"source_id": "s", "start": start}, "events": list(children)}
    nodes = [node("T9", "storyline", 90, ["B9"]), node("B9", "big_event", 0, ["S9"]), node("S9", "scene", 0, ["E9"]), node("E9", "event", 0), node("B1", "big_event", 100)]
    # A completed story's summary may have been disclosed after its first member.
    nodes[0]["disclosure"]["start"] = 190
    assert [r["id"] for r in candidates(nodes, {"s": 0}, 1)] == ["T9", "B9", "S9", "E9", "B1"]


def test_action_leaf_import_keeps_exact_event_parent_without_siblings(project):
    ids = seed(project)
    state = project.import_adaptation([ids["开门动作"]])
    nodes = {n["id"]: n for n in state["nodes"]}
    assert set(nodes) == {ids[name] for name in ("屋内", "屋内场", "开门", "开门动作")}
    assert nodes[ids["开门"]]["events"] == [ids["开门动作"]]
    assert nodes[ids["开门动作"]].get("events", []) == []
    assert project.check_adaptation()["ok"]
