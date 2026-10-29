"""MCP over stdio: the protocol envelope, not the tools themselves."""

from __future__ import annotations

import io
import json
from pathlib import Path

from narrative import mcp_server
from narrative.project_catalog import create_graph_project


def _project(path: Path) -> Path:
    ops = create_graph_project(path)
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "交代年龄",
                 "participants": ["1"]},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    return path


def test_initialize_answers_with_tools_capability() -> None:
    reply = mcp_server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}},
        default_project=None,
    )
    assert reply["id"] == 1
    assert reply["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in reply["result"]["capabilities"]
    assert reply["result"]["serverInfo"]["name"] == mcp_server.SERVER_NAME


def test_notifications_get_no_reply() -> None:
    assert mcp_server.handle(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, default_project=None
    ) is None


def test_tools_list_needs_a_project_argument_without_a_default() -> None:
    reply = mcp_server.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, default_project=None
    )
    tools = reply["result"]["tools"]
    assert any(tool["name"] == "list_nodes" for tool in tools)
    for tool in tools:
        assert "project" in tool["inputSchema"]["properties"]
        assert "project" in tool["inputSchema"]["required"]

    with_default = mcp_server.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        default_project="x.sqlite3",
    )
    assert all(
        "project" not in tool["inputSchema"]["properties"]
        for tool in with_default["result"]["tools"]
    )


def test_calling_a_tool_returns_text_content(tmp_path: Path) -> None:
    project = _project(tmp_path / "mcp.nproj")
    reply = mcp_server.handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "get_node", "arguments": {"node_id": "1"}}},
        default_project=str(project),
    )
    assert reply["result"]["isError"] is False
    payload = json.loads(reply["result"]["content"][0]["text"])
    assert payload["node"]["name"] == "林舟"


def test_an_unknown_tool_is_reported_not_crashed(tmp_path: Path) -> None:
    project = _project(tmp_path / "mcp-bad.nproj")
    reply = mcp_server.handle(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "no_such_tool", "arguments": {}}},
        default_project=str(project),
    )
    assert reply["result"]["isError"] is True
    assert "no_such_tool" in reply["result"]["content"][0]["text"]


def test_unknown_method_is_a_jsonrpc_error() -> None:
    reply = mcp_server.handle(
        {"jsonrpc": "2.0", "id": 6, "method": "nope"}, default_project=None
    )
    assert reply["error"]["code"] == -32601


def test_stdio_round_trip(tmp_path: Path) -> None:
    project = _project(tmp_path / "mcp-io.nproj")
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "project_info", "arguments": {}}},
    ]
    stdin = io.StringIO("\n".join(json.dumps(line) for line in lines) + "\n")
    stdout = io.StringIO()
    assert mcp_server.serve(default_project=str(project), stdin=stdin, stdout=stdout) == 0
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    # 通知不回包，所以是两条而不是三条。
    assert [reply["id"] for reply in replies] == [1, 2]
    payload = json.loads(replies[1]["result"]["content"][0]["text"])
    assert payload["project"]["revision"] >= 0
    assert payload["nodes"]["person"] == 1
