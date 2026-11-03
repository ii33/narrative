"""Human CLI aliases share Operations tooling without losing JSON or false values."""
from __future__ import annotations

import json

import pytest

from narrative import tool_cli
from narrative.operations import Operations


@pytest.mark.parametrize(("argv", "tool", "params"), [
    (["adaptation"], "adaptation_snapshot", {}),
    (["adaptation-check"], "check_adaptation", {}),
    (["adaptation-sync", "--expected-revision", "0", "--reason", "同步"], "sync_adaptation", {"expected_revision": 0, "reason": "同步"}),
    (["adaptation-edit", "--changes", '{"name":"角色","node_type":"person"}'], "edit_adaptation_node", {"changes": {"name": "角色", "node_type": "person"}}),
    (["adaptation-copy", "E1", "--after", "E2"], "copy_adaptation_node", {"node_id": "E1", "after_id": "E2"}),
    (["adaptation-move", "E1", "--parent", "S1"], "move_adaptation_node", {"node_id": "E1", "parent_id": "S1"}),
    (["adaptation-suppress", "E1", "--suppressed", "false"], "suppress_adaptation_node", {"node_id": "E1", "suppressed": False}),
    (["adaptation-property", "P1", "存活", "--value", "false", "--expire", "false"], "set_adaptation_property", {"node_id": "P1", "property_id": "存活", "value": False, "expire": False}),
    (["assets", "appearance", "--node", "P1", "--moment", "E1"], "list_assets", {"category": "appearance", "node_id": "P1", "moment": "E1"}),
    (["asset", "A1"], "get_asset", {"asset_id": "A1"}),
    (["asset-save", "--payload", '{"category":"material","title":"布"}'], "save_asset", {"payload": {"category": "material", "title": "布"}}),
    (["asset-delete", "A1", "--expected-revision", "0"], "delete_asset", {"asset_id": "A1", "expected_revision": 0}),
    (["asset-moments", "--domain", "adaptation"], "asset_moments", {"domain": "adaptation"}),
])
def test_aliases_bind_exact_tool_parameters(argv, tool, params, monkeypatch, capsys):
    seen = []

    def call(name, arguments, *, project=None):
        seen.append((name, arguments, project))
        return {"ok": True}

    monkeypatch.setattr(tool_cli.tool_api, "call", call)
    assert tool_cli.main(argv + ["--project", "example.nproj", "--json"]) == 0
    assert seen == [(tool, params, "example.nproj")]
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_json_files_and_real_independent_edit_lifecycle(tmp_path, capsys):
    entry = tmp_path / "cli.nproj"
    ops = Operations.create_graph_project(entry)
    changes = tmp_path / "changes.json"
    changes.write_text(json.dumps({"name": "角色", "node_type": "person"}, ensure_ascii=False), encoding="utf-8-sig")

    def run(*args):
        assert tool_cli.main([*args, "--project", str(entry), "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    run("adaptation-sync", "--expected-revision", "0")
    snap = run("adaptation-edit", "--changes-file", str(changes))
    identifier = snap["nodes"][0]["id"]
    second = run("adaptation-edit", "--changes", '{"node_type":"person","name":"另一角色"}', "--after", identifier)
    second_id = next(n["id"] for n in second["nodes"] if n["id"] != identifier)
    moved = run("adaptation-move", second_id, "--after", "", "--parent", "")
    assert moved["nodes"][0]["id"] == second_id
    run("adaptation-suppress", identifier)
    snap = run("adaptation-suppress", identifier, "--suppressed", "false")
    assert not next(n for n in snap["nodes"] if n["id"] == identifier)["suppressed"]
    value = tmp_path / "value.json"
    value.write_text("0", encoding="utf-8")
    snap = run("adaptation-property", identifier, "数量", "--value-file", str(value), "--expire", "false")
    assert next(p for p in snap["properties"] if p.get("node_id") == identifier)["value"] == 0
    payload = tmp_path / "asset.json"
    payload.write_text('{"category":"material","title":"丝绸","prompt":"柔光"}', encoding="utf-8")
    card = run("asset-save", "--payload-file", str(payload), "--expected-revision", "0")
    assert run("asset", card["id"])["prompt"] == "柔光"
    assert ops.graph_snapshot()["nodes"] == []


@pytest.mark.parametrize("argv", [
    ["adaptation-edit", "--changes", "{"],
    ["asset-save", "--payload-file", "missing-file.json"],
    ["tool", "call", "adaptation_snapshot", "{"],
])
def test_bad_json_or_file_is_friendly_error_before_tool_call(argv, monkeypatch, capsys):
    monkeypatch.setattr(tool_cli.tool_api, "call", lambda *a, **k: pytest.fail("invalid input executed"))
    assert tool_cli.main(argv) == 2
    assert capsys.readouterr().err


def test_extraction_granularity_aliases_preserve_model_profile_and_graph(tmp_path, monkeypatch, capsys):
    from narrative.harness import HarnessSettings
    from narrative.provider_store import ProviderStore

    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "settings.nproj"
    ops = Operations.create_graph_project(entry)
    profile = HarnessSettings(provider="custom", model="cli-model", base_url="http://127.0.0.1:12345/v1").model_dump()
    ops.save_project_settings({"extraction_granularity": "fine", "profile": profile})
    graph = ops.graph_snapshot()
    project_id = ops.graph_project_info()["project_id"]
    stored_profile = ProviderStore().get(project_id, "harness_settings")

    def run(*args):
        assert tool_cli.main([*args, "--project", str(entry), "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    assert run("extraction-settings") == {"extraction_granularity": "fine"}
    for granularity in ("standard", "fine"):
        assert run("extraction-granularity", granularity) == {"extraction_granularity": granularity}
        assert run("extraction-settings") == {"extraction_granularity": granularity}
        assert ops.provider_profile() == profile
        assert ProviderStore().get(project_id, "harness_settings") == stored_profile
        assert ops.graph_snapshot() == graph
