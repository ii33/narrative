"""节点资料：一张张资料卡，各有生效/失效时间，按阅读位置出现和消失。"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from narrative.node_material import MaterialStore, holds_at

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


def test_an_unknown_node_has_no_cards(store: MaterialStore) -> None:
    view = store.view("P7")
    assert view["cards"] == []
    assert view["hidden_count"] == 0


def test_a_card_needs_a_title_and_keeps_its_window(store: MaterialStore) -> None:
    with pytest.raises(ValueError):
        store.add_card("P7", "  ")
    store.add_card("P7", "青衫少年", "洗得发白的青衫", "E3", "E12")
    # 这张卡到 E12 就失效了，所以在「最新」上看不到它——读原始文件才拿得到。
    card = store.load("P7")["cards"][0]
    assert card["title"] == "青衫少年"
    assert card["detail"] == "洗得发白的青衫"
    assert (card["valid_from"], card["valid_to"]) == ("E3", "E12")
    assert card["images"] == []
    assert (store.root / "P7.json").is_file()


def test_the_window_must_move_forward(store: MaterialStore) -> None:
    with pytest.raises(ValueError):
        store.add_card("P7", "同一刻", "", "E3", "E3")
    with pytest.raises(ValueError):
        store.add_card("P7", "倒过来", "", "E5", "E2")
    store.add_card("P7", "青衫少年", "", "E3", "E12")
    card = store.load("P7")["cards"][0]
    with pytest.raises(ValueError):
        store.update_card("P7", card["id"], valid_from="E20")


def test_a_card_holds_between_its_two_moments() -> None:
    card = {"valid_from": "E3", "valid_to": "E12"}
    assert not holds_at(card, "E1")  # 还没到
    assert holds_at(card, "E3")  # 从这一步起
    assert holds_at(card, "E5")
    assert not holds_at(card, "E12")  # 到这一步起不算
    # 停在「最新」就是读完全书：有失效时刻的都不在了。
    assert not holds_at(card, "")
    assert holds_at({"valid_from": "", "valid_to": ""}, "")
    assert holds_at({"valid_from": "E3", "valid_to": ""}, "")
    assert not holds_at({"valid_from": "E3", "valid_to": ""}, "E2")


def test_the_view_shows_only_what_holds_here(store: MaterialStore) -> None:
    store.add_card("P7", "一直", "", "", "")
    store.add_card("P7", "早先", "", "", "E12")
    store.add_card("P7", "后来的", "", "E20", "")

    early = store.view("P7", cut="E5")
    assert [card["title"] for card in early["cards"]] == ["一直", "早先"]
    assert early["hidden_count"] == 1

    late = store.view("P7", cut="E30")
    assert [card["title"] for card in late["cards"]] == ["一直", "后来的"]
    assert late["hidden_count"] == 1

    # 停在「最新」＝读完全书：只有没有失效时刻的还在，起点早就不成问题了。
    latest = store.view("P7")
    assert [card["title"] for card in latest["cards"]] == ["一直", "后来的"]
    assert latest["hidden_count"] == 1


def test_cards_are_ordered_by_when_they_start(store: MaterialStore) -> None:
    store.add_card("P7", "后来的", "", "E20", "")
    store.add_card("P7", "从头", "", "", "")
    store.add_card("P7", "早先", "", "E3", "")
    assert [card["title"] for card in store.view("P7", cut="E30")["cards"]] == [
        "从头", "早先", "后来的",
    ]


def test_images_are_copied_in_and_carry_a_caption(store: MaterialStore, png: Path) -> None:
    store.add_card("P7", "青衫少年")
    card_id = store.view("P7")["cards"][0]["id"]

    record = store.add_image("P7", card_id, png, "正面")
    assert Path(record["path"]).is_file()
    assert store.root in Path(record["path"]).parents  # 拷进来了，不是引用原路径

    image = store.view("P7")["cards"][0]["images"][0]
    assert image["caption"] == "正面"

    store.update_image_caption("P7", card_id, image["id"], "正面，束发")
    assert store.view("P7")["cards"][0]["images"][0]["caption"] == "正面，束发"


def test_only_real_images(store: MaterialStore, tmp_path: Path) -> None:
    store.add_card("P7", "青衫少年")
    card_id = store.view("P7")["cards"][0]["id"]
    with pytest.raises(ValueError):
        store.add_image("P7", card_id, tmp_path / "missing.png")
    text = tmp_path / "note.txt"
    text.write_text("not an image", encoding="utf-8")
    with pytest.raises(ValueError):
        store.add_image("P7", card_id, text)


def test_removing_an_image_deletes_the_file(store: MaterialStore, png: Path) -> None:
    store.add_card("P7", "青衫少年")
    card_id = store.view("P7")["cards"][0]["id"]
    record = store.add_image("P7", card_id, png)
    store.remove_image("P7", card_id, record["id"])
    assert store.view("P7")["cards"][0]["images"] == []
    assert not Path(record["path"]).exists()


def test_removing_a_card_takes_its_folder(store: MaterialStore, png: Path) -> None:
    store.add_card("P7", "青衫少年")
    card_id = store.view("P7")["cards"][0]["id"]
    store.add_image("P7", card_id, png)
    folder = store.root / "P7" / card_id
    assert folder.is_dir()

    store.remove_card("P7", card_id)
    assert not folder.exists()
    assert store.view("P7")["cards"] == []


def test_a_file_that_cannot_be_deleted_is_cleaned_up_on_the_next_view(
    store: MaterialStore, png: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """页面上正开着那张图时 Windows 不放文件：记录先走，文件下次读这一页再清。"""
    store.add_card("P7", "青衫少年")
    card_id = store.view("P7")["cards"][0]["id"]
    record = store.add_image("P7", card_id, png)
    copied = Path(record["path"])

    with monkeypatch.context() as patch:
        def locked(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError(32, "另一个程序正在使用此文件")

        patch.setattr(Path, "unlink", locked)
        result = store.remove_image("P7", card_id, record["id"])
        assert result["file_deleted"] is False
        assert store.view("P7")["cards"][0]["images"] == []
        assert copied.exists()

    store.view("P7")  # 没人引用它了，这一次清掉
    assert not copied.exists()
