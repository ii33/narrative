"""数据浏览页的分类界面：按大类分组、移动分类、改分类表、反向引用。

这些用例走真实的 QML：分类逻辑在 Python 层，能不能看见、能不能点动在界面上。
"""

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
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "person", "name": "老周"},
                {"kind": "node", "id": "3", "node_type": "item", "name": "铜钥匙"},
                {"kind": "node", "id": "4", "node_type": "event", "name": "开场"},
                {"kind": "node", "id": "5", "node_type": "event", "name": "后来又过了些日子"},
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
        _pump(app, results, "graph_view")
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
        _pump(app, results, "graph_view")
        page = window.findChild(object, "browsePage")
        page.select("3")
        _pump(app, results, "detail", "3")
        _pump(app, results, "node_properties", "3")
        app.processEvents()

        latest = {row["label"] for row in _value(page.property("propertyRows"))}
        assert latest == {"持有者", "状态"}

        # 第 1 个选项是第一个事件「开场」，手镯的状态还没披露。
        del results[:]
        page.pickCut(1)
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

    # 补录不直接进正式资料：先成为候选，等人在审核页接受。
    assert payload["status"] == "pending"
    assert payload["candidate_id"]


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
        assert added["status"] == "pending"

        reopened = open_graph_project(project)
        candidate = reopened.property_candidate(added["candidate_id"])
        record = next(
            item for item in candidate["records"] if item["kind"] == "property_value"
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
