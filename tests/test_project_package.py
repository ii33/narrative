"""工程包：库、资料、提取缓存、会话收进一个目录，读写还是同一套路径。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from narrative import tool_api
from narrative.chat_store import ChatStore
from narrative.node_material import MaterialStore
from narrative.project_catalog import create_graph_project, list_projects, open_graph_project
from narrative.project_package import DB_NAME, MANIFEST_NAME, layout, pack

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f00050001ff9d5f6d0d0000000049454e44ae426082"
)


def _loose(tmp_path: Path) -> Path:
    """一个裸工程：库 + 资料 + 会话 + 提取缓存，四样都在同级目录里。"""
    project = tmp_path / "book.sqlite3"
    ops = create_graph_project(project)
    ops.graph_accept(
        ops.graph_propose(
            [{"kind": "node", "id": "1", "node_type": "person", "name": "林舟"}],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    store = MaterialStore(project)
    card = store.add_card("1", "青衫少年", "洗得发白的青衫")
    store.add_image("1", card["id"], _png(tmp_path))
    ChatStore(project).create()
    (project.with_name("book_extraction")).mkdir()
    (project.with_name("book_extraction") / "cache.json").write_text("{}", encoding="utf-8")
    return project


def _png(tmp_path: Path) -> Path:
    path = tmp_path / "ref.png"
    path.write_bytes(PNG)
    return path


def test_a_loose_project_is_a_database_with_three_siblings(tmp_path: Path) -> None:
    project = _loose(tmp_path)
    found = layout(project)
    assert found.package is False
    assert found.db == project
    assert found.material.name == "book_material"
    assert found.chats.name == "book_chats"
    assert found.extraction.name == "book_extraction"


def test_packing_moves_everything_into_one_directory(tmp_path: Path) -> None:
    project = _loose(tmp_path)
    result = pack(project)

    package = tmp_path / "book.nproj"
    assert Path(result["package"]) == package
    assert result["moved"] is True
    assert sorted(result["parts"]) == ["chats", "extraction", "material"]

    # 原来的散件都不在了，只剩包。
    assert not project.exists()
    assert not (tmp_path / "book_material").exists()
    assert (package / DB_NAME).is_file()
    assert (package / MANIFEST_NAME).is_file()

    manifest = json.loads((package / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["format"] == "narrative-project"
    # 标题取自库里的 meta，不是文件名。
    assert manifest["title"] == open_graph_project(package).graph_project_info()["title"]
    assert manifest["parts"] == {"material": True, "extraction": True, "chats": True}


def test_the_package_reads_and_writes_like_before(tmp_path: Path) -> None:
    project = _loose(tmp_path)
    package = Path(pack(project)["package"])

    found = layout(package)
    assert found.package is True
    # 库还是那个库：节点、资料、图片都在。
    ops = open_graph_project(package)
    assert [node["name"] for node in ops.graph_snapshot()["nodes"]] == ["林舟"]

    store = MaterialStore(package)
    cards = store.view("1")["cards"]
    assert cards[0]["title"] == "青衫少年"
    assert Path(cards[0]["images"][0]["path"]).is_file()
    assert str(package) in cards[0]["images"][0]["path"]

    # 往包里继续写：落在 <包>/material 下，不是包外面。
    store.add_card("1", "认主之后")
    assert (package / "material" / "1.json").is_file()

    chats = ChatStore(package)
    chat = chats.create()
    chats.append(chat["id"], "user", "第三章里林舟见过谁？")
    assert (package / "chats" / f"{chat['id']}.json").is_file()


def test_extraction_cache_follows_the_package(tmp_path: Path) -> None:
    project = _loose(tmp_path)
    package = Path(pack(project)["package"])
    ops = open_graph_project(package)
    # 工具的缓存路径也指向包里。
    path = tool_api._extraction_path(ops, "src1")
    assert package in path.parents
    assert path.parent.name == "extraction"


def test_the_catalog_lists_packages_and_bare_files(tmp_path: Path) -> None:
    pack(_loose(tmp_path))
    other = tmp_path / "other.sqlite3"
    create_graph_project(other)

    listed = {item["name"]: item for item in list_projects(tmp_path)}
    assert listed["book"]["package"] is True
    assert listed["other"]["package"] is False
    # 包里读的是 graph.sqlite3 的元信息。
    assert listed["book"]["db"].endswith("graph.sqlite3")


def test_packing_twice_is_a_no_op(tmp_path: Path) -> None:
    project = _loose(tmp_path)
    package = Path(pack(project)["package"])
    again = pack(package)
    assert again["moved"] is False
    assert "已经是工程包" in again["note"]


def test_a_copy_leaves_the_original_alone(tmp_path: Path) -> None:
    project = _loose(tmp_path)
    result = pack(project, move=False)
    assert project.is_file()
    assert (tmp_path / "book_material").is_dir()
    assert (Path(result["package"]) / DB_NAME).is_file()


def test_packing_refuses_to_overwrite(tmp_path: Path) -> None:
    project = _loose(tmp_path)
    (tmp_path / "book.nproj").mkdir()
    with pytest.raises(ValueError, match="已存在"):
        pack(project)


def test_the_tool_packs_through_the_same_function(tmp_path: Path) -> None:
    project = _loose(tmp_path)
    result = tool_api.call("pack_project", {}, project=project)
    assert Path(result["package"]).name == "book.nproj"
    # 包能直接打开：工具面里 --project 给包目录就行。
    info = tool_api.call("project_info", {}, project=Path(result["package"]))
    assert info["project"]["title"] == "未命名作品"
    assert info["project"]["path"].endswith("graph.sqlite3")
