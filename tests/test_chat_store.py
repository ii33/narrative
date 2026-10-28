"""会话存在工程旁边，一次对话一个文件。"""

from __future__ import annotations

from pathlib import Path

import pytest

from narrative.chat_store import ChatStore


@pytest.fixture()
def store(tmp_path: Path) -> ChatStore:
    return ChatStore(tmp_path / "book.sqlite3")


def test_a_new_chat_starts_empty_and_titled(store: ChatStore) -> None:
    chat = store.create()
    assert chat["title"] == "新会话"
    assert chat["messages"] == []
    assert chat["session"].startswith("chat-")
    assert store.path_for(chat["id"]).is_file()


def test_the_first_question_becomes_the_title(store: ChatStore) -> None:
    chat = store.create()
    saved = store.append(chat["id"], "user", "第三章里林舟见过谁？\n顺便说说他在哪")
    assert saved["title"].startswith("第三章里林舟见过谁？ 顺便")
    assert len(saved["title"]) <= 25  # 24 个字 + 省略号


def test_renaming_wins_over_the_auto_title(store: ChatStore) -> None:
    chat = store.create()
    store.append(chat["id"], "user", "第三章里林舟见过谁？")
    renamed = store.rename(chat["id"], "第三章·出场人物")
    assert renamed["title"] == "第三章·出场人物"
    store.append(chat["id"], "user", "再问一句")
    assert store.load(chat["id"])["title"] == "第三章·出场人物"


def test_messages_keep_their_steps_and_image(store: ChatStore) -> None:
    chat = store.create()
    store.append(chat["id"], "user", "看这张图", image="C:/tmp/shot.png")
    store.append(chat["id"], "assistant", "查到了", steps=[{"tool": "search", "ok": True}])
    messages = store.load(chat["id"])["messages"]
    assert messages[0]["image"] == "C:/tmp/shot.png"
    assert messages[1]["steps"][0]["tool"] == "search"


def test_listing_puts_the_latest_first(store: ChatStore) -> None:
    first = store.create()
    second = store.create()
    store.append(first["id"], "user", "先问的")
    store.append(second["id"], "user", "后问的")
    rows = store.list()
    assert [row["id"] for row in rows][0] == second["id"]
    assert rows[0]["message_count"] == 1


def test_deleting_removes_the_file(store: ChatStore) -> None:
    chat = store.create()
    store.delete(chat["id"])
    assert not store.path_for(chat["id"]).exists()
    assert store.list() == []


def test_a_broken_file_does_not_hide_the_others(store: ChatStore) -> None:
    good = store.create()
    store.append(good["id"], "user", "还在")
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "broken.json").write_text("{not json", encoding="utf-8")
    assert [row["id"] for row in store.list()] == [good["id"]]


def test_unknown_chat_says_so(store: ChatStore) -> None:
    with pytest.raises(ValueError, match="不存在"):
        store.load("nope")
