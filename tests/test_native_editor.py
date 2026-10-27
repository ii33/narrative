"""节点编辑器：取消要真的收起表单；一条重复的记录要能单独失效。"""

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
    """一个人物，一条属性上有两条一模一样的披露（不同事件）。"""
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    project = tmp_path / "editor.sqlite3"
    ops = Operations.create_graph_project(project)
    source = ops.import_source("第一章", "林舟是总经理。林舟还是总经理。")
    candidate = ops.graph_propose(
        [
            {"kind": "node", "id": "1", "node_type": "person", "name": "林舟",
             "disclosure": {"source_id": source, "start": 0}},
            {"kind": "node", "id": "2", "node_type": "event", "name": "林舟出场",
             "disclosure": {"source_id": source, "start": 0}},
            {"kind": "node", "id": "3", "node_type": "event", "name": "再次提到",
             "disclosure": {"source_id": source, "start": 6}},
        ],
        ops.graph_project_info()["revision"],
    )
    ops.graph_accept(candidate, 1)
    revision = ops.graph_project_info()["revision"]
    property_candidate = ops.property_propose(
        [
            {"kind": "property_definition", "id": "identity", "value_type": "string",
             "cardinality": "one", "persistence": "until_changed"},
            {"kind": "property_value", "id": "v1", "node_id": "1",
             "property_id": "identity", "value": "总经理", "event_id": "2",
             "evidence": {"source_id": source, "quote": "林舟是总经理"}},
            {"kind": "property_value", "id": "v2", "node_id": "1",
             "property_id": "identity", "value": "总经理", "event_id": "3",
             "evidence": {"source_id": source, "quote": "林舟还是总经理"}},
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
        editor = page.findChild(object, "nodeEditor")
        assert editor is not None
        editor.openFor("1")
        _pump(app, results, "node_editor")
        yield app, bridge, page, editor, results, errors, project
        assert not errors
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()


def test_two_identical_records_are_both_listed(workspace) -> None:
    app, _bridge, _page, editor, results, _errors, _project = workspace
    app.processEvents()
    row = next(
        item for item in _value(editor.property("properties"))
        if item["label"] == "身份"
    )
    assert [record["id"] for record in row["records"]] == ["v1", "v2"]


def test_cancel_really_closes_the_correction_form(workspace) -> None:
    app, _bridge, _page, editor, results, _errors, _project = workspace
    row = next(
        item for item in _value(editor.property("properties"))
        if item["label"] == "身份"
    )
    editor.startEdit(row, row["records"][1])
    app.processEvents()
    assert editor.property("editKey") != ""

    # 取消＝什么都没发生：表单的显示条件是 editKey，只清记录 id 关不掉。
    editor.cancelForms()
    app.processEvents()
    assert editor.property("editKey") == ""
    assert editor.property("editRecordId") == ""
    assert editor.property("retractKey") == ""


def test_one_duplicate_can_be_retracted_alone(workspace) -> None:
    app, _bridge, _page, editor, results, _errors, _project = workspace
    row = next(
        item for item in _value(editor.property("properties"))
        if item["label"] == "身份"
    )
    second = row["records"][1]

    editor.startRetractRecord(row, second)
    app.processEvents()
    assert _value(editor.property("retractTargets")) == ["v2"]
    # 表单上说清撤的是哪一条，否则两条一样的话看不出区别。
    assert "再次提到" in str(editor.property("retractLabel"))

    editor.setProperty("retractReason", "重复的说法")
    del results[:]
    editor.submitRetract()
    payload = _pump(app, results, "property_retract")
    assert payload["retracted"] == ["v2"]
    # 提交之后表单收起。
    assert editor.property("retractKey") == ""

    row = next(
        item for item in _value(_pump(app, results, "node_editor")["properties"])
        if item["label"] == "身份"
    )
    assert [record["id"] for record in row["records"]] == ["v1"]


def test_delete_takes_one_duplicate_and_says_it_never_counted(workspace) -> None:
    """删除是「从来不算」，不是「从这一步起不算」——在更早的阅读位置上也看不到。"""
    app, _bridge, _page, editor, results, _errors, project = workspace
    row = next(
        item for item in _value(editor.property("properties"))
        if item["label"] == "身份"
    )
    first = row["records"][0]  # 事件 E2

    del results[:]
    editor.deleteRecords([first["id"]])
    payload = _pump(app, results, "property_delete")
    assert payload["retracted"] == ["v1"]

    # 停在 E2 这一步：删除的那条从来不算，所以这里也不该有它。
    ops = Operations.open_graph_project(project)
    at_early = ops.property_node_summary("1", cut="2")
    values = [
        record["id"]
        for item in at_early["properties"]
        for record in item["records"]
    ]
    assert "v1" not in values


def test_delete_row_takes_every_record(workspace) -> None:
    app, _bridge, _page, editor, results, _errors, _project = workspace
    row = next(
        item for item in _value(editor.property("properties"))
        if item["label"] == "身份"
    )
    del results[:]
    editor.deleteRow(row)
    payload = _pump(app, results, "property_delete")
    assert sorted(payload["retracted"]) == ["v1", "v2"]


def test_retracting_a_whole_row_still_takes_every_record(workspace) -> None:
    app, _bridge, _page, editor, results, _errors, _project = workspace
    row = next(
        item for item in _value(editor.property("properties"))
        if item["label"] == "身份"
    )
    editor.startRetract(row)
    app.processEvents()
    assert sorted(_value(editor.property("retractTargets"))) == ["v1", "v2"]

    editor.setProperty("retractReason", "整条都不要")
    del results[:]
    editor.submitRetract()
    payload = _pump(app, results, "property_retract")
    assert sorted(payload["retracted"]) == ["v1", "v2"]
