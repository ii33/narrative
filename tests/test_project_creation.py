"""Project creation paths and clear failures; no model or user project writes."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from narrative.operations import Operations


def test_native_creation_accepts_folder_urls_with_spaces_and_unicode(tmp_path, monkeypatch):
    qt = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from narrative.native_bridge import NativeBridge

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    folder = tmp_path / "含 空格的目录"
    folder.mkdir()
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    try:
        bridge.createProjectAt(qt.QUrl.fromLocalFile(str(folder)).toString(), "新工程")
        deadline = time.monotonic() + 5
        while not errors and not any(kind == "project" for kind, _ in results):
            app.processEvents()
            if time.monotonic() >= deadline:
                pytest.fail("Project creation did not finish")
            time.sleep(0.005)
        assert not errors
        entry = folder / "新工程.nproj"
        assert entry.is_file()
        assert Operations.open_graph_project(entry).graph_project_info()["revision"] == 0
    finally:
        bridge.close()
        app.processEvents()


def test_data_path_file_collision_is_not_overwritten(tmp_path):
    entry = tmp_path / "example.nproj"
    existing = tmp_path / "example_data"
    existing.write_text("keep this user file", encoding="utf-8")
    with pytest.raises(ValueError, match="工程数据路径已存在"):
        Operations.create_graph_project(entry)
    assert existing.read_text(encoding="utf-8") == "keep this user file"
    assert not entry.exists()


def test_creation_permission_failure_names_the_path_without_fallback(tmp_path, monkeypatch):
    entry = tmp_path / "example.nproj"
    blocked = tmp_path / "example_data"
    original_mkdir = Path.mkdir

    def deny_selected_directory(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError(13, "Access denied", str(path))
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_selected_directory)
    with pytest.raises(ValueError, match="没有写入权限") as error:
        Operations.create_graph_project(entry)
    assert str(blocked) in str(error.value)
    assert "检查应用运行环境" in str(error.value)
    assert isinstance(error.value.__cause__, PermissionError)
    assert not entry.exists()
    assert not blocked.exists()


def test_set_graph_title_syncs_database_and_entry_without_renaming_paths(tmp_path):
    entry = tmp_path / "original-file-name.nproj"
    ops = Operations.create_graph_project(entry)
    descriptor = json.loads(entry.read_text(encoding="utf-8"))
    descriptor["unrecognized_metadata"] = {"keep": True}
    entry.write_text(json.dumps(descriptor, ensure_ascii=False), encoding="utf-8")

    ops.set_graph_title("  新的工程名称  ")

    updated = json.loads(entry.read_text(encoding="utf-8"))
    assert updated["project"]["title"] == "新的工程名称"
    assert updated["unrecognized_metadata"] == {"keep": True}
    assert updated["project"]["id"] == descriptor["project"]["id"]
    assert updated["project"]["created_at"] == descriptor["project"]["created_at"]
    assert updated["layout"] == descriptor["layout"]
    assert entry.name == "original-file-name.nproj"
    assert ops.graph_project_info()["title"] == "新的工程名称"
    assert Operations.open(entry).graph_project_info()["title"] == "新的工程名称"
    assert next(row for row in Operations.list_projects(tmp_path) if row["path"] == str(entry))["title"] == "新的工程名称"

    with pytest.raises(ValueError, match="不能为空"):
        ops.set_graph_title("  ")
    assert json.loads(entry.read_text(encoding="utf-8"))["project"]["title"] == "新的工程名称"
