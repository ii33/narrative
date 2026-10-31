"""Project identity belongs to a private information file, not a directory name."""
from __future__ import annotations

import json
import shutil

import pytest

from narrative.chat_store import ChatStore
from narrative.graph_store import GraphStore
from narrative.node_material import MaterialStore
from narrative.project_catalog import create_graph_project, list_projects, open_graph_project
from narrative.project_package import (
    DB_NAME,
    FORMAT,
    VERSION,
    layout,
    project,
)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f00050001ff9d5f6d0d0000000049454e44ae426082"
)


def _entry(tmp_path):
    entry = tmp_path / "book.nproj"
    ops = create_graph_project(entry)
    ops.graph_accept(ops.graph_propose(
        [{"kind": "node", "id": "1", "node_type": "person", "name": "林舟"}],
        ops.graph_project_info()["revision"]), 1)
    return entry


def test_private_entry_is_a_file_and_data_is_separate(tmp_path):
    entry = _entry(tmp_path)
    assert entry.is_file()
    found = layout(entry)
    assert found.entry == entry
    assert found.root == tmp_path / "book_data"
    assert found.db == found.root / DB_NAME
    assert found.db.is_file()
    assert not (found.root / "project.json").exists()
    payload = json.loads(entry.read_text(encoding="utf-8"))
    assert payload["format"] == FORMAT and payload["version"] == VERSION
    assert payload["layout"] == {"database": "book_data/graph.sqlite3", "material": "book_data/material",
                                 "extraction": "book_extraction", "chats": "book_chats"}
    assert payload["project"]["id"] == open_graph_project(entry).graph_project_info()["project_id"]


def test_containing_directory_has_no_extension_requirement(tmp_path):
    folder = tmp_path / "a normal folder with spaces"
    entry = folder / "作品.nproj"
    create_graph_project(entry)
    assert open_graph_project(entry).graph_project_info()["revision"] == 0
    assert list_projects(folder)[0]["path"] == str(entry)


def test_internal_database_resolves_the_actual_entry(tmp_path):
    entry = _entry(tmp_path)
    assert layout(layout(entry).db).entry == entry
    assert project(layout(entry))["version"] == VERSION


def test_future_entry_has_a_deterministic_separate_layout(tmp_path):
    found = layout(tmp_path / "future.nproj")
    assert found.root == tmp_path / "future_data"
    assert found.extraction == tmp_path / "future_extraction"
    assert found.chats == tmp_path / "future_chats"


@pytest.mark.parametrize("kind", ["directory", "database", "json"])
def test_public_open_refuses_everything_except_the_private_entry(tmp_path, kind):
    entry = _entry(tmp_path)
    found = layout(entry)
    target = {"directory": found.root, "database": found.db, "json": found.root / "project.json"}[kind]
    if kind == "json":
        target.write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="入口"):
        open_graph_project(target)


@pytest.mark.parametrize("content", ["not json", "{}", '{"format":"unrelated","version":3}',
                                      '{"format":"narrative-project","version":2}'])
def test_invalid_entry_is_not_repaired_from_the_database(tmp_path, content):
    entry = _entry(tmp_path)
    entry.write_text(content, encoding="utf-8")
    before = entry.read_bytes()
    with pytest.raises(ValueError):
        open_graph_project(entry)
    assert entry.read_bytes() == before


def test_two_entries_in_one_directory_have_isolated_data(tmp_path):
    left, right = tmp_path / "left.nproj", tmp_path / "right.nproj"
    a, b = create_graph_project(left), create_graph_project(right)
    a.import_source("left", "only left")
    assert b.sources() == []
    assert layout(left).db != layout(right).db
    assert layout(left).material != layout(right).material
    assert layout(left).extraction != layout(right).extraction
    with pytest.raises(ValueError, match="覆盖"):
        create_graph_project(left)


def test_material_and_chats_follow_the_entry_locations(tmp_path):
    entry = _entry(tmp_path)
    found = layout(entry)
    store = MaterialStore(entry)
    card = store.add_card("1", "外观", "衣服")
    image = tmp_path / "ref.png"
    image.write_bytes(PNG)
    record = store.add_image("1", card["id"], image)
    assert (found.material / "1.json").is_file()
    assert (found.material / record["file"]).is_file()
    chat_store = ChatStore(entry)
    chat = chat_store.create()
    assert chat_store.path_for(chat["id"]).parent == tmp_path / "book_chats"


def test_moving_an_entire_project_folder_preserves_relative_paths(tmp_path):
    folder = tmp_path / "original"
    entry = _entry(folder)
    copied = tmp_path / "moved"
    shutil.copytree(folder, copied)
    moved_entry = copied / entry.name
    assert open_graph_project(moved_entry).graph_project_info()["revision"] == 1
    assert layout(moved_entry).db == copied / "book_data" / DB_NAME


def test_catalog_ignores_directories_and_bare_databases(tmp_path):
    _entry(tmp_path)
    legacy = tmp_path / "legacy.nproj"
    legacy.mkdir()
    GraphStore(legacy / DB_NAME, create=True)
    (tmp_path / "loose.sqlite3").write_bytes(b"database")
    assert [row["name"] for row in list_projects(tmp_path)] == ["book"]


@pytest.mark.parametrize("filename", ["project.json", "manifest.json"])
def test_old_descriptors_are_refused_without_conversion(tmp_path, filename):
    old = tmp_path / "old-project"
    old.mkdir()
    description = old / filename
    description.write_text(json.dumps({"format": FORMAT, "version": 2,
        "project_id": "old-id", "title": "Old"}), encoding="utf-8")
    before = description.read_bytes()
    with pytest.raises(ValueError, match="入口"):
        open_graph_project(description)
    assert description.read_bytes() == before
    assert list(old.iterdir()) == [description]


def test_descriptor_cannot_identify_another_projects_database(tmp_path):
    left = _entry(tmp_path)
    right = tmp_path / "right.nproj"
    create_graph_project(right)
    payload = json.loads(left.read_text(encoding="utf-8"))
    payload["layout"]["database"] = "right_data/graph.sqlite3"
    left.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="标识与数据库不一致"):
        open_graph_project(left)
