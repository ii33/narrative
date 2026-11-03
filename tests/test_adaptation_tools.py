"""CLI and resident agent use the same independent Operations capabilities."""
from narrative import tool_api
from narrative.assistant import ASSISTANT_TOOLS
from narrative.operations import Operations


def test_tool_writes_are_isolated_and_catalogued(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "tools.nproj"
    ops = Operations.create_graph_project(entry)
    before = ops.graph_snapshot()
    result = tool_api.call("edit_adaptation_node", {"changes": {"node_type": "scene", "name": "新场景"}}, project=entry)
    assert result["status"] == "accepted"
    assert tool_api.call("check_adaptation", project=entry)["ok"]
    assert len(tool_api.call("asset_moments", project=entry)["moments"]) == 1
    card = tool_api.call("save_asset", {"payload": {"category": "material", "title": "木纹", "prompt": "细木纹"}}, project=entry)
    assert tool_api.call("get_asset", {"asset_id": card["id"]}, project=entry)["prompt"] == "细木纹"
    tool_api.call("delete_asset", {"asset_id": card["id"], "expected_revision": card["revision"]}, project=entry)
    assert tool_api.call("list_assets", {"category": "material"}, project=entry)["assets"] == []
    assert ops.graph_snapshot() == before
    for name in ("edit_adaptation_node", "sync_adaptation", "save_asset", "delete_asset"):
        assert tool_api.TOOLS[name].writes and name in ASSISTANT_TOOLS


def test_transport_schema_rejects_wrong_types_before_write(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "validation.nproj"
    ops = Operations.create_graph_project(entry)
    before = ops.adaptation_snapshot()
    for name, params in [
        ("sync_adaptation", {"expected_revision": False}),
        ("suppress_adaptation_node", {"node_id": "missing", "suppressed": "false"}),
        ("edit_adaptation_node", {"changes": []}),
        ("save_asset", {"payload": {"category": "material", "title": "铜", "reference_images": "file.png"}}),
        ("save_asset", {"payload": {"category": "material", "title": "铜", "typo": 1}}),
    ]:
        with pytest.raises(tool_api.ToolError):
            tool_api.call(name, params, project=entry)
        assert ops.adaptation_snapshot() == before
        assert ops.graph_snapshot()["revision"] == 0
    with pytest.raises(tool_api.ToolError, match="资产不存在"):
        tool_api.call("get_asset", {"asset_id": "missing"}, project=entry)
