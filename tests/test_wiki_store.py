"""角色 wiki：素材存在工程旁边，按节点分开，图片拷进来。"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from narrative.wiki_store import KINDS, WikiStore

# 1x1 透明 PNG：够真实，又不用依赖图像库。
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def store(tmp_path: Path) -> WikiStore:
    return WikiStore(tmp_path / "book.sqlite3")


@pytest.fixture()
def png(tmp_path: Path) -> Path:
    path = tmp_path / "ref.png"
    path.write_bytes(PNG)
    return path


def test_an_unknown_node_has_an_empty_wiki(store: WikiStore) -> None:
    view = store.view("7")
    assert view["bible"] == ""
    assert view["appearances"] == []
    assert [item["key"] for item in view["kinds"]] == list(KINDS)


def test_bible_round_trip(store: WikiStore) -> None:
    store.set_bible("7", "嘴硬心软。")
    assert store.view("7")["bible"] == "嘴硬心软。"
    # 存在工程旁边，一个角色一个文件。
    assert (store.root / "7.json").is_file()


def test_appearance_needs_a_name_and_can_carry_a_time_point(store: WikiStore) -> None:
    with pytest.raises(ValueError):
        store.add_appearance("7", "  ")
    store.add_appearance("7", "青衫少年", "洗得发白的青衫", "34")
    appearance = store.view("7")["appearances"][0]
    assert appearance["name"] == "青衫少年"
    assert appearance["event_id"] == "34"
    assert appearance["images"] == []


def test_images_are_copied_in_with_their_kind(store: WikiStore, png: Path) -> None:
    store.add_appearance("7", "青衫少年")
    appearance_id = store.view("7")["appearances"][0]["id"]

    record = store.add_image("7", appearance_id, "turnaround", png, "正面/侧面/背面")
    assert Path(record["path"]).is_file()
    assert store.root in Path(record["path"]).parents  # 拷进来了，不是引用原路径

    image = store.view("7")["appearances"][0]["images"][0]
    assert image["kind"] == "turnaround"
    assert image["kind_label"] == "三视图"
    assert Path(image["path"]).is_file()


def test_only_known_kinds_and_real_images(store: WikiStore, tmp_path: Path) -> None:
    store.add_appearance("7", "青衫少年")
    appearance_id = store.view("7")["appearances"][0]["id"]
    with pytest.raises(ValueError):
        store.add_image("7", appearance_id, "poster", tmp_path / "x.png")
    with pytest.raises(ValueError):
        store.add_image("7", appearance_id, "concept", tmp_path / "missing.png")

    text = tmp_path / "note.txt"
    text.write_text("not an image", encoding="utf-8")
    with pytest.raises(ValueError):
        store.add_image("7", appearance_id, "concept", text)


def test_removing_an_image_deletes_the_file(store: WikiStore, png: Path) -> None:
    store.add_appearance("7", "青衫少年")
    appearance_id = store.view("7")["appearances"][0]["id"]
    record = store.add_image("7", appearance_id, "concept", png)
    store.remove_image("7", appearance_id, record["id"])
    assert store.view("7")["appearances"][0]["images"] == []
    assert not Path(record["path"]).exists()


def test_removing_an_appearance_takes_its_folder(store: WikiStore, png: Path) -> None:
    store.add_appearance("7", "青衫少年")
    appearance_id = store.view("7")["appearances"][0]["id"]
    store.add_image("7", appearance_id, "concept", png)
    folder = store.root / "7" / appearance_id
    assert folder.is_dir()

    store.remove_appearance("7", appearance_id)
    assert not folder.exists()
    assert store.view("7")["appearances"] == []


def test_index_lists_nodes_that_have_a_wiki(store: WikiStore) -> None:
    store.set_bible("7", "x")
    store.add_appearance("12", "黑衣")
    assert store.nodes_with_wiki() == ["12", "7"]


def test_a_file_that_cannot_be_deleted_is_cleaned_up_on_the_next_view(
    store: WikiStore, png: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """页面上正开着那张图时 Windows 不放文件：记录先走，文件下次读这一页再清。"""
    store.add_appearance("7", "青衫少年")
    appearance_id = store.view("7")["appearances"][0]["id"]
    record = store.add_image("7", appearance_id, "concept", png)
    copied = Path(record["path"])

    with monkeypatch.context() as patch:
        def locked(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError(32, "另一个程序正在使用此文件")

        patch.setattr(Path, "unlink", locked)
        result = store.remove_image("7", appearance_id, record["id"])
        assert result["file_deleted"] is False
        # 记录立刻没了，页面不会再显示它。
        assert store.view("7")["appearances"][0]["images"] == []
        assert copied.exists()

    store.view("7")  # 没人引用它了，这一次清掉
    assert not copied.exists()
