"""节点资料：详情栏里的资料卡，按阅读位置过滤，默认折叠成标题。"""

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
    """等一条结果；给了 node_id 就只认那个节点的。"""
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
    """一个人物、一件物品、两个事件；进工程后停在人物上。"""
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    project = tmp_path / "material.nproj"
    ops = Operations.create_graph_project(project)
    source = ops.import_source("第一章", "林舟出场了。火纹石认主。")
    candidate = ops.graph_propose(
        [
            {"kind": "node", "id": "1", "node_type": "person", "name": "林舟",
             "disclosure": {"source_id": source, "start": 0}},
            {"kind": "node", "id": "2", "node_type": "item", "name": "火纹石",
             "disclosure": {"source_id": source, "start": 6}},
            {"kind": "node", "id": "3", "node_type": "event", "name": "林舟出场",
             "disclosure": {"source_id": source, "start": 0}},
            {"kind": "node", "id": "4", "node_type": "event", "name": "火纹石认主",
             "disclosure": {"source_id": source, "start": 6}},
            {"kind": "node", "id": "5", "node_type": "scene", "name": "房间冲突",
             "events": ["3", "4"], "disclosure": {"source_id": source, "start": 0}},
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

    bridge = NativeBridge()
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


def test_a_node_starts_with_no_cards_and_a_timeline_to_pick_from(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    app.processEvents()
    material = _value(page.property("material"))
    assert material["cards"] == []
    assert material["hidden_count"] == 0
    # 生效/失效挑的是「哪一步」：一列一层，粗的在前。
    assert [row["label"] for row in material["moments"]["levels"]] == ["场景", "事件"]
    assert [row["label_id"] for row in material["moments"]["nodes"]] == ["E3", "S5", "E4"]
    assert _value(page.momentRows(0, "")) == [
        {"id": "5", "label_id": "S5", "name": "房间冲突", "level": "scene",
         "kind_label": "场景", "parent_id": "", "rank": 0},
    ]
    assert [row["id"] for row in _value(page.momentRows(1, "5"))] == ["3", "4"]

    del results[:]
    page.select("2")
    _pump(app, results, "material", "2")
    assert page.property("materialNodeId") == "2"


def test_a_card_is_added_from_the_dialog_with_a_window(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    page.openCardDialog(None)
    page.findChild(object, "materialCardTitle").setProperty("text", "青衫少年")
    page.findChild(object, "materialCardDetail").setProperty("text", "洗得发白的青衫")
    # 生效：在分级选择器里先点场景、再点它底下的第一个事件；失效不指定。
    page.openMomentPicker("from", "")
    page.pickMoment(0, "5")
    page.pickMoment(1, "3")
    page.confirmMoment()

    del results[:]
    page.submitCard()
    payload = _pump(app, results, "material", "1")
    app.processEvents()
    card = payload["cards"][0]
    assert card["title"] == "青衫少年"
    assert card["detail"] == "洗得发白的青衫"
    assert card["valid_from"] == "3"
    assert card["valid_to"] == ""

    # 刚建的那张直接展开，其余默认折叠。
    expanded = _value(page.property("expandedCards"))
    assert expanded.get(card["id"]) is True
    # 资料不是披露：属性行里不会多出一条「资料」。
    labels = [row["label"] for row in _value(page.property("propertyRows"))]
    assert labels == ["身份"]


def test_the_reading_position_decides_which_cards_are_shown(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    # 事件粒度：这张工程没有场景，阅读位置只能按事件走。
    del results[:]
    page.pickGranularity("event")
    _pump(app, results, "browse")
    # 相关节点默认开：选了步骤后列表会收窄，被筛掉的节点会失去选中。这里关掉。
    del results[:]
    page.toggleRelated()
    _pump(app, results, "browse")

    page.openCardDialog(None)
    page.findChild(object, "materialCardTitle").setProperty("text", "认主之后")
    page.openMomentPicker("from", "")
    page.pickMoment(0, "5")
    page.pickMoment(1, "4")
    page.confirmMoment()
    del results[:]
    page.submitCard()
    payload = _pump(app, results, "material", "1")
    # 停在「最新」＝读完全书：E4 已经读到，所以它算数。
    assert [card["title"] for card in payload["cards"]] == ["认主之后"]

    # 把阅读位置挪回 E3：这一步它还没开始，只剩个数量提示。
    del results[:]
    page.pickCut(1)
    _pump(app, results, "browse")
    payload = _pump(app, results, "material", "1")
    app.processEvents()
    assert payload["cards"] == []
    assert payload["hidden_count"] == 1

    # 走到 E4：它出现了。
    del results[:]
    page.pickCut(2)
    _pump(app, results, "browse")
    payload = _pump(app, results, "material", "1")
    app.processEvents()
    assert [card["title"] for card in payload["cards"]] == ["认主之后"]
    assert payload["hidden_count"] == 0


def test_an_image_carries_its_own_caption(workspace, tmp_path: Path) -> None:
    app, bridge, page, results, _errors = workspace
    page.openCardDialog(None)
    page.findChild(object, "materialCardTitle").setProperty("text", "青衫少年")
    del results[:]
    page.submitCard()
    payload = _pump(app, results, "material", "1")
    card_id = payload["cards"][0]["id"]

    source = tmp_path / "ref.png"
    source.write_bytes(PNG)
    del results[:]
    bridge.addMaterialImage("1", card_id, str(source), "正面")
    payload = _pump(app, results, "material", "1")
    image = payload["cards"][0]["images"][0]
    assert image["caption"] == "正面"
    copied = Path(image["path"])
    assert copied.is_file()
    assert copied.parent.parent.parent == tmp_path / "material.nproj" / "material"

    del results[:]
    bridge.updateMaterialImageCaption("1", card_id, image["id"], "正面，束发")
    payload = _pump(app, results, "material", "1")
    assert payload["cards"][0]["images"][0]["caption"] == "正面，束发"

    del results[:]
    bridge.removeMaterialImage("1", card_id, image["id"])
    payload = _pump(app, results, "material", "1")
    assert payload["cards"][0]["images"] == []


def test_images_can_be_added_in_the_editor(workspace, tmp_path: Path) -> None:
    app, _bridge, page, results, _errors = workspace
    source = tmp_path / "ref.png"
    source.write_bytes(PNG)

    page.openCardDialog(None)
    page.findChild(object, "materialCardTitle").setProperty("text", "青衫少年")
    # 编辑器里一次选多张，先摆在草稿里；说明也在这里写。
    page.addDraftImages([source.as_uri()])
    page.draftCaption(0, "正面")
    assert len(_value(page.property("draftImages"))) == 1

    del results[:]
    page.submitCard()
    payload = _pump(app, results, "material", "1")
    card = payload["cards"][0]
    assert len(card["images"]) == 1
    assert card["images"][0]["caption"] == "正面"
    assert Path(card["images"][0]["path"]).is_file()

    # 再打开编辑器：已有的图会回到草稿里；移除它再保存，图和文件一起走。
    page.openCardDialog(card)
    assert len(_value(page.property("draftImages"))) == 1
    page.dropDraftImage(0)
    del results[:]
    page.submitCard()
    payload = _pump(app, results, "material", "1")
    assert payload["cards"][0]["images"] == []


def test_clicking_a_picture_opens_a_preview(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    page.openCardDialog(None)
    page.findChild(object, "materialCardTitle").setProperty("text", "青衫少年")
    del results[:]
    page.submitCard()
    payload = _pump(app, results, "material", "1")
    card = payload["cards"][0]

    preview = page.findChild(object, "materialImagePreview")
    assert preview is not None
    assert preview.property("visible") is False

    page.previewImage({"path": card["images"][0]["path"] if card["images"] else "C:/x.png",
                       "caption": "正面"})
    app.processEvents()
    assert preview.property("visible") is True
    assert preview.property("caption") == "正面"
    assert str(preview.property("source")).startswith("file:///")


def test_cards_belong_to_the_node_not_the_page(workspace) -> None:
    app, bridge, page, results, _errors = workspace
    del results[:]
    bridge.addMaterialCard("2", "认主之后", "紫光大盛", "", "", "")
    _pump(app, results, "material", "2")

    del results[:]
    page.select("1")
    _pump(app, results, "material", "1")
    app.processEvents()
    assert _value(page.property("material"))["cards"] == []

    del results[:]
    page.select("2")
    _pump(app, results, "material", "2")
    app.processEvents()
    assert [card["title"] for card in _value(page.property("material"))["cards"]] == ["认主之后"]
