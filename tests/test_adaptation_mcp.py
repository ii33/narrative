"""Real JSON-RPC stdio serialization for adaptation and asset MCP tools."""
from __future__ import annotations

import copy
import io
import json

import pytest
from agent_fixtures import TEXT, draft_result

from narrative import mcp_server, tool_api
from narrative.operations import Operations

TOOLS = {
    "adaptation_snapshot", "check_adaptation", "sync_adaptation", "edit_adaptation_node",
    "copy_adaptation_node", "move_adaptation_node", "suppress_adaptation_node",
    "set_adaptation_property", "list_assets", "get_asset", "save_asset", "delete_asset", "asset_moments",
}


def rpc(method, params=None, *, default_project=None):
    request = {"jsonrpc": "2.0", "id": 17, "method": method, "params": params or {}}
    output = io.StringIO()
    assert mcp_server.serve(default_project=default_project,
                            stdin=io.StringIO(json.dumps(request, ensure_ascii=False) + "\n"),
                            stdout=output) == 0
    lines = output.getvalue().splitlines()
    assert len(lines) == 1
    response = json.loads(lines[0])
    assert response["jsonrpc"] == "2.0" and response["id"] == 17
    assert "error" not in response, response
    return response["result"]


def call(entry, name, arguments=None, *, error=False):
    result = rpc("tools/call", {"name": name, "arguments": {"project": str(entry), **(arguments or {})}})
    assert result["isError"] is error, result
    assert result["content"][0]["type"] == "text"
    text = result["content"][0]["text"]
    return text if error else json.loads(text)


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "无默认工程 空格.nproj"
    ops = Operations.create_graph_project(entry)
    sid = ops.import_source("合成短文", TEXT)
    assert ops.submit_extraction(sid, draft_result())["status"] == "accepted"
    return entry, ops


def named(state, name):
    return next(node for node in state["nodes"] if node["name"] == name)


def test_stdio_catalog_schemas_match_public_tool_api_and_project_entry(seeded):
    entry, _ops = seeded
    assert len(TOOLS) == 13
    direct = {tool["name"]: tool for tool in tool_api.describe()}
    default = {tool["name"]: tool for tool in rpc("tools/list", default_project=str(entry))["tools"]}
    explicit = {tool["name"]: tool for tool in rpc("tools/list")["tools"]}
    assert TOOLS <= default.keys() and TOOLS <= explicit.keys()
    for name in TOOLS:
        assert default[name] == direct[name]
        schema = copy.deepcopy(explicit[name]["inputSchema"])
        assert schema["properties"].pop("project")["type"] == "string"
        schema["required"].remove("project")
        assert schema == direct[name]["inputSchema"]
    assert ".nproj" in explicit["sync_adaptation"]["inputSchema"]["properties"]["project"]["description"]
    missing = rpc("tools/call", {"name": "adaptation_snapshot", "arguments": {}})
    assert missing["isError"] and ".nproj" in missing["content"][0]["text"]
    assert call(entry, "adaptation_snapshot")["nodes"] == []


def test_stdio_adaptation_edit_copy_move_false_values_and_atomic_conflict(seeded):
    entry, ops = seeded
    before = ops.graph_snapshot()
    state = call(entry, "sync_adaptation", {"expected_revision": 0})
    scene = named(state, "甲约定并完成接乙")["id"]
    first = named(state, "甲约好接乙")["id"]
    last = named(state, "甲接到乙后离开")["id"]
    state = call(entry, "edit_adaptation_node", {"changes": {"node_type": "person", "name": "新角色"},
                                                "expected_revision": state["revision"]})
    person = named(state, "新角色")["id"]
    state = call(entry, "edit_adaptation_node", {"node_id": person, "changes": {"name": "改编角色"}})
    state = call(entry, "edit_adaptation_node", {"changes": {"node_type": "event", "name": "插入事件"}, "parent_id": scene})
    inserted = named(state, "插入事件")["id"]
    previous = {node["id"] for node in state["nodes"]}
    state = call(entry, "copy_adaptation_node", {"node_id": inserted, "parent_id": scene})
    clone = next(node["id"] for node in state["nodes"] if node["id"] not in previous)
    state = call(entry, "move_adaptation_node", {"node_id": clone})
    assert state["nodes"][0]["id"] == clone
    state = call(entry, "suppress_adaptation_node", {"node_id": clone, "suppressed": True})
    assert next(node for node in state["nodes"] if node["id"] == clone)["suppressed"] is True
    state = call(entry, "suppress_adaptation_node", {"node_id": clone, "suppressed": False})
    assert next(node for node in state["nodes"] if node["id"] == clone)["suppressed"] is False
    for property_id, value in (("开关", False), ("数量", 0)):
        state = call(entry, "set_adaptation_property", {"node_id": person, "property_id": property_id,
                                                       "value": value, "event_id": first, "op": "set"})
        row = next(row for row in state["properties"] if row.get("property_id") == property_id)
        assert row["value"] == value and type(row["value"]) is type(value)
        state = call(entry, "set_adaptation_property", {"node_id": person, "property_id": property_id,
                                                       "event_id": last, "op": "clear"})
        assert next(row2 for row2 in state["properties"] if row2["id"] == row["id"])["value"] == value
        assert any(row2.get("op")=="clear" and row2["event_id"]==last for row2 in state["properties"] if row2.get("property_id")==property_id)
    assert call(entry, "check_adaptation")["ok"]
    snapshot = call(entry, "adaptation_snapshot")
    message = call(entry, "edit_adaptation_node", {"node_id": person, "changes": {"name": "不能写入"},
                                                   "expected_revision": 0}, error=True)
    assert "修订" in message
    assert call(entry, "adaptation_snapshot") == snapshot
    assert ops.graph_snapshot() == before


def test_stdio_assets_crud_revisions_and_extraction_isolation(seeded):
    entry, ops = seeded
    before = ops.graph_snapshot()
    state = call(entry, "sync_adaptation")
    person = named(state, "甲")["id"]
    first = named(state, "甲约好接乙")["id"]
    assert first in {node["id"] for node in call(entry, "asset_moments")["moments"]}
    card = call(entry, "save_asset", {"payload": {"category": "appearance", "node_id": person,
                                                 "title": "青衣形象", "valid_from": first, "prompt": "青色长衫"},
                                      "expected_revision": 0})
    assert call(entry, "get_asset", {"asset_id": card["id"]})["prompt"] == "青色长衫"
    assert call(entry, "list_assets", {"category": "appearance", "node_id": person, "moment": first})["assets"][0]["id"] == card["id"]
    message = call(entry, "save_asset", {"payload": {"id": card["id"], "title": "过期修改"},
                                        "expected_revision": 0}, error=True)
    assert "改变" in message or "修订" in message
    assert call(entry, "get_asset", {"asset_id": card["id"]}) == card
    updated = call(entry, "save_asset", {"payload": {"id": card["id"], "title": "白衣形象"},
                                        "expected_revision": card["revision"]})
    assert updated["title"] == "白衣形象"
    message = call(entry, "delete_asset", {"asset_id": card["id"], "expected_revision": 0}, error=True)
    assert "改变" in message or "修订" in message
    assert call(entry, "get_asset", {"asset_id": card["id"]}) == updated
    call(entry, "delete_asset", {"asset_id": card["id"], "expected_revision": updated["revision"]})
    assert call(entry, "list_assets", {"category": "appearance"})["assets"] == []
    assert ops.graph_snapshot() == before


def test_stdio_incremental_sync_preserves_local_edits_and_reads_new_extraction(seeded):
    entry, ops = seeded
    state = call(entry, "sync_adaptation")
    person = named(state, "甲")["id"]
    call(entry, "edit_adaptation_node", {"node_id": person, "changes": {"name": "改编甲"}})
    source = ops.graph_snapshot()
    node = next(node for node in source["nodes"] if node["id"] == person)
    added = copy.deepcopy(node)
    added.update(id="P-sync-added", name="提取新增角色")
    node["name"] = "原文勘误甲"
    assert ops.apply_changes([node, added], [], ops.graph_project_info()["revision"], reason="合成增量勘误")["status"] == "accepted"
    before = ops.graph_snapshot()
    state = call(entry, "sync_adaptation")
    assert named(state, "改编甲")["id"] == person
    assert named(state, "提取新增角色")["id"] == "P-sync-added"
    assert state["source_revision"] == ops.graph_project_info()["revision"]
    assert any(row["id"] == person and row["field"] == "name" for row in state["conflicts"])
    assert call(entry, "sync_adaptation")["conflicts"] == state["conflicts"]
    assert ops.graph_snapshot() == before


def test_malformed_arguments_return_protocol_error_and_global_tools_need_no_project():
    for arguments in ([1], "bad", False):
        output = io.StringIO()
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "sync_adaptation", "arguments": arguments}}
        mcp_server.serve(default_project=None, stdin=io.StringIO(json.dumps(request) + "\n"), stdout=output)
        assert json.loads(output.getvalue())["error"]["code"] == -32602
    catalog = {t["name"]: t for t in rpc("tools/list")["tools"]}
    assert "project" not in catalog["list_projects"]["inputSchema"]["required"]


def test_process_stdio_write_is_visible_through_cli(seeded):
    import os
    import subprocess
    import sys

    entry, ops = seeded
    before = ops.graph_snapshot()
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "save_asset", "arguments": {"payload": {"category": "material", "title": "跨入口木纹", "prompt": "柔和日光"}}}},
    ]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    mcp = subprocess.run([sys.executable, "-m", "narrative.mcp_server", "--project", str(entry)],
                         input="".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests),
                         capture_output=True, encoding="utf-8", env=env, timeout=20, check=True)
    replies = [json.loads(line) for line in mcp.stdout.splitlines()]
    assert len(replies) == 2 and not replies[1]["result"]["isError"]
    asset_id = json.loads(replies[1]["result"]["content"][0]["text"])["id"]
    cli = subprocess.run([sys.executable, "-m", "narrative.cli", "asset", asset_id,
                          "--project", str(entry), "--json"], capture_output=True,
                         encoding="utf-8", env=env, timeout=20, check=True)
    assert json.loads(cli.stdout)["title"] == "跨入口木纹"
    assert ops.graph_snapshot() == before


def test_extraction_granularity_matches_ui_settings_without_model_change(seeded):
    entry, ops = seeded
    before = ops.graph_snapshot()
    profile = ops.load_project_settings()["profile"]
    assert call(entry, "get_extraction_settings")["extraction_granularity"] == "fine"
    assert call(entry, "set_extraction_granularity", {"granularity": "standard"}) == {"extraction_granularity": "standard"}
    assert ops.load_project_settings()["profile"] == profile
    assert ops.graph_snapshot() == before
    assert "粒度" in call(entry, "set_extraction_granularity", {"granularity": False}, error=True)
