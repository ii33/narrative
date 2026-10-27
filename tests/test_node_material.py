"""节点资料：总结、描述、形态与设定图存在工程旁边，按节点分开。"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from narrative.node_material import KINDS, MaterialStore

# 1x1 透明 PNG：够真实，又不用依赖图像库。
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def store(tmp_path: Path) -> MaterialStore:
    return MaterialStore(tmp_path / "book.sqlite3")


@pytest.fixture()
def png(tmp_path: Path) -> Path:
    path = tmp_path / "ref.png"
    path.write_bytes(PNG)
    return path


def test_an_unknown_node_has_empty_material(store: MaterialStore) -> None:
    view = store.view("7")
    assert view["summary"] == ""
    assert view["description"] == ""
    assert view["forms"] == []
    assert [item["key"] for item in view["kinds"]] == list(KINDS)


def test_summary_and_description_round_trip(store: MaterialStore) -> None:
    store.save_text("7", "  洗得发白的青衫少年  ", "嘴硬心软，遇事先笑再算账。")
    view = store.view("7")
    assert view["summary"] == "洗得发白的青衫少年"
    assert view["description"] == "嘴硬心软，遇事先笑再算账。"
    # 存在工程旁边，一个节点一个文件。
    assert (store.root / "7.json").is_file()


def test_a_form_needs_a_name_and_can_carry_a_time_point(store: MaterialStore) -> None:
    with pytest.raises(ValueError):
        store.add_form("7", "  ")
    store.add_form("7", "青衫少年", "洗得发白的青衫", "34")
    form = store.view("7")["forms"][0]
    assert form["name"] == "青衫少年"
    assert form["event_id"] == "34"
    assert form["images"] == []


def test_a_form_can_be_edited_and_dropped(store: MaterialStore) -> None:
    store.add_form("7", "青衫少年", "旧衫", "34")
    form_id = store.view("7")["forms"][0]["id"]
    store.update_form("7", form_id, name="白衣少年", note="换了一身白衣", event_id="")
    form = store.view("7")["forms"][0]
    assert (form["name"], form["note"], form["event_id"]) == ("白衣少年", "换了一身白衣", "")

    with pytest.raises(ValueError):
        store.update_form("7", form_id, name="  ")

    store.remove_form("7", form_id)
    assert store.view("7")["forms"] == []


def test_images_are_copied_in_with_their_kind(store: MaterialStore, png: Path) -> None:
    store.add_form("7", "青衫少年")
    form_id = store.view("7")["forms"][0]["id"]

    record = store.add_image("7", form_id, "setting", png, "设定图")
    assert Path(record["path"]).is_file()
    assert store.root in Path(record["path"]).parents  # 拷进来了，不是引用原路径

    image = store.view("7")["forms"][0]["images"][0]
    assert image["kind"] == "setting"
    assert image["kind_label"] == "设定图"
    assert Path(image["path"]).is_file()


def test_only_known_kinds_and_real_images(store: MaterialStore, tmp_path: Path) -> None:
    store.add_form("7", "青衫少年")
    form_id = store.view("7")["forms"][0]["id"]
    with pytest.raises(ValueError):
        store.add_image("7", form_id, "poster", tmp_path / "x.png")
    with pytest.raises(ValueError):
        store.add_image("7", form_id, "concept", tmp_path / "missing.png")

    text = tmp_path / "note.txt"
    text.write_text("not an image", encoding="utf-8")
    with pytest.raises(ValueError):
        store.add_image("7", form_id, "concept", text)


def test_removing_an_image_deletes_the_file(store: MaterialStore, png: Path) -> None:
    store.add_form("7", "青衫少年")
    form_id = store.view("7")["forms"][0]["id"]
    record = store.add_image("7", form_id, "concept", png)
    store.remove_image("7", form_id, record["id"])
    assert store.view("7")["forms"][0]["images"] == []
    assert not Path(record["path"]).exists()


def test_removing_a_form_takes_its_folder(store: MaterialStore, png: Path) -> None:
    store.add_form("7", "青衫少年")
    form_id = store.view("7")["forms"][0]["id"]
    store.add_image("7", form_id, "concept", png)
    folder = store.root / "7" / form_id
    assert folder.is_dir()

    store.remove_form("7", form_id)
    assert not folder.exists()
    assert store.view("7")["forms"] == []


def test_index_lists_nodes_that_have_material(store: MaterialStore) -> None:
    store.save_text("7", "x", "")
    store.add_form("12", "黑衣")
    assert store.nodes_with_material() == ["12", "7"]


def test_a_file_that_cannot_be_deleted_is_cleaned_up_on_the_next_view(
    store: MaterialStore, png: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """页面上正开着那张图时 Windows 不放文件：记录先走，文件下次读这一页再清。"""
    store.add_form("7", "青衫少年")
    form_id = store.view("7")["forms"][0]["id"]
    record = store.add_image("7", form_id, "concept", png)
    copied = Path(record["path"])

    with monkeypatch.context() as patch:
        def locked(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError(32, "另一个程序正在使用此文件")

        patch.setattr(Path, "unlink", locked)
        result = store.remove_image("7", form_id, record["id"])
        assert result["file_deleted"] is False
        # 记录立刻没了，页面不会再显示它。
        assert store.view("7")["forms"][0]["images"] == []
        assert copied.exists()

    store.view("7")  # 没人引用它了，这一次清掉
    assert not copied.exists()
