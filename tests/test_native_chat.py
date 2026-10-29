"""面板上的会话：列、开、新、改名、删——都不需要模型。"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from narrative.native_bridge import NativeBridge
from narrative.project_catalog import create_graph_project


def _bridge(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    project = tmp_path / "chat.nproj"
    create_graph_project(project)
    bridge = NativeBridge()
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    return app, bridge, results, project


def _wait(app, results, kind: str, timeout: float = 8) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        for got, payload in reversed(results):
            if got == kind:
                return payload
        time.sleep(0.01)
    raise TimeoutError(kind)


def test_chats_start_empty_then_new_chat_appears(tmp_path: Path) -> None:
    app, bridge, results, project = _bridge(tmp_path)
    bridge.openProject(str(project))
    _wait(app, results, "project")

    del results[:]
    bridge.loadChats()
    listed = _wait(app, results, "chats")
    assert listed["items"] == []
    assert listed["current"] == ""

    del results[:]
    bridge.newChat()
    created = _wait(app, results, "chat")
    chat = created["chat"]
    assert chat["title"] == "新会话"
    assert chat["messages"] == []
    assert [row["id"] for row in created["items"]] == [chat["id"]]
    assert (project.parent / "chat_chats" / f"{chat['id']}.json").is_file()


def test_reopening_a_chat_gives_back_its_messages(tmp_path: Path) -> None:
    app, bridge, results, project = _bridge(tmp_path)
    bridge.openProject(str(project))
    _wait(app, results, "project")

    del results[:]
    bridge.newChat()
    chat_id = _wait(app, results, "chat")["chat"]["id"]

    # 假装聊过两句：直接写盘，不调模型。
    from narrative.chat_store import ChatStore

    store = ChatStore(project)
    store.append(chat_id, "user", "第三章里林舟见过谁？")
    store.append(chat_id, "assistant", "见过沈砚和陈默。", steps=[{"tool": "search", "ok": True}])

    del results[:]
    bridge.openChat(chat_id)
    opened = _wait(app, results, "chat")
    assert [row["role"] for row in opened["chat"]["messages"]] == ["user", "assistant"]
    assert opened["chat"]["title"].startswith("第三章里林舟见过谁？")


def test_rename_and_delete(tmp_path: Path) -> None:
    app, bridge, results, project = _bridge(tmp_path)
    bridge.openProject(str(project))
    _wait(app, results, "project")

    del results[:]
    bridge.newChat()
    chat_id = _wait(app, results, "chat")["chat"]["id"]

    del results[:]
    bridge.renameChat(chat_id, "出场人物")
    renamed = _wait(app, results, "chat")
    assert renamed["chat"]["title"] == "出场人物"

    del results[:]
    bridge.deleteChat(chat_id)
    deleted = _wait(app, results, "chat")
    assert deleted["items"] == []
    assert deleted["chat"]["id"] == ""
