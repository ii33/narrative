"""Read-only native QA after explicitly approved human temporal candidate acceptance."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge

project = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
output.mkdir(parents=True, exist_ok=True)
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
        found = [payload for key, payload in results if key == kind]
        if found:
            return found[-1]
    raise TimeoutError(kind)


def capture(name):
    QTest.qWait(350)
    grab = window.contentItem().grabToImage()
    deadline = time.monotonic() + 10
    while grab.image().isNull() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(20)
    assert grab.saveToFile(str(output / name))
    print("saved", name, flush=True)


window.openProjectPath(str(project))
info = wait("project")
wait("canvas")
hierarchy = wait("hierarchy")
timeline = wait("timeline")
assert len(timeline["events"]) == 13
assert len(timeline["temporal_edges"]) == 6
assert not timeline["known_time"]
assert not timeline["lanes"]
assert len(timeline["relative"]) + len(timeline["unknown"]) == 13
nodes = {node["id"]: node for node in hierarchy["nodes"]}
assert len(nodes["s1"]["child_ids"]) == 5
assert sum(len(nodes[identity]["child_ids"]) for identity in nodes["s1"]["child_ids"]) == 8
project_info = window.property("projectInfo")
project_info = project_info.toVariant() if hasattr(project_info, "toVariant") else project_info
window.setProperty("projectInfo", project_info | {"title": "《狼》第一则 · 人工已审时序"})
window.setProperty("currentPage", "graph")
window.setProperty("graphMode", "hierarchy")
window.toggleHierarchy("s1", False)
window.selectNode("e1")
wait("detail")
capture("wolf-main-five.png")
window.toggleHierarchy("e1", False)
capture("wolf-tree-expanded.png")
window.toggleHierarchy("e1", False)
capture("wolf-tree-collapsed.png")
window.setProperty("displayType", "event")
window.setProperty("displayDepth", 0)
assert window.nodeShown("e1", "event")
assert not window.nodeShown("e1a", "event")
capture("wolf-main-event-filter.png")
window.setProperty("displayType", "location")
assert window.nodeShown("p_tree", "location")
capture("wolf-location-filter.png")
window.setProperty("displayType", "all")
window.setProperty("displayDepth", -1)
window.setProperty("graphMode", "relations")
window.setProperty("displayRelation", "temporal")
window.fitGraph()
capture("wolf-temporal-graph.png")
window.setProperty("displayType", "event")
window.setProperty("displayDepth", 0)
assert window.visibleNodeCount() == 5
capture("wolf-main-temporal-filter.png")
window.setProperty("displayType", "all")
window.setProperty("displayDepth", -1)
window.setProperty("displayRelation", "all")
results.clear()
window.expandNode("e1")
expanded = wait("expand")
assert expanded["nodes_added"]
window.fitGraph()
capture("wolf-relations-expanded.png")
results.clear()
bridge.collapseNode("e1")
collapsed = wait("collapse")
for edge in timeline["temporal_edges"]:
    assert window.edgeExists(edge["assertion_id"])
assert collapsed["edges_removed"]
window.fitGraph()
capture("wolf-relations-collapsed.png")
window.setProperty("graphMode", "timeline")
results.clear()
window.selectNode("e1")
wait("detail")
capture("wolf-timeline-human-accepted.png")
window.setProperty("displayRelation", "participation")
app.processEvents()
capture("wolf-timeline-relation-filter.png")
window.setProperty("displayRelation", "temporal")
window.setProperty("displayType", "location")
assert window.timelineVisibleCount() == 0
capture("wolf-timeline-type-empty.png")
window.setProperty("displayType", "all")
window.setProperty("displayRelation", "all")
report = {
    "project": str(project),
    "pid": os.getpid(),
    "historical_main_events": 5,
    "historical_children": 8,
    "accepted_temporal_relations": 6,
    "accepted_origin": "user-approved-human-candidate",
    "candidate_id": "0bc4e16d58124fc88d58637f3d16f58b",
    "unknown_time_events": len(timeline["unknown"]),
    "relative_time_events": len(timeline["relative"]),
    "absolute_time_lanes": len(timeline["lanes"]),
    "checks": [
        "hierarchy_expand_collapse",
        "main_event_filter_excludes_children",
        "location_filter_exposes_shared_root",
        "relation_expand_collapse_restores_cache",
        "timeline_6_human_accepted_relations_no_absolute_lanes",
        "temporal_edges_preserved_after_collapse",
        "timeline_type_empty_state_and_relation_filter",
    ],
    "new_candidates_accepted": 0,
    "model_requests": 0,
    "window_left_open": True,
    "errors": errors,
}
(output / "qa-summary.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False), flush=True)
app.exec()
bridge.close()
