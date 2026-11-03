"""Offline validation of the storage acceptance fixture and evidence collector."""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

from narrative import tool_api
from narrative.operations import Operations
from narrative.project_package import layout


def _module(monkeypatch):
    scripts = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("accept_storage_test", scripts / "accept_screenplay_storage.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_storage_acceptance_fixture_and_resolver_do_not_repair_missing_model_result(tmp_path, monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "fixture.nproj")
    fixture = module.seed(ops)
    before = ops._store.path.read_bytes()
    assert fixture["storage_before"]["missing_tables"] == ["script_timelines"]
    assert fixture["storage_before"]["row_counts"]["script_segments"] == 0
    with pytest.raises(ValueError):
        module.resolve_fixture(ops, fixture)
    assert ops._store.path.read_bytes() == before
    assert inspect.signature(module.qa.main).parameters["fixture_resolver"].default is None


def test_storage_acceptance_collector_accepts_only_actual_current_operations_result(tmp_path, monkeypatch):
    module = _module(monkeypatch)
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "fixture.nproj")
    fixture = module.seed(ops)
    entry = layout(ops.project_info().path).entry
    report = tool_api.call("inspect_screenplay_storage", {}, project=entry)
    tool_api.call("reset_screenplay_storage", {"reason": "离线测试明确重建", "scope": "screenplay",
        "expected_storage_fingerprint": report["expected_storage_fingerprint"], "confirm_discard": True}, project=entry)
    tool_api.call("create_screenplay", {"title": module.TARGET_TITLE, "format": "series", "reason": "离线新建"}, project=entry)
    resolved = module.resolve_fixture(ops, fixture)
    result = {}
    checks = module.validate(ops, resolved, ops.read_screenplay(resolved["screenplay_id"]), [], result)
    assert all(checks.values())
    assert result["storage_evidence"]["storage_after"]["status"] == "ready"
