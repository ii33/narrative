"""节点资料：详情区里的总结、描述、形态与设定图，任何节点都有。"""

from __future__ import annotations

import base64
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

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _value(item: Any) -> Any:
    return item.toVariant() if hasattr(item, "toVariant") else item


def _node_of(payload: dict) -> str:
    """资料说 node_id，详情说 node.id；两种都认。"""
    return str(payload.get("node_id") or (payload.get("node") or {}).get("id") or "")


def _pump(
    app: QApplication,
    results: list[tuple[str, dict]],
    kind: str,
    node_id: str = "",
) -> dict:
    """等一条结果；给了 node_id 就只认那个节点的。

    只等 kind 会接受上一个节点迟到的 payload——切节点时的等待会被它骗过去。
    """
    def ready() -> list[dict]:
        return [
            payload for name, payload in results
            if name == kind and (not node_id or _node_of(payload) == node_id)
        ]

    deadline = time.monotonic() + 8
    while not ready() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    matched = ready()
    assert matched, f"没有收到 {kind}（节点 {node_id or '任意'}）"
    return matched[-1]


@pytest.fixture()
def workspace(tmp_path: Path):
    """一个人物、一件物品、一个事件；进工程后停在人物上。"""
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    project = tmp_path / "material.sqlite3"
    ops = Operations.create_graph_project(project)
    candidate = ops.graph_propose(
        [
            {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
            {"kind": "node", "id": "2", "node_type": "item", "name": "火纹石"},
            {"kind": "node", "id": "3", "node_type": "event", "name": "林舟出场"},
        ],
        ops.graph_project_info()["revision"],
    )
    ops.graph_accept(candidate, 1)
    revision = ops.graph_project_info()["revision"]
    property_candidate = ops.property_propose(
        [
            {"kind": "property_definition", "id": "identity", "value_type": "string",
             "cardinality": "one", "persistence": "until_changed"},
            {"kind": "property_value", "id": "p1", "node_id": "1",
             "property_id": "identity", "value": "总经理", "event_id": "3"},
        ],
        revision,
        source="manual",
    )
    ops.property_accept(property_candidate, 1)

    bridge = NativeBridge(tmp_path)
    results: list[tuple[str, dict]] = []
    errors: list[tuple[str, str]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    # engine 必须活到用例结束：它被回收，QML 对象就跟着散架。
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    try:
        engine.load(QUrl.fromLocalFile(str(QML_PATH)))
        window: Any = engine.rootObjects()[0]
        bridge.openProject(str(project))
        _pump(app, results, "project")
        _pump(app, results, "browse")
        page = window.findChild(object, "browsePage")
        assert page is not None
        page.select("1")
        _pump(app, results, "detail", "1")
        _pump(app, results, "node_properties", "1")
        _pump(app, results, "material", "1")
        yield app, bridge, page, results, errors
        assert not errors
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()


def test_every_node_has_a_material_section_of_its_own(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    app.processEvents()
    assert page.property("materialNodeId") == "1"
    assert _value(page.property("summaryDraft")) == ""
    assert _value(page.property("material"))["forms"] == []

    # 物品也一样有：这一块不属于人物。
    del results[:]
    page.select("2")
    _pump(app, results, "material", "2")
    assert page.property("materialNodeId") == "2"


def test_summary_and_description_are_kept_out_of_the_property_rows(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    page.setProperty("summaryDraft", "嘴硬心软的总经理")
    page.setProperty("descriptionDraft", "出身寒微，最恨别人拿出身说事。")
    del results[:]
    page.saveMaterialText()
    payload = _pump(app, results, "material", "1")
    app.processEvents()

    assert payload["summary"] == "嘴硬心软的总经理"
    assert _value(page.property("summaryDraft")) == "嘴硬心软的总经理"
    # 素材不是披露：属性行里不会多出一条「总结」，也没有引文可回查。
    labels = [row["label"] for row in _value(page.property("propertyRows"))]
    assert "总结" not in labels and "描述" not in labels
    assert labels == ["身份"]


def test_forms_carry_a_time_point_and_their_own_pictures(workspace, tmp_path: Path) -> None:
    app, bridge, page, results, _errors = workspace
    page.openFormDialog(None)
    page.findChild(object, "materialFormName").setProperty("text", "青衫少年")
    page.findChild(object, "materialFormNote").setProperty("text", "洗得发白的青衫")
    page.findChild(object, "materialFormEvent").setProperty("text", "3")
    del results[:]
    page.submitForm()
    payload = _pump(app, results, "material", "1")
    form = payload["forms"][0]
    assert form["name"] == "青衫少年"
    assert form["event_id"] == "3"
    # 形态写的是编号，页面上要看到那一步的名字。
    assert page.eventName("3") == "林舟出场"

    source = tmp_path / "ref.png"
    source.write_bytes(PNG)
    del results[:]
    bridge.addMaterialImage("1", form["id"], "setting", str(source), "设定图")
    payload = _pump(app, results, "material", "1")
    image = payload["forms"][0]["images"][0]
    assert image["kind_label"] == "设定图"
    copied = Path(image["path"])
    assert copied.is_file()
    assert copied.parent.parent.parent == tmp_path / "material_material"

    del results[:]
    bridge.removeMaterialImage("1", form["id"], image["id"])
    payload = _pump(app, results, "material", "1")
    assert payload["forms"][0]["images"] == []


def test_material_follows_the_node_not_the_page(workspace) -> None:
    app, bridge, page, results, _errors = workspace
    del results[:]
    bridge.saveMaterialText("2", "一枚发着紫光的珠子", "")
    _pump(app, results, "material", "2")

    # 切到人物：看到的是人物自己的那一份，不是刚写的那份。
    del results[:]
    page.select("1")
    _pump(app, results, "material", "1")
    app.processEvents()
    assert _value(page.property("summaryDraft")) == ""

    del results[:]
    page.select("2")
    _pump(app, results, "material", "2")
    app.processEvents()
    assert _value(page.property("summaryDraft")) == "一枚发着紫光的珠子"
