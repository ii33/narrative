"""角色 wiki：圣经与形象存在工程旁边，页面上的状态仍然来自属性记录。"""

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


def _pump(
    app: QApplication,
    results: list[tuple[str, dict]],
    kind: str,
    errors: list[tuple[str, str]] | None = None,
) -> dict:
    deadline = time.monotonic() + 8
    while not any(item[0] == kind for item in results) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    matched = [payload for name, payload in results if name == kind]
    assert matched, f"没有收到 {kind} 结果（报错：{errors}）"
    return matched[-1]


def _value(item: Any) -> Any:
    return item.toVariant() if hasattr(item, "toVariant") else item


@pytest.fixture()
def workspace(tmp_path: Path):
    """一个有两个人物、一条属性的工程，停在 wiki 页。"""
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    project = tmp_path / "wiki.sqlite3"
    ops = Operations.create_graph_project(project)
    candidate = ops.graph_propose(
        [
            {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
            {"kind": "node", "id": "2", "node_type": "event", "name": "林舟出场"},
            {"kind": "node", "id": "3", "node_type": "person", "name": "韩英"},
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
             "property_id": "identity", "value": "总经理", "event_id": "2"},
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

    # engine 必须活到用例结束：它被回收，QML 对象就跟着散架，页面上的函数也找不到了。
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    try:
        engine.load(QUrl.fromLocalFile(str(QML_PATH)))
        window: Any = engine.rootObjects()[0]
        window.setProperty("currentPage", "wiki")
        bridge.openProject(str(project))
        _pump(app, results, "project")
        _pump(app, results, "browse")
        _pump(app, results, "wiki_index")
        page = window.findChild(object, "wikiPage")
        assert page is not None, "wiki 页没有加载"
        # 清单出来就自动选中第一个角色，那一页也要等它到齐：不等的话，后面每一次
        # 等待都会被这条迟到的结果骗过去，看起来像是新请求成功了。
        _pump(app, results, "wiki")
        assert page.property("selectedId") == "1"
        yield app, bridge, page, results, errors
        assert not errors
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()


def test_the_page_lists_people_and_shows_what_is_known_so_far(workspace) -> None:
    app, _bridge, page, results, errors = workspace
    app.processEvents()
    payload = next(item for item in results if item[0] == "wiki")[1]

    characters = _value(page.property("characters"))
    assert [item["name"] for item in characters] == ["林舟", "韩英"]
    # 编号带类型前缀，和浏览页看到的是同一个写法。
    assert [item["label_id"] for item in characters] == ["P1", "P3"]
    assert not errors

    assert payload["node"]["name"] == "林舟"
    state = _value(page.property("stateRows"))
    assert [(row["label"], row["value"]) for row in state] == [("身份", "总经理")]
    # 停在最新，就没有「读到哪一步」这回事。
    assert payload["cut_name"] == ""


def test_bible_and_appearances_round_trip(workspace) -> None:
    app, bridge, page, results, _errors = workspace

    del results[:]
    bridge.saveWikiBible("1", "嘴硬心软，遇事先笑再算账。")
    payload = _pump(app, results, "wiki")
    app.processEvents()
    assert payload["wiki"]["bible"] == "嘴硬心软，遇事先笑再算账。"
    assert _value(page.property("bibleDraft")) == "嘴硬心软，遇事先笑再算账。"

    del results[:]
    bridge.addWikiAppearance("1", "青衫少年", "洗得发白的青衫", "2")
    payload = _pump(app, results, "wiki")
    appearance = payload["wiki"]["appearances"][0]
    assert appearance["name"] == "青衫少年"
    # 事件编号在页面上要变成那一步的名字。
    assert appearance["event_name"] == "林舟出场"

    del results[:]
    bridge.updateWikiAppearance("1", appearance["id"], "白衣少年", "换了身白衣", "")
    payload = _pump(app, results, "wiki")
    edited = payload["wiki"]["appearances"][0]
    assert (edited["name"], edited["note"], edited["event_id"]) == ("白衣少年", "换了身白衣", "")
    assert edited["event_name"] == ""


def test_an_image_is_copied_in_and_removed_with_its_record(workspace, tmp_path: Path) -> None:
    app, bridge, page, results, _errors = workspace

    source = tmp_path / "ref.png"
    source.write_bytes(PNG)
    del results[:]
    bridge.addWikiAppearance("1", "青衫少年", "", "")
    payload = _pump(app, results, "wiki")
    appearance_id = payload["wiki"]["appearances"][0]["id"]

    del results[:]
    bridge.addWikiImage("1", appearance_id, "turnaround", str(source), "正面/侧面/背面")
    payload = _pump(app, results, "wiki")
    image = payload["wiki"]["appearances"][0]["images"][0]
    assert image["kind_label"] == "三视图"
    copied = Path(image["path"])
    assert copied.is_file()
    # 拷进工程的 wiki 目录，而不是引用用户选的路径。
    assert copied.parent.parent.parent == tmp_path / "wiki_wiki"

    del results[:]
    bridge.removeWikiImage("1", appearance_id, image["id"])
    payload = _pump(app, results, "wiki")
    assert payload["wiki"]["appearances"][0]["images"] == []

    # 页面上还开着那张图时 Windows 不放文件，所以删除只是尽力而为：
    # 记录立刻没了，文件在下次读这一页（没人引用它了）时清掉。
    del results[:]
    bridge.loadWiki("1")
    _pump(app, results, "wiki")
    deadline = time.monotonic() + 5
    while copied.exists() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert not copied.exists()


def test_picking_another_character_shows_their_own_page(workspace) -> None:
    app, _bridge, page, results, _errors = workspace

    del results[:]
    page.pick("3")
    payload = _pump(app, results, "wiki")
    app.processEvents()
    assert payload["node"]["name"] == "韩英"
    assert payload["wiki"]["bible"] == ""
    assert _value(page.property("stateRows")) == []
