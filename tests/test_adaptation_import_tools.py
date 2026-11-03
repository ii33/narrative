"""Selected import and shared browse projections remain aligned across transports."""
import json

import pytest
from agent_fixtures import TEXT, draft_result

from narrative import mcp_server, tool_api, tool_cli
from narrative.operations import Operations


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "selected.nproj"
    ops = Operations.create_graph_project(entry)
    sid = ops.import_source("合成短文", TEXT)
    assert ops.submit_extraction(sid, draft_result())["status"] == "accepted"
    return entry, ops


def test_cli_selected_import_then_mcp_shared_browse(project, capsys):
    entry, ops = project
    before = ops.graph_snapshot()
    assert tool_cli.main(["adaptation-import-candidates", "--project", str(entry), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["moments"]
    chosen = next(n["id"] for n in rows if n["node_type"] == "event")
    assert tool_cli.main(["adaptation-import", "--moment", chosen, "--project", str(entry), "--json"]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert chosen in imported["import_summary"]["selected_moment_ids"]
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "adaptation_browse_view", "arguments": {"granularity": "event"}}}
    reply = mcp_server.handle(request, default_project=str(entry))
    assert not reply["result"]["isError"]
    view = json.loads(reply["result"]["content"][0]["text"])
    assert chosen in {n["id"] for n in view["nodes"]}
    assert {"nodes", "counts", "moments", "timeline", "steps", "revision"} <= view.keys()
    editor = tool_api.call("adaptation_node_editor", {"node_id": chosen}, project=entry)
    assert editor["node"]["id"] == chosen
    assert ops.graph_snapshot() == before


def test_invalid_selection_does_not_mutate_workspace(project):
    entry, ops = project
    before = ops.adaptation_snapshot()
    for ids in ([], ["missing"], "E1", [False]):
        with pytest.raises(tool_api.ToolError):
            tool_api.call("import_adaptation", {"moment_ids": ids}, project=entry)
        assert ops.adaptation_snapshot() == before
