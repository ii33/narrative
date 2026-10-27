"""常驻协作者面板：挂在页面右边，收起时让位，消息不随切页面丢失。"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge
from narrative.operations import Operations

pytestmark = pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") != "offscreen",
    reason="Qt Quick 用例需要 offscreen 平台",
)


def _settle(app: QApplication, seconds: float = 0.3) -> None:
    """让布局真的跑一轮：SplitView 的宽度不是 processEvents 一次就分好的。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _value(item: Any) -> Any:
    return item.toVariant() if hasattr(item, "toVariant") else item


def _pump(app: QApplication, results: list[tuple[str, dict]], kind: str) -> dict:
    deadline = time.monotonic() + 8
    while not any(name == kind for name, _payload in results) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    matched = [payload for name, payload in results if name == kind]
    assert matched, f"没有收到 {kind}"
    return matched[-1]


@pytest.fixture()
def workspace(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    project = tmp_path / "chat.sqlite3"
    ops = Operations.create_graph_project(project)
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "林舟出场",
                 "participants": ["1"]},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )

    bridge = NativeBridge(tmp_path)
    results: list[tuple[str, dict]] = []
    errors: list[tuple[str, str]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    try:
        engine.load(QUrl.fromLocalFile(str(QML_PATH)))
        window: Any = engine.rootObjects()[0]
        # 不 show 就没有布局：SplitView 的宽度要真的分配过才测得到。
        window.show()
        bridge.openProject(str(project))
        _pump(app, results, "project")
        _pump(app, results, "browse")
        panel = window.findChild(object, "assistantPanel")
        assert panel is not None, "协作者面板没有加载"
        yield app, window, panel, results
        assert not errors
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()


def test_the_panel_starts_collapsed_and_opens_on_demand(workspace) -> None:
    app, window, panel, _results = workspace
    app.processEvents()
    assert window.property("assistantOpen") is False
    assert panel.property("visible") is False

    window.setProperty("assistantOpen", True)
    _settle(app)
    assert panel.property("visible") is True
    assert panel.property("width") > 200

    # 收起之后页面拿回宽度，消息还在（面板状态不随可见性清空）。
    panel.apply({"answer": "先记一句", "steps": []})
    window.setProperty("assistantOpen", False)
    _settle(app)
    assert panel.property("visible") is False
    assert len(_value(panel.property("messages"))) == 1


def test_a_turn_shows_the_answer_and_the_tools_it_used(workspace) -> None:
    app, window, panel, _results = workspace
    window.setProperty("assistantOpen", True)
    _settle(app)

    panel.apply({
        "answer": "林舟出现过一次。",
        "steps": [
            {"tool": "list_nodes", "args": {}, "ok": True, "summary": "2 个节点"},
            {"tool": "get_node", "args": {"node_id": "P1"}, "ok": False,
             "summary": "节点不存在：P1"},
        ],
    })
    app.processEvents()

    messages = _value(panel.property("messages"))
    assert len(messages) == 1
    assert messages[0]["text"] == "林舟出现过一次。"
    assert [step["tool"] for step in messages[0]["steps"]] == ["list_nodes", "get_node"]
    transcript = panel.findChild(object, "assistantTranscript")
    assert transcript is not None
    assert transcript.property("count") == 1


def test_clearing_starts_a_new_conversation(workspace) -> None:
    app, window, panel, results = workspace
    window.setProperty("assistantOpen", True)
    panel.apply({"answer": "说过一句", "steps": []})
    app.processEvents()
    assert len(_value(panel.property("messages"))) == 1

    del results[:]
    panel.reset()
    app.processEvents()
    assert _value(panel.property("messages")) == []
    # 清空会顺手把会话关掉：下一轮不带着旧话题。
    assert any(kind == "assistant_reset" for kind, _payload in results)
