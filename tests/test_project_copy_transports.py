"""Project duplication uses the same Operations contract through CLI and MCP."""
from __future__ import annotations

import json

import pytest

from narrative import mcp_server, tool_api, tool_cli
from narrative.assistant import ASSISTANT_TOOLS
from narrative.operations import Operations


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "源 工程.nproj"
    ops = Operations.create_graph_project(entry)
    ops.import_source("原文", "甲停在桥头。")
    return entry, ops


def test_cli_creates_an_independent_copy_without_a_current_project(source, tmp_path, capsys):
    entry, ops = source
    target = tmp_path / "副本 甲.nproj"
    assert tool_cli.main(["project-copy", str(entry), str(target), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "copied" and result["path"] == str(target)
    assert result["title"] == target.stem
    duplicate = Operations.open(target)
    assert duplicate.graph_project_info()["project_id"] != ops.graph_project_info()["project_id"]
    assert duplicate.sources() == ops.sources()
    duplicate.import_source("独立原文", "乙走到桥边。")
    assert len(ops.sources()) == 1 and len(duplicate.sources()) == 2


def test_mcp_workspace_copy_and_collision_report_without_overwriting(source, tmp_path):
    entry, ops = source
    target = tmp_path / "MCP副本.nproj"
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "duplicate_project", "arguments": {"source": str(entry), "destination": str(target)}}}
    result = mcp_server.handle(request, default_project=None)["result"]
    assert not result["isError"]
    assert json.loads(result["content"][0]["text"])["status"] == "copied"
    before = target.read_bytes()
    rejected = mcp_server.handle(request, default_project=None)["result"]
    assert rejected["isError"] and target.read_bytes() == before
    assert len(ops.sources()) == 1
    catalog = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, default_project=None)["result"]["tools"]
    tool = next(row for row in catalog if row["name"] == "duplicate_project")
    assert set(tool["inputSchema"]["required"]) == {"source", "destination"}
    assert "project" not in tool["inputSchema"]["properties"]
    assert "duplicate_project" not in ASSISTANT_TOOLS


@pytest.mark.parametrize("params", [{"source": "a.nproj"}, {"source": 3, "destination": "b.nproj"}])
def test_copy_parameters_are_validated_before_any_filesystem_write(params):
    with pytest.raises(tool_api.ToolError):
        tool_api.call("duplicate_project", params)
