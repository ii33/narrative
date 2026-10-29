"""工程就是一个 .nproj 目录：库和资料在里面，缓存和会话在旁边。

旧版那种「一个 .sqlite3 + 三个同级目录」已经不支持了——它正是拷一个文件就丢资料、
丢会话的形态。这组用例钉住新契约：只认包，包内只有库和资料。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from narrative.chat_store import ChatStore
from narrative.node_material import MaterialStore
from narrative.project_catalog import create_graph_project, list_projects, open_graph_project
from narrative.project_package import (
    DB_NAME,
    MANIFEST_NAME,
    PACKAGE_SUFFIX,
    layout,
    manifest,
)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f00050001ff9d5f6d0d0000000049454e44ae426082"
)


def _package(tmp_path: Path) -> Path:
    package = tmp_path / f"book{PACKAGE_SUFFIX}"
    ops = create_graph_project(package)
    ops.graph_accept(
        ops.graph_propose(
            [{"kind": "node", "id": "1", "node_type": "person", "name": "林舟"}],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    return package


def test_a_new_project_is_a_package_with_a_manifest(tmp_path: Path) -> None:
    package = _package(tmp_path)

    assert package.is_dir()
    assert (package / DB_NAME).is_file()
    assert (package / MANIFEST_NAME).is_file()

    payload = json.loads((package / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert payload["format"] == "narrative-project"
    assert payload["title"] == open_graph_project(package).graph_project_info()["title"]
    assert payload["parts"] == {"material": False}


def test_the_layout_puts_the_work_inside_and_the_rest_beside(tmp_path: Path) -> None:
    package = _package(tmp_path)
    found = layout(package)

    assert found.root == package
    assert found.db == package / DB_NAME
    assert found.material == package / "material"
    # 缓存和会话不进包：它们在包旁边，名字和包同名。
    assert found.extraction == tmp_path / "book_extraction"
    assert found.chats == tmp_path / "book_chats"
    assert package not in found.extraction.parents
    assert package not in found.chats.parents


def test_a_database_inside_a_package_resolves_to_the_package(tmp_path: Path) -> None:
    package = _package(tmp_path)
    assert layout(package / DB_NAME).root == package


def test_a_path_that_does_not_exist_yet_is_a_package(tmp_path: Path) -> None:
    target = tmp_path / f"future{PACKAGE_SUFFIX}"
    assert layout(target).root == target


def test_a_bare_sqlite_file_is_refused(tmp_path: Path) -> None:
    loose = tmp_path / "old.sqlite3"
    loose.write_bytes(b"")
    with pytest.raises(ValueError, match="旧版单文件工程"):
        layout(loose)


def test_a_plain_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不是"):
        layout(tmp_path / "notes.txt")


def test_a_directory_that_is_not_a_package_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "stuff"
    folder.mkdir()
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="不是工程包"):
        layout(folder)


def test_the_manifest_reader_returns_what_was_written(tmp_path: Path) -> None:
    package = _package(tmp_path)
    payload = manifest(layout(package))
    assert payload["format"] == "narrative-project"
    assert payload["version"] == 1


def test_material_and_chats_land_where_the_layout_says(tmp_path: Path) -> None:
    package = _package(tmp_path)
    found = layout(package)

    store = MaterialStore(package)
    card = store.add_card("1", "青衫少年", "洗得发白的青衫")
    image = tmp_path / "ref.png"
    image.write_bytes(PNG)
    record = store.add_image("1", card["id"], image)

    assert (package / "material" / "1.json").is_file()
    assert Path(found.material / record["file"]).is_file()

    chats = ChatStore(package)
    chat = chats.create()
    chats.append(chat["id"], "user", "第三章里林舟见过谁？")
    assert (tmp_path / "book_chats" / f"{chat['id']}.json").is_file()
    assert not (package / "chats").exists()


def test_the_catalog_lists_packages_only(tmp_path: Path) -> None:
    _package(tmp_path)
    # 一个裸库不该出现在列表里。
    (tmp_path / "loose.sqlite3").write_bytes(b"")

    listed = list_projects(tmp_path)
    assert [row["name"] for row in listed] == ["book"]
    assert listed[0]["status"] == "ready"
