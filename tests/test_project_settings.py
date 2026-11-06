"""Granularity is strict project metadata; model settings keep their existing scope."""
from __future__ import annotations

import pytest

from narrative.harness import HarnessSettings
from narrative.operations import Operations


def test_project_granularity_and_model_configuration_are_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    a=Operations.create_graph_project(tmp_path/"a.nproj")
    b=Operations.create_graph_project(tmp_path/"b.nproj")
    before=b.load_project_settings()
    assert a.load_project_settings()["extraction_granularity"] == "standard"
    model=HarnessSettings(provider="custom",model="isolated-test",base_url="http://127.0.0.1:12345/v1").model_dump()
    saved=a.save_project_settings({"extraction_granularity":"fine","profile":model})
    assert saved == {"extraction_granularity":"fine","profile":model}
    assert Operations.open(tmp_path/"a.nproj").load_project_settings() == saved
    assert b.load_project_settings() == before
    assert a.graph_project_info()["revision"] == 0
    for bad in ("coarse", None, True, []):
        with pytest.raises(ValueError):
            a.save_project_settings({"extraction_granularity":bad,"profile":model})
        assert a.load_project_settings() == saved


def test_saving_only_granularity_keeps_global_model_inheritance(tmp_path, monkeypatch):
    from narrative.graph_profile_ops import save_global_settings
    from narrative.provider_store import ProviderStore

    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops=Operations.create_graph_project(tmp_path/"inherit.nproj")
    current=ops.load_project_settings()
    ops.save_project_settings(current | {"extraction_granularity":"fine"})
    providers=ProviderStore()
    assert providers.get(ops.graph_project_info()["project_id"], "harness_settings") is None
    changed=HarnessSettings.model_validate(current["profile"]).model_dump() | {"model":"changed-global-model"}
    save_global_settings(providers, changed)
    assert ops.provider_profile()["model"] == "changed-global-model"
    assert ops.load_project_settings()["extraction_granularity"] == "fine"
