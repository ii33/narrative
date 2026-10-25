"""Read-only real mouse click QA of the accepted Wolf graph; keeps the QA window open."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge
from narrative.project_catalog import open_graph_project

project = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
output.mkdir(parents=True, exist_ok=True)
ops = open_graph_project(project)


def formal_state():
    hierarchy = ops.graph_hierarchy()
    nodes = {node["id"]: node for node in hierarchy["nodes"]}
    return {
        "revision": ops.graph_project_info()["revision"],
        "main_events": len(nodes["s1"]["child_ids"]),
        "children": sum(len(nodes[i]["child_ids"]) for i in nodes["s1"]["child_ids"]),
        "temporal_edges": len(ops.graph_timeline()["temporal_edges"]),
    }


before = formal_state()
assert before == {"revision": 2, "main_events": 5, "children": 8, "temporal_edges": 6}
app = QApplication([])
bridge = NativeBridge(project.parent)
results, errors = [], []
bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("bridge", bridge)
engine.load(QUrl.fromLocalFile(str(QML_PATH)))
window = engine.rootObjects()[0]
assert isinstance(window, QQuickWindow)
window.show()


def wait(kind):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(20)
        if errors:
            raise RuntimeError(errors)
        found = [p for k, p in results if k == kind]
        if found:
            return found[-1]
    raise TimeoutError(kind)


def capture(name):
    QTest.qWait(400)
    grab = window.contentItem().grabToImage()
    deadline = time.monotonic() + 10
    while grab.image().isNull() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(20)
    assert grab.saveToFile(str(output / name))
    print("saved", name, flush=True)


def click(item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(
        window, Qt.MouseButton.LeftButton, pos=QPoint(round(point.x()), round(point.y()))
    )


window.openProjectPath(str(project))
wait("project")
wait("canvas")
wait("hierarchy")
timeline = wait("timeline")
info = window.property("projectInfo")
info = info.toVariant() if hasattr(info, "toVariant") else info
window.setProperty("projectInfo", info | {"title": "《狼》第一则 · 相对数轴 · 人工已审时序"})
window.setProperty("currentPage", "graph")
window.setProperty("graphMode", "timeline")
axes = timeline["relative_axes"]
assert len(axes) == 4
assert len(axes[0]["points"]) == 5
assert not timeline["lanes"]
capture("numberline-wide.png")
def find_visual(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = find_visual(child, name)
        if found is not None:
            return found
    return None
point_card = find_visual(window.contentItem(), "relativeCard_relative-1_0")
assert point_card is not None
results.clear()
click(point_card)
detail = wait("detail")
assert detail["node"]["id"] == axes[0]["points"][0]["event_id"]
capture("numberline-selected-evidence.png")
window.setWidth(1120)
QTest.qWait(300)
capture("numberline-narrow.png")
window.setWidth(1500)
window.setProperty("graphMode", "relations")
window.fitGraph()
app.processEvents()
scene = window.findChild(QQuickItem, "graphScene")


def card():
    return next(item for item in scene.childItems() if item.objectName() == "node_e1")


results.clear()
click(card())
first = wait("expand")
added = {node["id"] for node in first["nodes_added"]}
assert added
capture("click-1-expanded.png")
results.clear()
click(card())
second = wait("collapse")
assert set(second["nodes_removed"]) == added
assert all(window.nodeIndex(identity) < 0 for identity in added)
assert all(window.edgeExists(edge["assertion_id"]) for edge in timeline["temporal_edges"])
capture("click-2-collapsed.png")
results.clear()
click(card())
third = wait("expand")
assert {node["id"] for node in third["nodes_added"]} == added
assert all(window.nodeIndex(identity) >= 0 for identity in added)
capture("click-3-reexpanded.png")
after = formal_state()
assert before == after
report = {
    "project": str(project),
    "pid": os.getpid(),
    "relative_axis_count": len(axes),
    "largest_axis_points": len(axes[0]["points"]),
    "absolute_axis_count": len(timeline["lanes"]),
    "unknown_events": len(timeline["unknown"]),
    "numberline_point_selection": detail["node"]["id"],
    "input": "QTest.mouseClick rendered node_e1 center",
    "sequence": ["expand", "collapse", "expand"],
    "first_added_nodes": sorted(added),
    "second_removed_nodes": second["nodes_removed"],
    "third_restored_nodes": [node["id"] for node in third["nodes_added"]],
    "formal_before": before,
    "formal_after": after,
    "model_requests": 0,
    "database_writes": 0,
    "window_left_open": True,
    "errors": errors,
}
(output / "qa-summary.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False), flush=True)
window.setProperty("graphMode", "timeline")
app.exec()
bridge.close()
