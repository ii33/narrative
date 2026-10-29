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


def _legacy_package(root: Path, version: int = 5, title: str = "旧工程") -> Path:
    """一个旧版工程包：目录里有库，库是旧 schema。"""
    root.mkdir(parents=True, exist_ok=True)
    _legacy_project(root / "graph.sqlite3", version, title)
    return root


def _broken_package(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "graph.sqlite3").write_bytes(b"not a database")
    return root


def test_project_status_follows_the_current_schema(tmp_path: Path) -> None:
    project_catalog.create_graph_project(tmp_path / "current.nproj")
    _legacy_package(tmp_path / "old.nproj")
    _broken_package(tmp_path / "broken.nproj")

    rows = {item["name"]: item for item in project_catalog.list_projects(tmp_path)}

    assert rows["current"]["status"] == "ready"
    assert rows["current"]["version"] == GRAPH_SCHEMA_VERSION
    assert rows["old"]["status"] == "incompatible"
    assert rows["broken"]["status"] == "unreadable"


def test_clear_incompatible_moves_old_packages_and_keeps_unreadable(tmp_path: Path) -> None:
    project_catalog.create_graph_project(tmp_path / "current.nproj")
    _legacy_package(tmp_path / "old.nproj")
    _broken_package(tmp_path / "broken.nproj")

    result = project_catalog.clear_incompatible(tmp_path)

    cleared = result["removed"] + result["moved"]
    assert [Path(item["path"]).name for item in cleared] == ["old.nproj"]
    # An unreadable database may still be recoverable, so it is reported, not removed.
    assert [Path(item["path"]).name for item in result["kept"]] == ["broken.nproj"]
    assert (tmp_path / "current.nproj").exists()
    assert not (tmp_path / "old.nproj").exists()


def test_clear_incompatible_leaves_a_directory_without_legacy_files_alone(
    tmp_path: Path,
) -> None:
    project_catalog.create_graph_project(tmp_path / "current.nproj")

    result = project_catalog.clear_incompatible(tmp_path)

    assert result["removed"] == []
    assert result["moved"] == []
    assert result["kept"] == []


def test_global_settings_become_the_default_for_new_projects(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "provider.nproj"))
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

    ops = project_catalog.create_graph_project(tmp_path / "fresh.nproj")

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
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "provider.nproj"))
    store = ProviderStore()

    assert global_settings(store)["provider"] == "ark"

    saved = save_global_settings(store, {"provider": "deepseek", "model": "deepseek-chat"})

    assert saved["model"] == "deepseek-chat"
    assert global_settings(store)["model"] == "deepseek-chat"
