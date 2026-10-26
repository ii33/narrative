"""数据浏览页的分类界面：按大类分组、移动分类、改分类表、反向引用。

这些用例走真实的 QML：分类逻辑在 Python 层，能不能看见、能不能点动在界面上。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import shiboken6
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge
from narrative.operations import Operations
from narrative.project_catalog import open_graph_project

pytestmark = pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") != "offscreen",
    reason="Qt Quick 用例需要 offscreen 平台",
)


def _value(item: object) -> object:
    """QML hands JS arrays back as plain lists; only typed values need unwrapping."""
    return item.toVariant() if hasattr(item, "toVariant") else item


def _node_of(payload: dict) -> str:
    """Both a dossier and a property summary say which node they describe."""
    return str(payload.get("node_id") or (payload.get("node") or {}).get("id") or "")


def _pump(
    app: QApplication,
    results: list[tuple[str, dict]],
    kind: str,
    node_id: str = "",
) -> dict:
    """Wait for one result, optionally for a specific node's payload.

    Waiting on the kind alone would accept the previous node's dossier, which is
    exactly what a jump to another node must not do.
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
    assert matched, f"没有收到 {kind} 结果"
    return matched[-1]


def _seed(path: Path) -> Path:
    ops = Operations.create_graph_project(path)
    # 节点带披露位置：按时间显示时列表要用它判断"出场了没有"。
    source_id = ops.import_source("第一章", "林舟脸色铁青，身受重伤。")
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "source_order", "id": "order_1", "source_id": source_id,
                 "ordinal": 1, "context_id": "canon"},
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟",
                 "disclosure": {"source_id": source_id, "start": 0}},
                {"kind": "node", "id": "2", "node_type": "person", "name": "老周",
                 "disclosure": {"source_id": source_id, "start": 1}},
                {"kind": "node", "id": "3", "node_type": "item", "name": "铜钥匙",
                 "disclosure": {"source_id": source_id, "start": 2}},
                {"kind": "node", "id": "4", "node_type": "event", "name": "开场",
                 "participants": ["1", "3"],
                 "disclosure": {"source_id": source_id, "start": 3}},
                {"kind": "node", "id": "5", "node_type": "event", "name": "后来又过了些日子",
                 "participants": ["2"],
                 "disclosure": {"source_id": source_id, "start": 4}},
                {"kind": "node", "id": "6", "node_type": "item", "name": "令牌",
                 "disclosure": {"source_id": source_id, "start": 5}},
                # 两个场景各装一个事件：默认粒度是场景，切点落在场景的最后一个事件上。
                {"kind": "node", "id": "7", "node_type": "scene", "name": "第一个场景",
                 "events": ["4"],
                 "disclosure": {"source_id": source_id, "start": 3}},
                {"kind": "node", "id": "8", "node_type": "scene", "name": "第二个场景",
                 "events": ["5"],
                 "disclosure": {"source_id": source_id, "start": 4}},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    ops.property_accept(
        ops.property_propose(
            [
                {"kind": "property_definition", "id": "state", "value_type": "string",
                 "cardinality": "one", "persistence": "until_changed"},
                {"kind": "property_definition", "id": "holder", "value_type": "node",
                 "cardinality": "one", "persistence": "until_changed"},
                {"kind": "property_definition", "id": "cultivation", "value_type": "string",
                 "cardinality": "one", "persistence": "until_changed"},
                {"kind": "property_definition", "id": "cultivation_stage",
                 "value_type": "string", "cardinality": "one", "persistence": "until_changed"},
                {"kind": "property_value", "id": "v1", "node_id": "1",
                 "property_id": "state", "value": "脸色铁青", "event_id": "4"},
                {"kind": "property_value", "id": "v2", "node_id": "1",
                 "property_id": "state", "value": "身受重伤", "event_id": "4"},
                {"kind": "property_value", "id": "v3", "node_id": "3",
                 "property_id": "holder", "value": "2", "event_id": "4"},
                # 老周：同一方面两个名字，用来验证界面的归并入口。
                {"kind": "property_value", "id": "v4", "node_id": "2",
                 "property_id": "cultivation", "value": "灵寂期", "event_id": "4"},
                {"kind": "property_value", "id": "v5", "node_id": "2",
                 "property_id": "cultivation_stage", "value": "元婴初期", "event_id": "4"},
                # 手镯的状态在更晚的事件才披露，用来验证阅读位置。
                {"kind": "property_value", "id": "v6", "node_id": "3",
                 "property_id": "state", "value": "完好无损", "event_id": "5"},
                # 林舟在更晚的事件才拿到令牌：反向引用也要跟着阅读位置。
                {"kind": "property_value", "id": "v7", "node_id": "6",
                 "property_id": "holder", "value": "1", "event_id": "5"},
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )
    return path


@pytest.fixture()
def workspace(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    project = _seed(tmp_path / "categories.sqlite3")
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
        bridge.openProject(str(project))
        _pump(app, results, "project")
        _pump(app, results, "browse")
        page = window.findChild(object, "browsePage")
        assert page is not None
        page.select("1")
        _pump(app, results, "detail", "1")
        _pump(app, results, "node_properties", "1")
        yield app, bridge, page, results, errors
        assert not errors
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()


def test_one_property_shows_under_two_categories(workspace) -> None:
    app, _bridge, page, _results, _errors = workspace
    app.processEvents()

    groups = _value(page.property("categoryGroups"))
    assert [group["key"] for group in groups] == ["外貌", "能力与身体状态"]
    assert [group["count"] for group in groups] == [1, 1]
    # 同一属性被拆成两条切片，各自只带自己的记录。
    assert [item["valueText"] for group in groups for item in group["items"]] == [
        "脸色铁青", "身受重伤",
    ]


def test_the_holder_of_an_item_is_listed_on_the_person(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    page.jumpTo("2")
    _pump(app, results, "detail", "2")
    _pump(app, results, "node_properties", "2")
    app.processEvents()

    inbound = _value(page.property("inboundRows"))
    assert [(row["node_name"], row["category"]) for row in inbound] == [
        ("铜钥匙", "持有与使用"),
    ]


def test_moving_a_record_from_the_pane_regroups_it(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    # 夹具里的 node_properties 已经在列表里；清掉才不会把旧结果当成新的。
    del results[:]

    # 点记录上的分类标签：先把目标记在页面上，再选大类。
    page.askCategory("外貌", ["v1"])
    app.processEvents()
    assert _value(page.property("pickerTargets")) == ["v1"]
    page.applyCategory("心理与意图")
    moved = _pump(app, results, "property_categorize")
    assert moved["category"] == "心理与意图"
    _pump(app, results, "node_properties", "1")
    app.processEvents()

    groups = _value(page.property("categoryGroups"))
    assert [group["key"] for group in groups] == ["能力与身体状态", "心理与意图"]
    # 取值没变，只是换了显示位置。
    assert {item["valueText"] for group in groups for item in group["items"]} == {
        "身受重伤", "脸色铁青",
    }


def test_merging_two_names_from_the_pane(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    page.jumpTo("2")
    _pump(app, results, "detail", "2")
    _pump(app, results, "node_properties", "2")
    app.processEvents()

    rows = _value(page.property("propertyRows"))
    item = next(row for row in rows if row["label"] == "修为")
    # 同一方面两个名字：界面要说出来，并给出归并入口。
    assert item["names"] == "cultivation / cultivation_stage"
    assert item["nameIds"] == ["cultivation", "cultivation_stage"]

    del results[:]
    page.askMerge(item["nameIds"])
    app.processEvents()
    page.applyMerge("cultivation")
    merged = _pump(app, results, "property_merge")
    assert (merged["alias_id"], merged["target_id"]) == ("cultivation_stage", "cultivation")
    _pump(app, results, "node_properties", "2")
    app.processEvents()

    rows = _value(page.property("propertyRows"))
    item = next(row for row in rows if row["label"] == "修为")
    assert item["names"] == ""
    assert len(item["records"]) == 2


def test_renaming_an_aspect_from_the_editor(workspace) -> None:
    """改名在节点编辑器里：一个方面铺成两个名字时，整行一起换。"""
    app, _bridge, page, results, _errors = workspace
    page.jumpTo("2")
    _pump(app, results, "detail", "2")
    _pump(app, results, "node_properties", "2")
    app.processEvents()

    del results[:]
    editor = page.findChild(object, "nodeEditor")
    editor.openFor("2")
    payload = _pump(app, results, "node_editor")
    app.processEvents()

    item = next(row for row in payload["properties"] if row["label"] == "修为")
    editor.startRename(item)
    app.processEvents()
    # 改的是整行：同一方面用了两个名字时，两个名字一起换，否则下一条披露会把旧名字带回来。
    assert _value(editor.property("renameNames")) == ["cultivation", "cultivation_stage"]

    editor.setProperty("renameName", "修真境界")
    del results[:]
    editor.submitRename()
    renamed = _pump(app, results, "property_rename")
    assert renamed["to_id"] == "修真境界"

    payload = _pump(app, results, "node_editor")
    labels = [row["label"] for row in payload["properties"]]
    assert "修真境界" in labels
    assert "修为" not in labels


def test_the_editor_fits_its_own_actions(workspace) -> None:
    """编辑器的属性行不能比面板宽：宽出来的部分会把「更正/失效/改名」推到面板外。"""
    app, _bridge, page, results, _errors = workspace
    del results[:]
    editor = page.findChild(object, "nodeEditor")
    editor.openFor("1")
    _pump(app, results, "node_editor")
    app.processEvents()

    scroll = editor.findChild(object, "editorPropertyScroll")
    listing = editor.findChild(object, "editorPropertyList")
    assert scroll is not None and listing is not None
    # 内容宽度跟视口走：绑到 parent.width 时会被最长的值撑开，行尾的动作就看不见了。
    assert listing.property("width") <= scroll.property("availableWidth") + 1


def test_editing_the_vocabulary_changes_the_grouping(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    del results[:]

    page.openCategorySettings()
    app.processEvents()
    editor = page.findChild(object, "categoryEditor")
    assert editor is not None
    # 一条一行；外貌改名成外形，已归在该大类下的记录要跟着迁移。
    editor.setProperty("text", "基本信息\n外形\n身份与经历\n能力与身体状态\n"
                               "持有与使用\n关系\n心理与意图")
    page.saveCategorySettings()
    saved = _pump(app, results, "property_categories")
    assert saved["saved"] is True
    assert saved["moved"] == 1
    _pump(app, results, "node_properties", "1")
    app.processEvents()

    assert _value(page.property("vocabulary"))[1] == "外形"
    groups = _value(page.property("categoryGroups"))
    assert [group["key"] for group in groups] == ["外形", "能力与身体状态"]


def test_stepping_the_reading_position(workspace) -> None:
    """上一个/下一个按事件走，两头分别接上「最新」和第一个事件。"""
    app, _bridge, page, results, _errors = workspace
    # 这个用例测的是步进：先关掉默认的相关节点筛选，否则选中节点会被筛出去。
    del results[:]
    page.toggleRelated()
    _pump(app, results, "browse")
    page.select("3")
    _pump(app, results, "detail", "3")
    _pump(app, results, "node_properties", "3")
    app.processEvents()

    # 从「最新」往回退一步，落在最后一个场景上（它锚在事件 5）。
    del results[:]
    page.stepCut(-1)
    _pump(app, results, "browse")
    assert page.property("cutEventId") == "5"
    _pump(app, results, "node_properties", "3")

    # 再往前一步：第一个场景，锚在事件 4。
    del results[:]
    page.stepCut(-1)
    _pump(app, results, "browse")
    assert page.property("cutEventId") == "4"

    # 已经是第一个步骤，再往前不动。
    page.stepCut(-1)
    assert page.property("cutEventId") == "4"

    # 再往后回到最后一个场景，然后回到「最新」。
    del results[:]
    page.stepCut(1)
    _pump(app, results, "browse")
    assert page.property("cutEventId") == "5"

    del results[:]
    page.stepCut(1)
    _pump(app, results, "browse")
    assert page.property("cutEventId") == ""
    assert page.property("cutLater") == 0


def test_inbound_records_follow_the_reading_cut(workspace) -> None:
    """还没读到的关系不该在对方页面上出现。"""
    app, _bridge, page, results, _errors = workspace
    del results[:]
    page.jumpTo("1")
    _pump(app, results, "detail", "1")
    _pump(app, results, "node_properties", "1")
    app.processEvents()
    # 最新：令牌已经记在林舟名下。
    assert [row["node_name"] for row in _value(page.property("inboundRows"))] == ["令牌"]

    # 退到第一个事件：令牌还没出现，这条反向引用也不该显示。
    del results[:]
    page.pickCut(1)
    _pump(app, results, "node_properties", "1")
    app.processEvents()
    assert _value(page.property("inboundRows")) == []


def test_the_list_narrows_with_the_time_filter(workspace) -> None:
    """「按时间显示」打开后，列表只列已经出场过的节点。"""
    app, _bridge, page, results, _errors = workspace
    # 这个用例测的是时间收窄：先关掉默认的相关节点筛选，否则两种收窄混在一起。
    del results[:]
    page.toggleRelated()
    _pump(app, results, "browse")
    assert page.property("total") == 8

    # 打开开关但停在「最新」：没有收窄。
    del results[:]
    page.toggleTimeFilter()
    _pump(app, results, "browse")
    assert page.property("timeFilter") is True
    assert page.property("total") == 8

    # 切到第一个场景（锚在事件 4，披露位置 3）：之后出场的 5、6、8 不在列表里。
    del results[:]
    page.pickCut(1)
    _pump(app, results, "browse")
    app.processEvents()
    assert page.property("total") == 5

    # 关掉开关：列表恢复全书。
    del results[:]
    page.toggleTimeFilter()
    _pump(app, results, "browse")
    assert page.property("total") == 8


def test_a_node_that_has_not_appeared_yet_loses_the_selection(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    del results[:]
    page.toggleTimeFilter()
    _pump(app, results, "browse")

    del results[:]
    page.select("6")
    _pump(app, results, "detail", "6")
    _pump(app, results, "node_properties", "6")

    # 收窄到第一个事件后，令牌还没出场：详情不能留在屏幕上。
    del results[:]
    page.pickCut(1)
    _pump(app, results, "browse")
    app.processEvents()
    assert page.property("selectedId") == ""
    assert _value(page.property("propertyRows")) == []


def test_the_granularity_switches_the_step_list(workspace) -> None:
    """默认按场景切；切到事件粒度后，步骤换成事件。"""
    app, _bridge, page, results, _errors = workspace
    steps = _value(page.property("steps"))
    assert page.property("granularity") == "scene"
    assert [step["id"] for step in steps] == ["7", "8"]

    del results[:]
    page.pickGranularity("event")
    _pump(app, results, "browse")
    steps = _value(page.property("steps"))
    assert [step["id"] for step in steps] == ["4", "5"]
    # 停在「最新」时没有位置要保，换粒度还是「最新」。
    assert page.property("cutId") == ""
    assert page.property("cutEventId") == ""


def test_switching_granularity_keeps_the_reading_position(workspace) -> None:
    """场景 7 的切点是它的事件 4；换到事件粒度停在这个事件上，换回来还是场景 7。"""
    app, _bridge, page, results, _errors = workspace
    del results[:]
    page.pickCut(1)
    _pump(app, results, "browse")
    assert page.property("cutId") == "7"
    assert page.property("cutEventId") == "4"

    del results[:]
    page.pickGranularity("event")
    _pump(app, results, "browse")
    assert page.property("cutId") == "4"
    assert page.property("cutEventId") == "4"
    # 下拉停在这个事件上，而不是跳回「最新」。
    assert page.property("steps")[0]["id"] == "4"

    del results[:]
    page.pickGranularity("scene")
    _pump(app, results, "browse")
    assert page.property("cutId") == "7"
    assert page.property("cutEventId") == "4"


def test_related_only_keeps_the_nodes_of_this_step(workspace) -> None:
    """默认只列当前这一步的相关节点；关掉才是全书。"""
    app, _bridge, page, results, _errors = workspace
    assert page.property("relatedOnly") is True

    del results[:]
    page.pickCut(1)
    _pump(app, results, "browse")
    app.processEvents()

    # 第一个场景只有一个事件（4），参与者是林舟和手镯：
    # 列表是场景 + 它的事件 + 这两个参与者。
    ids = {node["id"] for node in _value(page.property("nodes"))}
    assert ids == {"7", "4", "1", "3"}
    # 侧栏计数也要跟着收，否则数字和列表对不上。
    assert page.property("counts") == {"event": 1, "item": 1, "person": 1, "scene": 1}

    del results[:]
    page.toggleRelated()
    _pump(app, results, "browse")
    assert page.property("relatedOnly") is False
    assert page.property("total") == 8
    assert page.property("counts") == {"event": 2, "item": 2, "person": 2, "scene": 2}


def test_jumping_to_a_listed_node_keeps_the_filters(workspace) -> None:
    """跳到一个已经在列表里的节点，不该顺手把默认的相关节点筛选关掉。"""
    app, _bridge, page, results, _errors = workspace
    del results[:]
    page.pickCut(1)
    _pump(app, results, "browse")
    assert page.property("relatedOnly") is True

    # 手镯（3）就是这一步的相关节点，本来就在列表里。
    del results[:]
    page.jumpTo("3")
    _pump(app, results, "detail", "3")
    _pump(app, results, "browse")
    assert page.property("relatedOnly") is True

    # 令牌（6）还没出场，不在列表里：这时才清条件，否则跳过去看不见。
    del results[:]
    page.jumpTo("6")
    _pump(app, results, "detail", "6")
    _pump(app, results, "browse")
    assert page.property("relatedOnly") is False


def test_the_pane_separates_the_latest_state_from_every_disclosure(workspace) -> None:
    """「最新」是这一刻的状态，「全部」是历次披露的并集——能失效之后两者不是一回事。"""
    app, bridge, page, results, _errors = workspace

    # 让林舟的「脸色铁青」从事件 5 起失效：最新只剩一条，全部仍是两条。
    del results[:]
    bridge.applySuperseded(json.dumps([
        {"target_id": "v1", "event_id": "5", "reason": "后来又过了些日子"}
    ]))
    _pump(app, results, "property_supersede")

    page.jumpTo("1")
    _pump(app, results, "browse")
    _pump(app, results, "detail", "1")
    _pump(app, results, "node_properties", "1")
    app.processEvents()

    latest = _value(page.property("propertyRows"))
    assert page.property("propertyScope") == "latest"
    assert [row["valueText"] for row in latest] == ["身受重伤"]
    assert [len(row["records"]) for row in latest] == [1]

    del results[:]
    page.togglePropertyScope("all")
    _pump(app, results, "property_scope")
    _pump(app, results, "node_properties", "1")
    app.processEvents()

    everything = _value(page.property("propertyRows"))
    assert page.property("propertyScope") == "all"
    # 并集：失效的那条也列出来（它单独在一个大类下），带着标记和失效时点。
    assert len(everything) == 2
    assert {row["valueText"] for row in everything} == {"脸色铁青", "身受重伤"}
    invalid = next(row for row in everything if row["valueText"] == "脸色铁青")
    assert [record["valid"] for record in invalid["records"]] == [False]
    assert invalid["records"][0]["invalidated_at"] == "后来又过了些日子"
    assert invalid["countText"] == "共 1 条"

    # 回到「最新」，失效的那条不再列出。
    del results[:]
    page.togglePropertyScope("latest")
    _pump(app, results, "property_scope")
    _pump(app, results, "node_properties", "1")
    app.processEvents()
    assert len(_value(page.property("propertyRows"))[0]["records"]) == 1


def test_a_reading_cut_switches_the_state(tmp_path: Path) -> None:
    """切到某个事件，右侧只显示读到这里时已经披露的内容。"""
    app = QApplication.instance() or QApplication([])
    project = _seed(tmp_path / "cut.sqlite3")
    bridge = NativeBridge(tmp_path)
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    try:
        engine.load(QUrl.fromLocalFile(str(QML_PATH)))
        window: Any = engine.rootObjects()[0]
        bridge.openProject(str(project))
        _pump(app, results, "project")
        _pump(app, results, "browse")
        page = window.findChild(object, "browsePage")
        page.select("3")
        _pump(app, results, "detail", "3")
        _pump(app, results, "node_properties", "3")
        app.processEvents()

        latest = {row["label"] for row in _value(page.property("propertyRows"))}
        assert latest == {"持有者", "状态"}

        # 第 1 个选项是第一个步骤，手镯的状态还没披露。
        del results[:]
        page.pickCut(1)
        _pump(app, results, "browse")
        _pump(app, results, "node_properties", "3")
        app.processEvents()

        rows = _value(page.property("propertyRows"))
        assert {row["label"] for row in rows} == {"持有者"}
        assert page.property("cutEventId") == "4"
        assert page.property("cutLater") == 1
        assert _value(page.property("categoryGroups"))[0]["key"] == "归属与持有"

        # 回到最新，状态又出现。
        del results[:]
        page.backToLatest()
        _pump(app, results, "browse")
        _pump(app, results, "node_properties", "3")
        app.processEvents()
        assert {row["label"] for row in _value(page.property("propertyRows"))} == {
            "持有者", "状态",
        }
        assert page.property("cutEventId") == ""
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()


def test_the_pane_can_add_a_record(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    del results[:]

    page.openAddProperty()
    app.processEvents()
    page.findChild(object, "addPropertyField").setProperty("text", "state")
    page.findChild(object, "addValueField").setProperty("text", "面无表情")
    page.submitAddProperty()
    payload = _pump(app, results, "property_add")

    # 人工补录本身就是接受动作：预检通过即生效，不再多绕一次审核。
    assert payload["status"] == "accepted"
    assert payload["revision"] > 0


def test_a_hand_written_record_is_checked_against_the_source(tmp_path: Path) -> None:
    """补录的引文和模型的一样要能唯一定位，否则预检拒绝。"""
    project = tmp_path / "manual.sqlite3"
    ops = Operations.create_graph_project(project)
    source_id = ops.import_source("第一章", "林舟脸色铁青地走进房间。")
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "林舟进门"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )

    bridge = NativeBridge(tmp_path)
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    try:
        bridge.openProject(str(project))
        _pump(QApplication.instance() or QApplication([]), results, "project")
        bridge.addPropertyRecord({
            "node_id": "1", "property_id": "state", "value": "脸色铁青",
            "event_id": "2", "category": "外貌", "source": "narration",
            "quote": "林舟脸色铁青", "source_id": source_id,
        })
        added = _pump(QApplication.instance(), results, "property_add")
        assert added["status"] == "accepted"
        # 引文推出来的位置就在结果里：落点不在当前窗口时界面要说得出来。
        assert added["anchor"] == "2"

        reopened = open_graph_project(project)
        record = next(
            item for item in reopened.property_records()
            if item["kind"] == "property_value"
        )
        # 位置由引文推出来，不用人填。
        assert record["disclosure"] == {"source_id": source_id, "start": 0}
        assert record["category"] == "外貌"
        assert record["evidence"]["quote"] == "林舟脸色铁青"

        del results[:]
        bridge.addPropertyRecord({
            "node_id": "1", "property_id": "state", "value": "面无表情",
            "event_id": "2", "quote": "原文里没有这句", "source_id": source_id,
        })
        rejected = _pump(QApplication.instance(), results, "property_add")
        assert rejected["status"] == "precheck_failed"
    finally:
        bridge.close()


def test_the_node_editor_corrects_and_invalidates(workspace) -> None:
    """编辑器：改值＝追加一条更正，失效＝撤回记录，都立即生效。"""
    app, _bridge, page, results, _errors = workspace
    editor = page.findChild(object, "nodeEditor")
    assert editor is not None

    del results[:]
    editor.openFor("3")
    payload = _pump(app, results, "node_editor")
    assert payload["node_id"] == "3"
    assert {item["label"] for item in _value(editor.property("properties"))} == {
        "持有者", "状态",
    }

    # 更正：拿「状态」的最新一条，改成新值并同时撤回旧记录。
    item = next(
        entry for entry in _value(editor.property("properties"))
        if entry["label"] == "状态"
    )
    record = item["records"][-1]
    editor.startEdit(item, record)
    assert editor.property("editRecordId") == record["id"]
    editor.setProperty("editValue", "完好如初")
    editor.setProperty("editRetractOld", True)
    del results[:]
    editor.submitEdit()
    corrected = _pump(app, results, "property_correct")
    assert corrected["status"] == "accepted"

    # 失效：整条属性一起撤回，必须写原因。
    del results[:]
    editor.reload()
    payload = _pump(app, results, "node_editor")
    item = next(
        entry for entry in _value(editor.property("properties"))
        if entry["label"] == "持有者"
    )
    editor.startRetract(item)
    assert _value(editor.property("retractTargets")) == [row["id"] for row in item["records"]]
    editor.setProperty("retractReason", "这条读错了")
    del results[:]
    editor.submitRetract()
    retracted = _pump(app, results, "property_retract")
    assert retracted["retracted"]

    # 撤回之后这条属性从编辑器里消失：编辑器只回答「现在是什么」。
    del results[:]
    editor.reload()
    payload = _pump(app, results, "node_editor")
    labels = {entry["label"] for entry in _value(editor.property("properties"))}
    assert "持有者" not in labels



def _window(page: Any) -> Any:
    """这一页所在的窗口。

    同一个 QApplication 里可能还留着上一条用例的窗口，按名字取会拿到已经关掉的那个；
    顺着父链走一定拿到自己的那扇。
    """
    node = page.parent()
    while node is not None:
        if isinstance(node, QWindow):
            return node
        node = node.parent()
    return None


def _quick_window(page: Any) -> Any:
    """这一页所在的 QQuickWindow。

    findChild 拿到的是 QObject 包装，弹层（Dialog）挂在窗口的 overlay 下面而不是页面里，
    所以要从窗口根 item 往下找。
    """
    node = page
    parent = node.parent()
    while parent is not None and not isinstance(parent, QWindow):
        node, parent = parent, parent.parent()
    if parent is None:
        return None
    return shiboken6.wrapInstance(shiboken6.getCppPointer(parent)[0], QQuickWindow)


def _visual(root: Any, name: str) -> Any:
    """按可视化树找控件。

    Repeater 的 delegate 不在 QObject 树上，findChild 找不到——但它们在画面上，
    所以走 childItems。
    """
    stack = [root]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    return None


def test_enter_submits_the_rename_form(workspace) -> None:
    """输入框吃回车：改完名字直接按回车就等于点「确认改名」。"""
    app, _bridge, page, results, _errors = workspace
    page.jumpTo("2")
    _pump(app, results, "detail", "2")
    _pump(app, results, "node_properties", "2")
    app.processEvents()

    del results[:]
    editor = page.findChild(object, "nodeEditor")
    editor.openFor("2")
    payload = _pump(app, results, "node_editor")
    app.processEvents()

    item = next(row for row in payload["properties"] if row["label"] == "修为")
    editor.startRename(item)
    app.processEvents()
    window = _window(page)
    field = _visual(_quick_window(page).contentItem(), "editorRenameName")
    assert field is not None
    field.forceActiveFocus()
    field.setProperty("text", "修真境界")
    app.processEvents()
    assert field.hasActiveFocus()

    del results[:]
    QTest.keyClick(window, Qt.Key_Return)
    renamed = _pump(app, results, "property_rename")
    assert renamed["to_id"] == "修真境界"
    # 表单收起来了，不是还开着。
    assert editor.property("renameKey") == ""


def test_enter_submits_the_add_property_form(workspace) -> None:
    app, _bridge, page, results, _errors = workspace
    del results[:]
    page.openAddProperty()
    app.processEvents()
    page.findChild(object, "addPropertyField").setProperty("text", "state")
    value = page.findChild(object, "addValueField")
    value.setProperty("text", "站着没动")
    value.forceActiveFocus()
    app.processEvents()

    del results[:]
    QTest.keyClick(_window(page), Qt.Key_Return)
    added = _pump(app, results, "property_add")
    assert added["status"] == "accepted"


def test_two_rows_of_one_aspect_do_not_both_open_the_form(workspace) -> None:
    """同一个 state 分在两个大类下时，行头文字一样：表单只能开在点的那一行。"""
    app, _bridge, page, results, _errors = workspace
    del results[:]
    editor = page.findChild(object, "nodeEditor")
    editor.openFor("1")
    payload = _pump(app, results, "node_editor")
    app.processEvents()

    rows = [item for item in payload["properties"] if item["property_id"] == "state"]
    assert len(rows) == 2
    # 两行的 label 都是「状态」——按名字匹配会让两个表单同时出现。
    assert {item["label"] for item in rows} == {"状态"}
    keys = {item["property_id"] + "|" + item["category"] for item in rows}
    assert len(keys) == 2

    editor.startRetract(rows[0])
    assert editor.property("retractKey") == rows[0]["property_id"] + "|" + rows[0]["category"]
    # 只有一行对得上这个 key。
    assert sum(
        1 for item in rows
        if item["property_id"] + "|" + item["category"] == editor.property("retractKey")
    ) == 1


def test_the_node_editor_follows_the_reading_position(workspace) -> None:
    """按阅读位置看时，编辑器只列那一刻已经读到的属性，并且只读。"""
    app, _bridge, page, results, _errors = workspace
    editor = page.findChild(object, "nodeEditor")

    # 第一个场景锚在事件 4：手镯的「状态」在事件 5 才披露，此时还读不到。
    # 先清空，否则 _pump 会立刻拿到上一次的 browse，切点还没写进桥接。
    del results[:]
    page.pickCut(1)
    _pump(app, results, "browse")
    editor.openFor("3")
    payload = _pump(app, results, "node_editor")

    assert payload["at_cut"] is True
    assert payload["cut_name"] == "开场"
    assert payload["later_count"] == 1
    assert {item["label"] for item in _value(editor.property("properties"))} == {"持有者"}
    # 只过滤显示，不封锁编辑：在事件 a 发现问题，就在事件 a 补上。
    assert editor.findChild(object, "editorSubmitNode").property("enabled") is True
    assert editor.findChild(object, "editorAddProperty").property("enabled") is True

    # 回到最新：整份历史回来，编辑重新可用。
    del results[:]
    page.backToLatest()
    _pump(app, results, "browse")
    payload = _pump(app, results, "node_editor")
    assert payload["at_cut"] is False
    assert {item["label"] for item in _value(editor.property("properties"))} == {
        "持有者", "状态",
    }
    assert editor.findChild(object, "editorSubmitNode").property("enabled") is True


def test_a_hand_written_disclosure_changes_the_state_from_its_own_event(
    tmp_path: Path,
) -> None:
    """用户场景：某人在事件 a 瞎了但没提取出来。

    在 a 补一条之后，a 之后的状态立刻变成失明，a 之前仍然是正常——旧值不是
    被删掉，而是从 a 起不再算状态。
    """
    project = tmp_path / "blind.sqlite3"
    ops = Operations.create_graph_project(project)
    source_id = ops.import_source("第一章", "林舟视力正常。他走进房间。后来他瞎了。")
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "出场"},
                {"kind": "node", "id": "3", "node_type": "event", "name": "走进房间"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    ops.property_accept(
        ops.property_propose(
            [
                {"kind": "property_definition", "id": "vision", "value_type": "string",
                 "cardinality": "one", "persistence": "until_changed"},
                {"kind": "property_value", "id": "v1", "node_id": "1",
                 "property_id": "vision", "value": "正常", "event_id": "2",
                 "evidence": {"source_id": source_id, "quote": "林舟视力正常"},
                 "disclosure": {"source_id": source_id, "start": 0}},
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )

    bridge = NativeBridge(tmp_path)
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    try:
        bridge.openProject(str(project))
        _pump(QApplication.instance() or QApplication([]), results, "project")
        bridge.addPropertyRecord({
            "node_id": "1", "property_id": "vision", "value": "失明",
            "event_id": "3", "quote": "他走进房间", "source_id": source_id,
        })
        added = _pump(QApplication.instance(), results, "property_add")
        assert added["status"] == "accepted"
        assert added["outside_window"] is False
    finally:
        bridge.close()

    reopened = open_graph_project(project)
    # 事件 3 之后：失明是状态，正常从这次披露起失效。
    after = reopened.property_node_summary("1", cut="3")["properties"][0]
    assert after["value"] == "失明"
    assert after["invalid_count"] == 1
    # 并集里能看见旧值，以及它从哪次披露起不再算状态。
    union = reopened.property_node_summary(
        "1", cut="3", include_invalid=True
    )["properties"][0]
    assert [row["value"] for row in union["records"]] == ["正常", "失明"]
    assert union["records"][0]["invalidated_at"] == "走进房间"

    # 事件 2：还没读到失明，正常仍然是状态。
    before = reopened.property_node_summary("1", cut="2")["properties"][0]
    assert before["value"] == "正常"
    assert before["invalid_count"] == 0



def test_editing_at_a_reading_position_defaults_to_that_event(workspace) -> None:
    """在当前事件视图里改，默认就落在这个时间点，而不是第一个事件。"""
    app, _bridge, page, results, _errors = workspace

    del results[:]
    page.pickCut(2)  # 第二个场景，锚在事件 5
    _pump(app, results, "browse")

    # 补录：默认披露在事件 5（events 里的第 2 项），不是事件 4。
    page.openAddProperty()
    app.processEvents()
    assert page.findChild(object, "addEventField").property("currentIndex") == 1
    page.findChild(object, "addPropertyDialog").close()

    # 更正：原记录在事件 4，但当前阅读位置是事件 5，默认跟着当前走。
    editor = page.findChild(object, "nodeEditor")
    del results[:]
    editor.openFor("3")
    _pump(app, results, "node_editor")
    item = next(
        entry for entry in _value(editor.property("properties"))
        if entry["label"] == "持有者"
    )
    assert item["records"][-1]["event_name"] == "开场"
    editor.startEdit(item, item["records"][-1])
    assert editor.property("editEventIndex") == 1

    # 回到「最新」：更正回到原记录自己的事件。
    del results[:]
    page.backToLatest()
    _pump(app, results, "browse")
    _pump(app, results, "node_editor")
    item = next(
        entry for entry in _value(editor.property("properties"))
        if entry["label"] == "持有者"
    )
    editor.startEdit(item, item["records"][-1])
    assert editor.property("editEventIndex") == 0
