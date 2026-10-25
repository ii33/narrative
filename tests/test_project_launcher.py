"""Launcher-level behaviour: project listing, global settings, legacy cleanup."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from narrative import project_catalog
from narrative.graph_profile_ops import global_settings, save_global_settings
from narrative.graph_store import GRAPH_SCHEMA_VERSION
from narrative.provider_store import ProviderStore


def _legacy_project(path: Path, version: int = 5, title: str = "旧工程") -> Path:
    # sqlite3.connect() as a context manager only commits; the handle must be
    # closed explicitly or Windows keeps the file locked against deletion.
    db = sqlite3.connect(path)
    try:
        db.execute(f"PRAGMA user_version={version}")
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO meta VALUES ('title', ?)", (title,))
        db.execute("INSERT INTO meta VALUES ('revision', '3')")
        db.commit()
    finally:
        db.close()
    return path


def test_project_status_follows_the_current_schema(tmp_path: Path) -> None:
    project_catalog.create_graph_project(tmp_path / "current.sqlite3")
    _legacy_project(tmp_path / "old.sqlite3")
    (tmp_path / "broken.sqlite3").write_bytes(b"not a database")

    rows = {item["name"]: item for item in project_catalog.list_projects(tmp_path)}

    assert rows["current"]["status"] == "ready"
    assert rows["current"]["version"] == GRAPH_SCHEMA_VERSION
    assert rows["old"]["status"] == "incompatible"
    assert rows["broken"]["status"] == "unreadable"


def test_clear_incompatible_removes_old_files_and_keeps_unreadable(tmp_path: Path) -> None:
    project_catalog.create_graph_project(tmp_path / "current.sqlite3")
    _legacy_project(tmp_path / "old.sqlite3")
    (tmp_path / "broken.sqlite3").write_bytes(b"not a database")

    result = project_catalog.clear_incompatible(tmp_path)

    cleared = result["removed"] + result["moved"]
    assert [Path(item["path"]).name for item in cleared] == ["old.sqlite3"]
    # An unreadable file may still be recoverable, so it is reported, not deleted.
    assert [Path(item["path"]).name for item in result["kept"]] == ["broken.sqlite3"]
    assert (tmp_path / "current.sqlite3").exists()
    assert not (tmp_path / "old.sqlite3").exists()


def test_clear_incompatible_leaves_a_directory_without_legacy_files_alone(
    tmp_path: Path,
) -> None:
    project_catalog.create_graph_project(tmp_path / "current.sqlite3")

    result = project_catalog.clear_incompatible(tmp_path)

    assert result["removed"] == []
    assert result["moved"] == []
    assert result["kept"] == []


def test_global_settings_become_the_default_for_new_projects(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "provider.sqlite3"))
    store = ProviderStore()
    save_global_settings(
        store,
        {
            "provider": "ark",
            "model": "ark-code-latest",
            "timeout_seconds": 600,
            "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        },
    )

    ops = project_catalog.create_graph_project(tmp_path / "fresh.sqlite3")

    profile = ops.provider_profile()
    assert profile["model"] == "ark-code-latest"
    assert profile["timeout_seconds"] == 600

    # A project-level save overrides the default without changing it.
    ops.save_provider_profile({"provider": "ark", "model": "only-this-project"})
    assert ops.provider_profile()["model"] == "only-this-project"
    assert global_settings(store)["model"] == "ark-code-latest"


def test_global_settings_survive_without_any_project(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "provider.sqlite3"))
    store = ProviderStore()

    assert global_settings(store)["provider"] == "ark"

    saved = save_global_settings(store, {"provider": "deepseek", "model": "deepseek-chat"})

    assert saved["model"] == "deepseek-chat"
    assert global_settings(store)["model"] == "deepseek-chat"
