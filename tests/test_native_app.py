"""Qt Quick interaction smoke over a disposable graph Operations project."""

from __future__ import annotations

import os
import time
from math import hypot
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge
from narrative.operations import Operations


def _until(app: QApplication, condition, seconds: float = 8) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(20)
    assert condition()


def _point(item: QQuickItem, x: float | None = None, y: float | None = None) -> QPoint:
    mapped = item.mapToScene(QPointF(x if x is not None else item.width() / 2, y if y is not None else item.height() / 2))
    return QPoint(round(mapped.x()), round(mapped.y()))


def _assert_label_at_edge_midpoint(scene: QQuickItem, assertion_id: str, subject_id: str, object_id: str) -> None:
    """Compare rendered scene coordinates, including all QML transforms."""
    items = scene.childItems()
    edge = next(item for item in items if item.objectName() == f"edge_{assertion_id}")
    subject = next(item for item in items if item.objectName() == f"node_{subject_id}")
    target = next(item for item in items if item.objectName() == f"node_{object_id}")
    label = next(item for item in edge.childItems() if item.objectName() == f"edgeLabel_{assertion_id}")
    source_center = subject.mapToScene(QPointF(subject.width() / 2, subject.height() / 2))
    target_center = target.mapToScene(QPointF(target.width() / 2, target.height() / 2))
    expected = QPointF((source_center.x() + target_center.x()) / 2, (source_center.y() + target_center.y()) / 2)
    actual = label.mapToScene(QPointF(label.width() / 2, label.height() / 2))
    error = hypot(expected.x() - actual.x(), expected.y() - actual.y())
    assert error <= 2, f"{assertion_id} label/edge midpoint differs by {error:.2f}px: {actual} != {expected}"


def test_native_qml_click_expand_drag_zoom_and_review_gate(tmp_path: Path) -> None:
    # The offscreen QTest checks interaction, never the hardware adapter.
    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
    app = cast(QApplication, QApplication.instance() or QApplication([]))
    project = tmp_path / "qt-graph.sqlite3"
    ops = Operations.create_graph_project(project)
    source_id = ops.import_source("短章", "甲送别乙。")
    revision = ops.graph_project_info()["revision"]
    evidence = ops.locate(source_id, "甲送别乙")[0] | {"id": "q1"}
    objects = [
        {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
        {"kind": "node", "id": "e1", "node_type": "event", "name": "送别"},
        {"kind": "predicate", "id": "participates", "label": "参与", "semantic": "participation",
         "subject_types": ["event"], "object_types": ["person"], "value_type": "node"},
        evidence,
        {"kind": "assertion", "id": "a1", "subject_id": "e1", "predicate_id": "participates",
         "object_id": "p1", "qualifiers": {"role": "送行者"}},
        {"kind": "evidence_link", "id": "l1", "assertion_id": "a1", "evidence_id": "q1"},
    ]
    assert ops.graph_precheck(objects, revision) == []
    candidate_id = ops.graph_propose(objects, revision, source="manual")
    bridge = NativeBridge(tmp_path)
    results: list[tuple[str, dict]] = []
    errors: list[tuple[str, str]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    root_window = engine.rootObjects()[0]
    assert isinstance(root_window, QQuickWindow)
    window: Any = root_window  # QML-defined methods are dynamic Qt metaobject members.
    window.show()
    try:
        window.openProjectPath(str(project))
        _until(app, lambda: any(kind == "project" for kind, _ in results))
        assert window.nodeIndex("p1") == -1  # pending content is not on the canvas
        assert not errors

        # A stale version cannot bypass Operations review.
        bridge.acceptCandidate(candidate_id, 99)
        _until(app, lambda: bool(errors))
        assert errors[-1][0] == "review"
        assert ops.graph_candidate(candidate_id)["status"] == "pending"
        errors.clear()
        bridge.acceptCandidate(candidate_id, 1)
        _until(app, lambda: any(kind == "review" for kind, _ in results))
        bridge.loadGraph()
        _until(app, lambda: window.nodeIndex("p1") >= 0)
        scene = window.findChild(QQuickItem, "graphScene")
        canvas = window.findChild(QQuickItem, "graphCanvas")
        assert scene is not None and canvas is not None
        card = next((child for child in scene.childItems() if child.objectName() == "node_p1"), None)
        assert card is not None
        before = window.nodeById("p1").toVariant()

        # Click the rendered card: model-driven one-hop expansion and detail.
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=_point(card))
        _until(app, lambda: window.nodeIndex("e1") >= 0 and window.edgeExists("a1"))
        _until(app, lambda: (window.property("currentDetail").toVariant()
               if hasattr(window.property("currentDetail"), "toVariant")
               else window.property("currentDetail")).get("node", {}).get("name") == "甲")
        _assert_label_at_edge_midpoint(scene, "a1", "e1", "p1")
        assert window.nodeIndex("p1") >= 0
        window.expandNode("p1")
        _until(app, lambda: len([kind for kind, _ in results if kind == "expand"]) >= 2)
        assert window.nodeIndex("e1") == 1

        # Repeated card clicks collapse, then expand; an in-flight repeat is ignored.
        card = next(child for child in scene.childItems() if child.objectName() == "node_p1")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=_point(card))
        _until(app, lambda: window.nodeIndex("e1") == -1)
        assert not window.edgeExists("a1")
        assert window.nodeIndex("p1") >= 0
        expand_count = len([kind for kind, _ in results if kind == "expand"])
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=_point(card))
        window.toggleRelationNode("p1")
        _until(app, lambda: window.edgeExists("a1") and not window.property("relationRequestPending"))
        assert len([kind for kind, _ in results if kind == "expand"]) == expand_count + 1

        # A review refresh may replace QML delegates while async reads settle.
        card = next(child for child in scene.childItems() if child.objectName() == "node_p1")
        start = _point(card)
        QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(window, start + QPoint(10, 8), delay=30)
        QTest.mouseMove(window, start + QPoint(32, 24), delay=30)
        app.processEvents()
        while_dragging = window.nodeById("p1").toVariant()
        assert (while_dragging["x"], while_dragging["y"]) != (before["x"], before["y"])
        _assert_label_at_edge_midpoint(scene, "a1", "e1", "p1")
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=start + QPoint(32, 24))
        app.processEvents()
        after = window.nodeById("p1").toVariant()
        assert (after["x"], after["y"]) != (before["x"], before["y"])
        expand_count = len([kind for kind, _ in results if kind == "expand"])
        window.expandNode("p1")
        _until(app, lambda: len([kind for kind, _ in results if kind == "expand"]) > expand_count)
        preserved = window.nodeById("p1").toVariant()
        assert (preserved["x"], preserved["y"]) == (after["x"], after["y"])
        _assert_label_at_edge_midpoint(scene, "a1", "e1", "p1")

        # The same visual midpoint invariant covers both signed axes, horizontal,
        # vertical and different slopes; it is not a duplicate of QML's formula.
        for source_xy, target_xy in [
            ((100, 100), (500, 100)),
            ((500, 100), (100, 100)),
            ((100, 100), (100, 500)),
            ((100, 500), (500, 100)),
            ((500, 500), (100, 100)),
            ((500, 100), (100, 500)),
        ]:
            window.updateNodePosition("e1", *source_xy)
            window.updateNodePosition("p1", *target_xy)
            app.processEvents()
            _assert_label_at_edge_midpoint(scene, "a1", "e1", "p1")

        old_scale = scene.scale()
        pos = _point(canvas)
        wheel = QWheelEvent(QPointF(pos), QPointF(pos), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.ScrollUpdate, False)
        app.sendEvent(window, wheel)
        app.processEvents()
        assert scene.scale() > old_scale
        _assert_label_at_edge_midpoint(scene, "a1", "e1", "p1")
        scene.setX(scene.x() + 73)
        scene.setY(scene.y() - 41)
        app.processEvents()
        _assert_label_at_edge_midpoint(scene, "a1", "e1", "p1")
        # Display filters hide the rendered card without removing cached graph data.
        window.setProperty("displayType", "location")
        app.processEvents()
        assert not card.isVisible()
        assert window.nodeIndex("p1") >= 0
        assert window.visibleNodeCount() == 0
        window.setProperty("displayType", "all")
        app.processEvents()
        assert card.isVisible()
        bridge.loadTimeline()
        _until(app, lambda: any(kind == "timeline" for kind, _ in results))
        timeline = next(payload for kind, payload in reversed(results) if kind == "timeline")
        assert not timeline["temporal_edges"]
        assert [event["id"] for event in timeline["unknown"]] == ["e1"]
        window.setProperty("graphMode", "timeline")
        app.processEvents()
        assert window.findChild(QQuickItem, "timelineModeButton") is not None
        bridge.collapseNode("p1")
        _until(app, lambda: any(kind == "collapse" for kind, _ in results))
        assert window.nodeIndex("p1") >= 0
        assert window.nodeIndex("e1") == -1
        assert not window.edgeExists("a1")
        assert not errors
    finally:
        window.close()
        bridge.close()


def test_native_event_hierarchy_expands_and_collapses(tmp_path: Path) -> None:
    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
    app = cast(QApplication, QApplication.instance() or QApplication([]))
    project = tmp_path / "hierarchy.sqlite3"
    ops = Operations.create_graph_project(project)
    sid = ops.import_source("短章", "狼来到河边。小狼看见羊。")
    evidence = ops.locate(sid, "狼来到河边")[0] | {"id": "quote"}
    objects = [
        {"kind": "node", "id": "line", "node_type": "storyline", "name": "河边主线"},
        {"kind": "node", "id": "scene", "node_type": "scene", "name": "河边场景"},
        {"kind": "node", "id": "event", "node_type": "event", "name": "狼来到河边"},
        {"kind": "node", "id": "wolf", "node_type": "person", "name": "狼"},
        {"kind": "predicate", "id": "contains", "label": "包含", "semantic": "containment", "value_type": "node"},
        evidence,
        {"kind": "assertion", "id": "a1", "subject_id": "line", "predicate_id": "contains", "object_id": "scene"},
        {"kind": "assertion", "id": "a2", "subject_id": "scene", "predicate_id": "contains", "object_id": "event"},
        {"kind": "evidence_link", "id": "l1", "assertion_id": "a1", "evidence_id": "quote"},
        {"kind": "evidence_link", "id": "l2", "assertion_id": "a2", "evidence_id": "quote"},
    ]
    assert ops.graph_precheck(objects, ops.graph_project_info()["revision"]) == []
    ops.graph_accept(ops.graph_propose(objects, ops.graph_project_info()["revision"]), 1)
    bridge = NativeBridge(tmp_path)
    errors: list[tuple[str, str]] = []
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    assert isinstance(window, QQuickWindow)
    window.show()
    try:
        window.openProjectPath(str(project))
        _until(app, lambda: window.hierarchyNode("event", False) is not None
               and window.hierarchyNode("event", False).get("parent_id") == "scene")
        # Project initialization also loads reading notices, which can move the
        # hierarchy viewport after the graph result itself becomes available.
        _until(app, lambda: not window.property("busy"))
        assert window.property("graphMode") == "hierarchy"
        assert window.hierarchyVisibleIds(False).toVariant() == ["line"]
        assert window.findChild(QQuickItem, "sharedRootsToggle") is not None
        window.toggleHierarchy("line", False)
        assert window.hierarchyVisibleIds(False).toVariant() == ["line", "scene"]
        window.toggleHierarchy("scene", False)
        assert window.hierarchyVisibleIds(False).toVariant() == ["line", "scene", "event"]
        screenshot = os.environ.get("NARRATIVE_UI_SCREENSHOT")
        if screenshot:
            QTest.qWait(100)
            assert window.grabWindow().save(screenshot)
        # toggleHierarchy rebuilds the Repeater delegates. Wait for a completed
        # Qt Quick frame so their ColumnLayout geometry is ready for hit testing.
        frame = QSignalSpy(window.frameSwapped)
        window.requestUpdate()
        _until(app, lambda: frame.count() > 0)
        items = list(window.contentItem().childItems())
        title = None
        while items:
            item = items.pop()
            if item.objectName() == "hierarchyNode_line":
                title = item
                break
            items.extend(item.childItems())
        assert title is not None
        assert title.isVisible() and title.isEnabled()
        assert title.width() > 0 and title.height() > 0
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=_point(title))
        _until(app, lambda: window.hierarchyVisibleIds(False).toVariant() == ["line"])
        window.toggleHierarchy("line", False)
        assert window.hierarchyVisibleIds(False).toVariant() == ["line", "scene", "event"]
        # Detail ordering changes presentation only; no direct relation is added or lost.
        window.setProperty("currentDetail", {"related_nodes": [
            {"id": "event", "name": "狼来到河边", "node_type": "event"},
            {"id": "wolf", "name": "狼", "node_type": "person"},
            {"id": "scene", "name": "河边场景", "node_type": "scene"},
        ]})
        ordered = window.orderedRelatedNodes().toVariant()
        assert [row["id"] for row in ordered] == ["scene", "event", "wolf"]
        assert not errors
    finally:
        window.close()
        bridge.close()


def test_native_temporal_arrow_and_timeline_evidence(tmp_path: Path) -> None:
    from test_graph_hierarchy import _setup

    app = cast(QApplication, QApplication.instance() or QApplication([]))
    ops, sid, objects = _setup(tmp_path)
    next(item for item in objects if item["id"] == "event")["occurrence"] = {
        "kind": "interval", "start": {"state": "known", "ref": {"timeline_id": "story", "form": "number", "number": 5}}, "end": {"state": "unknown"},
    }
    objects += [
        {"kind": "assertion", "id": "org1", "subject_id": "line", "predicate_id": "contains", "object_id": "scene"},
        {"kind": "assertion", "id": "org2", "subject_id": "scene", "predicate_id": "contains", "object_id": "event"},
        {"kind": "evidence_link", "id": "org-proof1", "assertion_id": "org1", "evidence_id": "quote"},
        {"kind": "evidence_link", "id": "org-proof2", "assertion_id": "org2", "evidence_id": "quote"},
        {"kind": "predicate", "id": "before", "label": "早于", "semantic": "temporal", "temporal_relation": "before", "value_type": "node"},
        {"kind": "assertion", "id": "time-edge", "subject_id": "event", "predicate_id": "before", "object_id": "event2",
         "temporal_left": {"timeline_id": "story", "form": "event_boundary", "event_id": "event", "boundary": "end"},
         "temporal_right": {"timeline_id": "story", "form": "event_boundary", "event_id": "event2", "boundary": "start"}},
        {"kind": "evidence_link", "id": "time-proof", "assertion_id": "time-edge", "evidence_id": "quote"},
    ]
    ops.graph_accept(ops.graph_propose(objects, ops.graph_project_info()["revision"]), 1)
    project = next(tmp_path.glob("*.sqlite3"))
    bridge = NativeBridge(tmp_path)
    results = []
    errors = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window: Any = engine.rootObjects()[0]
    try:
        window.openProjectPath(str(project))
        _until(app, lambda: window.edgeExists("time-edge"))
        window.setProperty("graphMode", "relations")
        app.processEvents()
        scene = window.findChild(QQuickItem, "graphScene")
        edge = next(item for item in scene.childItems() if item.objectName() == "edge_time-edge")
        assert edge.property("isTemporal") is True
        assert edge.property("certainTemporal") is True
        label = next(item for item in edge.childItems() if item.objectName() == "edgeLabel_time-edge")
        assert label.property("text") == "早于 · 结束 → 开始"
        window.setProperty("displayRelation", "participation")
        app.processEvents()
        assert not edge.isVisible()
        window.setProperty("displayRelation", "all")
        window.setProperty("graphMode", "timeline")
        _until(app, lambda: any(kind in {"timeline", "graph_view"} for kind, _ in results))
        timeline = window.property("timelineData")
        timeline = timeline.toVariant() if hasattr(timeline, "toVariant") else timeline
        assert {item["id"] for item in timeline["relative"]} == {"event2"}
        assert timeline["lanes"][0]["events"][0]["id"] == "event"
        window.setProperty("showTemporalDetails", True)
        app.processEvents()
        def visual_items(item):
            yield item
            for child in item.childItems():
                yield from visual_items(child)

        axis = next(item for item in visual_items(window.contentItem()) if item.objectName() == "numberAxis_relative-1")
        assert axis.property("maxOrdinal") == 1
        ticks = [item for item in visual_items(axis) if item.objectName().startswith("relativeTick_")]
        assert len(ticks) == 2  # one tick per ordinal, independent of event boundary cards
        # The event is the primary interaction even when its boundaries also
        # participate in a relative constraint; it appears once on its known axis.
        event_cards = [item for item in visual_items(window.contentItem())
                       if item.objectName() == "timelineEvent_root_event"]
        assert len(event_cards) == 1
        point_card = event_cards[0]
        results.clear()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=_point(point_card))
        _until(app, lambda: any(kind == "detail" for kind, _ in results))
        assert next(payload for kind, payload in reversed(results) if kind == "detail")["node"]["id"] == "event"
        window.setWidth(1120)
        app.processEvents()
        timeline_scroll = window.findChild(QQuickItem, "timelineScroll")
        assert axis.width() <= timeline_scroll.width()
        assert timeline_scroll.property("contentWidth") <= timeline_scroll.width()
        window.setWidth(1500)
        window.setProperty("displayType", "event")
        window.setProperty("displayDepth", 0)
        app.processEvents()
        assert window.nodeShown("event", "event") is True
        assert "结束未明" in window.timeLabel(timeline["known_time"][0])
        window.selectNode("event")
        _until(app, lambda: any(kind == "detail" for kind, _ in results))
        detail = next(payload for kind, payload in reversed(results) if kind == "detail")
        assert any(row["evidence"] for row in detail["assertions"] if row["id"] == "time-edge")
        assert not errors
    finally:
        window.close()
        bridge.close()


def test_native_nested_timeline_path_details_switch_collapse_and_reading_reset(tmp_path: Path) -> None:
    from test_graph_hierarchy import _edge, _setup

    app = cast(QApplication, QApplication.instance() or QApplication([]))
    ops, sid, objects = _setup(tmp_path)
    for identity, value in (("event", 10), ("event2", 20)):
        next(item for item in objects if item["id"] == identity)["occurrence"] = {
            "kind": "point", "point": {"timeline_id": "story", "form": "number", "number": value},
        }
    objects += [
        {"kind": "node", "id": identity, "node_type": "event", "name": name}
        for identity, name in (("child1", "第一子事件"), ("child2", "第二子事件"),
                               ("leaf1", "第一细节"), ("leaf2", "第二细节"))
    ]
    for aid, parent, child in (("nested1", "event", "child1"), ("nested2", "event", "child2"),
                               ("nested3", "child1", "leaf1"), ("nested4", "child2", "leaf2")):
        objects += _edge(aid, parent, child)
    ops.graph_accept(ops.graph_propose(objects, ops.graph_project_info()["revision"]), 1)
    bridge = NativeBridge(tmp_path)
    results: list[tuple[str, dict]] = []
    errors: list[tuple[str, str]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window: Any = engine.rootObjects()[0]

    def value(name):
        result = window.property(name)
        return result.toVariant() if hasattr(result, "toVariant") else result

    def visual_items(item):
        yield item
        for child in item.childItems():
            yield from visual_items(child)

    def item(name):
        return next(child for child in visual_items(window.contentItem()) if child.objectName() == name)

    def click_event(axis, identity):
        card = item(f"timelineEvent_{axis}_{identity}")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=_point(card))
        _until(app, lambda: value("currentDetail").get("node", {}).get("id") == identity)

    try:
        window.openProjectPath(str(tmp_path / "nested.sqlite3"))
        _until(app, lambda: bool(value("timelineData").get("nested_axes")))
        _until(app, lambda: bool(value("currentHierarchy").get("nodes")))
        window.setProperty("graphMode", "timeline")
        window.setWidth(1120)
        app.processEvents()
        click_event("root", "event")
        assert value("timelinePath") == ["event"]
        click_event("event:event", "child1")
        assert value("timelinePath") == ["event", "child1"]
        axes = [child for child in visual_items(window.contentItem())
                if child.objectName().startswith("nestedAxis_") and child.isVisible()]
        assert len(axes) == 3
        root_axis = item("nestedAxis_root")
        child_axis = item("nestedAxis_event:event")
        leaf_axis = item("nestedAxis_event:child1")
        assert _point(leaf_axis).y() < _point(child_axis).y() < _point(root_axis).y()
        assert root_axis.width() <= 1120
        click_event("event:child1", "leaf1")
        assert value("timelinePath") == ["event", "child1"]  # a leaf opens details only
        click_event("event:event", "child2")
        assert value("timelinePath") == ["event", "child2"]
        assert not any(child.objectName() == "nestedAxis_event:child1"
                       for child in visual_items(window.contentItem()))
        click_event("event:event", "child2")
        assert value("timelinePath") == ["event"]
        click_event("event:event", "child1")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                         pos=_point(item("closeAxis_event:child1")))
        app.processEvents()
        assert value("timelinePath") == ["event"]
        click_event("root", "event")
        assert value("timelinePath") == []
        click_event("root", "event")
        # A stricter reading cut clears the path immediately, before async data returns.
        window.setProperty("readingPositions", [{"label": "此前", "source_id": sid, "start": 0, "excerpt": ""}])
        window.chooseReadingPosition(0)
        assert value("timelinePath") == []
        assert value("timelineSelections") == {}
        _until(app, lambda: value("timelineData").get("disclosure_cut") == {"source_id": sid, "start": 0})
        assert value("timelineData")["events"] == []
        assert not errors
    finally:
        window.close()
        bridge.close()


def test_native_reading_progress_focus_and_future_details(tmp_path: Path) -> None:
    from test_graph_hierarchy import _edge, _setup

    app = cast(QApplication, QApplication.instance() or QApplication([]))
    ops, sid, objects = _setup(tmp_path)
    objects += _edge("early", "scene", "event", disclosure={"source_id": sid, "start": 0})
    objects += _edge("late", "line", "scene", disclosure={"source_id": sid, "start": 8})
    objects += _edge("reparent", "event2", "event", supersedes_id="early",
                     disclosure={"source_id": sid, "start": 8})
    ops.graph_accept(ops.graph_propose(objects, ops.graph_project_info()["revision"]), 1)
    bridge = NativeBridge(tmp_path)
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window: Any = engine.rootObjects()[0]
    try:
        window.openProjectPath(str(tmp_path / "nested.sqlite3"))
        _until(app, lambda: len(window.property("readingPositions").toVariant()) == 2)
        assert "狼第一则" in window.property("readingPositions").toVariant()[1]["label"]
        window.setProperty("readingCut", {"source_id": sid, "start": 0})
        window.loadReadingView("")
        bridge.loadGraph()
        bridge.loadTimeline()
        bridge.searchNodes("主线", [])
        _until(app, lambda: any(kind == "reading_hierarchy" for kind, _ in results))
        _until(app, lambda: any(kind == "search" for kind, _ in results))
        tree = window.property("currentHierarchy")
        tree = tree.toVariant() if hasattr(tree, "toVariant") else tree
        assert {node["id"] for node in tree["nodes"]} == {"scene", "event"}
        assert window.nodeIndex("line") == -1
        assert next(payload for kind, payload in reversed(results) if kind == "search")["nodes"] == []
        window.selectNode("event")
        _until(app, lambda: any(kind == "detail" for kind, _ in results))
        detail = next(payload for kind, payload in reversed(results) if kind == "detail")
        assert "主线" not in str(detail)
        window.loadReadingView("event")
        _until(app, lambda: window.hierarchyVisibleIds(False).toVariant() == ["event"])
        window.focusParent()
        _until(app, lambda: window.hierarchyVisibleIds(False).toVariant() == ["scene"])
        window.chooseReadingPosition(1)
        _until(app, lambda: "line" in window.hierarchyVisibleIds(False).toVariant())
        window.toggleHierarchy("line", False)
        window.toggleHierarchy("scene", False)
        window.toggleHierarchy("event2", False)
        assert "event" in window.hierarchyVisibleIds(False).toVariant()
        results.clear()
        window.selectNode("event")
        _until(app, lambda: any(kind == "detail" for kind, _ in results))
        late_detail = next(payload for kind, payload in reversed(results) if kind == "detail")
        assert [n["id"] for n in late_detail["related_nodes"]] == ["event2"]
        assert next(a for a in late_detail["assertions"] if a["id"] == "early")["organization_label"] == "历史归属"
        assert next(a for a in late_detail["assertions"] if a["id"] == "reparent")["organization_label"] == "当前归属"
        assert not window.edgeExists("early")
        screenshot = os.environ.get("NARRATIVE_READING_UI_SCREENSHOT")
        if screenshot:
            app.processEvents()
            assert window.grabWindow().save(screenshot)
        assert not errors
    finally:
        window.close()
        bridge.close()


def test_native_unified_query_focus_filters_table_and_replay(tmp_path: Path) -> None:
    """Real QML controls share one bounded query; hidden bridge nodes stay hidden."""
    import json

    from test_graph_hierarchy import _setup

    app = cast(QApplication, QApplication.instance() or QApplication([]))
    ops, _, objects = _setup(tmp_path)
    objects += [
        {"kind": "node", "id": "lake", "name": "河岸", "node_type": "location"},
        {"kind": "predicate", "id": "involved", "label": "涉及", "semantic": "participation", "value_type": "node"},
    ]
    for aid, target in (("actor", "wolf"), ("place", "lake")):
        objects += [
            {"kind": "assertion", "id": aid, "subject_id": "event", "predicate_id": "involved", "object_id": target, "qualifiers": {"role": "参与者"}},
            {"kind": "evidence_link", "id": "proof-" + aid, "assertion_id": aid, "evidence_id": "quote"},
        ]
    ops.graph_accept(ops.graph_propose(objects, ops.graph_project_info()["revision"]), 1)
    revision = ops.graph_project_info()["revision"]
    bridge = NativeBridge(tmp_path)
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window: Any = engine.rootObjects()[0]

    def value(name):
        item = window.property(name)
        return item.toVariant() if hasattr(item, "toVariant") else item

    def query(action):
        before = len([k for k, _ in results if k == "graph_view"])
        action()
        _until(app, lambda: len([k for k, _ in results if k == "graph_view"]) > before)
        return next(p for k, p in reversed(results) if k == "graph_view")

    def click(name):
        item = window.findChild(QQuickItem, name)
        assert item is not None and item.isVisible(), name
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=_point(item))

    try:
        window.openProjectPath(str(tmp_path / "nested.sqlite3"))
        _until(app, lambda: bool(value("queryJson")))
        window.selectNode("wolf")
        _until(app, lambda: value("currentDetail").get("node", {}).get("id") == "wolf")
        focused = query(lambda: click("setQueryFocusButton"))
        assert value("queryFocusId") == "wolf"  # any node, including a person
        assert {n["id"] for n in focused["canvas"]["nodes"]} == {"wolf", "event", "lake"}
        assert focused["query"]["as_of_revision"] == revision
        assert len({focused[key]["revision"] for key in ("canvas", "timeline", "table", "hierarchy")}) == 1
        assert next(row for row in focused["table"]["rows"] if row["id"] == "wolf")["evidence_count"] == 1
        scene = window.findChild(QQuickItem, "graphScene")
        app.processEvents()
        card = next(item for item in scene.childItems() if item.objectName() == "node_wolf")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=_point(card))
        _until(app, lambda: window.nodeIndex("event") == -1)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=_point(card))
        _until(app, lambda: window.nodeIndex("event") >= 0 and window.edgeExists("actor"))
        assert window.nodeIndex("wolf") >= 0

        # Multi-select endpoints can connect through an undisplayed event, without inventing a direct edge.
        window.setProperty("queryNodeTypes", ["person", "location"])
        window.setProperty("queryRelations", ["participation"])
        filtered = query(window.runGraphQuery)
        assert {n["id"] for n in filtered["canvas"]["nodes"]} == {"wolf", "lake"}
        assert filtered["canvas"]["edges"] == []
        assert filtered["table"]["paths"][0]["length"] == 2
        assert {row["id"] for row in filtered["table"]["rows"]} == {n["id"] for n in filtered["canvas"]["nodes"]}
        assert window.nodeIndex("event") == -1
        click("tableModeButton")
        # Repeater delegates are visual children, not QObject children of the root.
        def visual(item):
            yield item
            for child in item.childItems():
                yield from visual(child)
        table_lake = next(item for item in visual(window.contentItem()) if item.objectName() == "tableNode_lake")
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=_point(table_lake))
        _until(app, lambda: value("currentDetail").get("node", {}).get("id") == "lake")
        assert value("queryFocusId") == "wolf"  # selecting details does not change focus
        click("relationsModeButton")
        assert value("queryNodeTypes") == ["person", "location"]
        replay = ops.graph_view(json.loads(value("queryJson")))
        assert {n["id"] for n in replay["nodes"]} == {"wolf", "lake"}

        window.setProperty("queryDistance", 1)
        one_hop = query(window.runGraphQuery)
        assert {n["id"] for n in one_hop["canvas"]["nodes"]} == {"wolf"}
        window.setProperty("queryDistance", 3)
        window.setProperty("queryDirection", "out")
        directed = query(window.runGraphQuery)
        assert {n["id"] for n in directed["canvas"]["nodes"]} == {"wolf"}
        window.setProperty("queryDirection", "both")
        window.setProperty("queryRelations", [])
        empty_edges = query(window.runGraphQuery)
        assert empty_edges["canvas"]["edges"] == []
        assert {n["id"] for n in empty_edges["canvas"]["nodes"]} == {"wolf"}
        window.setProperty("queryNodeTypes", [])
        anchor = query(window.runGraphQuery)
        assert anchor["canvas"]["focus"]["matches_filter"] is False
        empty = query(lambda: click("clearQueryFocusButton"))
        assert empty["canvas"]["nodes"] == empty["table"]["rows"] == []
        assert window.visibleNodeCount() == 0
        assert ops.graph_project_info()["revision"] == revision
        assert not errors
    finally:
        window.close()
        bridge.close()

