"""数据浏览主页：进入工程后按分类列出节点。"""

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


def _pump(app: QApplication, results: list[tuple[str, dict]], kind: str) -> dict:
    deadline = time.monotonic() + 8
    while not any(item[0] == kind for item in results) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    matched = [payload for name, payload in results if name == kind]
    assert matched, f"没有收到 {kind} 结果"
    return matched[-1]


def test_workspace_opens_on_the_browse_page_with_categories(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    project = tmp_path / "browse.nproj"
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
            {"kind": "property_value", "id": "p2", "node_id": "1",
             "property_id": "identity", "value": "总经理，后升任董事长", "event_id": "3"},
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

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    window: Any = None
    try:
        engine.load(QUrl.fromLocalFile(str(QML_PATH)))
        window = engine.rootObjects()[0]

        bridge.openProject(str(project))
        _pump(app, results, "project")
        _pump(app, results, "browse")
        assert not errors

        # The workspace lands on the browsing page, not on 原文.
        assert window.property("mode") == "workspace"
        assert window.property("currentPage") == "browse"

        page = window.findChild(object, "browsePage")
        assert page is not None
        assert page.property("total") == 3

        counts = page.property("counts")
        assert counts["person"] == 1
        assert counts["item"] == 1
        assert counts["event"] == 1
        assert counts.get("scene", 0) == 0

        # Filtering by category narrows the list; clearing it restores everything.
        # 过滤在后端做，所以每次都要等一次结果；清掉旧结果才不会被它骗过。
        del results[:]
        page.pickType("item")
        _pump(app, results, "browse")
        assert page.property("total") == 1
        del results[:]
        page.pickType("")
        _pump(app, results, "browse")
        assert page.property("total") == 3

        # 搜索也走同一条查询，界面不再自己筛。
        del results[:]
        page.setProperty("query", "火纹")
        page.load()
        _pump(app, results, "browse")
        assert page.property("total") == 1

        # Selecting a node loads its dossier, evidence included.
        del results[:]
        page.setProperty("query", "")
        page.load()
        _pump(app, results, "browse")
        page.select("1")
        _pump(app, results, "detail")
        detail = page.property("detail")
        assert detail["node"]["name"] == "林舟"

        # ... and its properties, which the right pane lists and can expand.
        _pump(app, results, "node_properties")
        rows = page.property("propertyRows").toVariant()
        assert len(rows) == 1
        assert rows[0]["label"] == "身份"
        assert len(rows[0]["records"]) == 2
        # identity 落在「身份与经历」，不是「其他」：分类由属性名推出来。
        assert rows[0]["category"] == "身份与经历"
        assert rows[0]["categorySource"] == "name"
        # A record names the event it hangs on instead of showing a bare id.
        assert rows[0]["records"][0]["event_name"] == "林舟出场"

        # The same rows are grouped by category, in vocabulary order.
        groups = page.property("categoryGroups").toVariant()
        assert [group["key"] for group in groups] == ["身份与经历"]
        assert groups[0]["count"] == 1

        # Clicking a linked node jumps to it and clears whatever filter hid it.
        del results[:]
        page.pickType("item")
        _pump(app, results, "browse")
        assert page.property("total") == 1
        del results[:]
        page.jumpTo("1")
        _pump(app, results, "browse")
        _pump(app, results, "detail")
        assert page.property("selectedType") == ""
        assert page.property("selectedId") == "1"
        assert page.property("total") == 3
        assert not errors
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()
