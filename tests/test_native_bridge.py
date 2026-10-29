"""Qt event-loop integration over an isolated public Operations project."""

from __future__ import annotations

import time

from PySide6.QtWidgets import QApplication

from narrative.native_bridge import NativeBridge
from narrative.operations import Operations


def test_bridge_project_source_review_and_incremental_canvas(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results: list[tuple[str, dict]] = []
    errors: list[tuple[str, str]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    def invoke(kind: str, action) -> dict:
        before = len(results)
        action()
        deadline = time.monotonic() + 8
        while len(results) == before and not errors and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not errors
        assert len(results) == before + 1, f"未收到 {kind} 的异步结果"
        assert results[-1][0] == kind
        return results[-1][1]

    try:
        project = invoke("project", lambda: bridge.createProjectAt(str(tmp_path), "native-test"))
        assert project["project"]["revision"] >= 0
        ark = invoke("provider", bridge.loadProviderProfile)["profile"]
        invoke("provider", lambda: bridge.saveProviderProfile(ark | {"model": "saved-ark-model"}))
        invoke("provider", lambda: bridge.saveProviderProfile({
            "provider": "qwen", "model": "qwen-test", "max_tokens": 4096,
        }))
        remembered = invoke("provider", lambda: bridge.loadProviderProfileFor("ark"))["profile"]
        assert remembered["model"] == "saved-ark-model"
        assert invoke("provider", bridge.loadProviderProfile)["profile"]["provider"] == "qwen"
        imported = invoke("source_imported", lambda: bridge.importSource("短章", "狼来到河边。小狼看见羊。"))
        source_id = imported["source_id"]
        source = invoke("source", lambda: bridge.sourceText(source_id))["source"]
        assert "狼来到河边" in source["text"]

        ops = Operations.open_graph_project(tmp_path / "native-test.nproj")
        revision = ops.graph_project_info()["revision"]
        evidence = ops.locate(source_id, "狼来到河边")[0] | {"id": "quote"}
        objects = [
            {"kind": "node", "id": "line", "node_type": "storyline", "name": "河边主线"},
            {"kind": "node", "id": "scene", "node_type": "scene", "name": "河边场景"},
            {"kind": "node", "id": "event", "node_type": "event", "name": "狼来到河边"},
            {"kind": "predicate", "id": "contains", "label": "包含", "semantic": "containment", "value_type": "node"},
            evidence,
            {"kind": "assertion", "id": "a1", "subject_id": "line", "predicate_id": "contains", "object_id": "scene"},
            {"kind": "assertion", "id": "a2", "subject_id": "scene", "predicate_id": "contains", "object_id": "event"},
            {"kind": "evidence_link", "id": "l1", "assertion_id": "a1", "evidence_id": "quote"},
            {"kind": "evidence_link", "id": "l2", "assertion_id": "a2", "evidence_id": "quote"},
        ]
        assert ops.graph_precheck(objects, revision) == []
        candidate_id = ops.graph_propose(objects, revision, source="manual")
        candidate = invoke("candidate", lambda: bridge.candidateDetail(candidate_id))
        assert candidate["preview"]["display_map"]["items"]["line"]["label"] == "河边主线"
        candidate_hierarchy = invoke("hierarchy", lambda: bridge.loadHierarchy(candidate_id))
        assert candidate_hierarchy["candidate_id"] == candidate_id
        assert {node["id"] for node in candidate_hierarchy["nodes"]} == {"line", "scene", "event"}
        assert invoke("canvas", bridge.loadGraph)["nodes"] == []  # pending remains private
        assert invoke("hierarchy", bridge.loadHierarchy)["nodes"] == []
        invoke("review", lambda: bridge.acceptCandidate(candidate_id, 1))
        assert {node["id"] for node in invoke("hierarchy", bridge.loadHierarchy)["nodes"]} == {"line", "scene", "event"}
        # Containment seeds only the storyline; scene and event stay collapsed on the canvas.
        overview = invoke("canvas", bridge.loadGraph)
        assert [node["id"] for node in overview["nodes"]] == ["line"]
        bridge.setNodePosition("line", 84.0, -17.0)
        expanded = invoke("expand", lambda: bridge.expandNode("line"))
        assert [node["id"] for node in expanded["nodes_added"]] == ["scene"]
        assert [(edge["subject_id"], edge["object_id"]) for edge in expanded["edges_added"]] == [("line", "scene")]
        assert invoke("expand", lambda: bridge.expandNode("line"))["edges_added"] == []
        detail = invoke("detail", lambda: bridge.nodeDetail("scene"))
        assertion = next(row for row in detail["assertions"] if row["id"] == "a1")
        assert assertion["evidence"][0]["quote"] == "狼来到河边"
        assert assertion["object_label"] == "河边场景"
        assert invoke("search", lambda: bridge.searchNodes("河边", ["scene"]))["nodes"][0]["id"] == "scene"
    finally:
        bridge.close()


def test_bridge_query_epoch_discards_slow_filter_and_preserves_revision_zero(tmp_path, monkeypatch) -> None:
    """A completed old query cannot restore nodes, details, or timeline after a new filter."""
    from threading import Event

    from narrative.graph_operations import GraphOperations

    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    started, release = Event(), Event()
    original = GraphOperations.graph_view

    def delayed(self, spec):
        result = original(self, spec)
        if spec.get("node_types") == ["person"] and spec["view"] == "graph":
            started.set()
            assert release.wait(8)
        return result

    monkeypatch.setattr(GraphOperations, "graph_view", delayed)

    def until(condition):
        deadline = time.monotonic() + 8
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert condition(), errors

    try:
        bridge.createProjectAt(str(tmp_path), "epoch")
        until(lambda: any(k == "project" for k, _ in results))
        bridge.loadGraphView({"node_types": ["person"]})
        assert started.wait(3)
        bridge.loadGraphView({"node_types": [], "as_of_revision": 0})
        until(lambda: any(k == "graph_view" for k, _ in results))
        view = next(p for k, p in results if k == "graph_view")
        assert view["query"]["node_types"] == []
        assert view["query"]["as_of_revision"] == 0
        assert {view[key]["revision"] for key in ("canvas", "timeline", "table", "hierarchy")} == {0}
        release.set()
        until(lambda: not bridge._requests)
        assert len([k for k, _ in results if k == "graph_view"]) == 1
        assert not errors
    finally:
        release.set()
        bridge.close()


def test_bridge_compare_and_temporal_candidate_call_operations(tmp_path, monkeypatch) -> None:
    """The bridge reaches the audited Operations methods and emits dedicated kinds.

    The core ``graph_event_compare``/``graph_temporal_propose`` land in a later
    mainline round; this checks the bridge wiring only, never the pure core.
    """
    from narrative.graph_operations import GraphOperations

    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results: list[tuple[str, dict]] = []
    errors: list[tuple[str, str]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    compare_calls: list[dict] = []
    propose_calls: list[dict] = []

    def fake_compare(self, left_id, right_id, context_id="canon", disclosure_cut=None, as_of_revision=None):
        compare_calls.append({"left_id": left_id, "right_id": right_id, "context_id": context_id,
                              "disclosure_cut": disclosure_cut, "as_of_revision": as_of_revision})
        return {"left_id": left_id, "right_id": right_id, "relation": "before",
                "assertion_ids": ["t1"], "reason": "已审 before 断言：左事件结束严格早于右事件开始"}

    def fake_propose(self, source_id, quote, left_id, left_boundary, relation, right_id,
                     right_boundary, expected_revision=None):
        propose_calls.append({"source_id": source_id, "quote": quote, "left_id": left_id,
                              "left_boundary": left_boundary, "relation": relation,
                              "right_id": right_id, "right_boundary": right_boundary,
                              "expected_revision": expected_revision})
        return "cand-temporal-1"

    monkeypatch.setattr(GraphOperations, "graph_event_compare", fake_compare)
    monkeypatch.setattr(GraphOperations, "graph_temporal_propose", fake_propose)

    def invoke(kind: str, action) -> dict:
        before = len(results)
        action()
        deadline = time.monotonic() + 8
        while len(results) == before and not errors and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not errors
        assert len(results) == before + 1, f"未收到 {kind} 的异步结果"
        assert results[-1][0] == kind
        return results[-1][1]

    try:
        invoke("project", lambda: bridge.createProjectAt(str(tmp_path), "compare-test"))
        compared = invoke("event_compare", lambda: bridge.compareEvents("e1", "e2"))
        assert compared["relation"] == "before"
        assert compared["assertion_ids"] == ["t1"]
        assert compare_calls == [{"left_id": "e1", "right_id": "e2", "context_id": "canon",
                                  "disclosure_cut": None, "as_of_revision": None}]
        proposed = invoke("temporal_candidate", lambda: bridge.proposeTemporalRelation(
            "s1", "甲先别乙", "e1", "end", "before", "e2", "start"))
        assert proposed == {"candidate_id": "cand-temporal-1", "status": "pending"}
        assert propose_calls == [{"source_id": "s1", "quote": "甲先别乙", "left_id": "e1",
                                  "left_boundary": "end", "relation": "before", "right_id": "e2",
                                  "right_boundary": "start", "expected_revision": None}]
    finally:
        bridge.close()


def test_bridge_disclosure_query_and_change_candidates(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results: list[tuple[str, dict]] = []
    errors: list[tuple[str, str]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    def invoke(kind: str, action) -> dict:
        before = len(results)
        action()
        deadline = time.monotonic() + 8
        while len(results) == before and not errors and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not errors
        assert len(results) == before + 1
        assert results[-1][0] == kind
        return results[-1][1]

    try:
        invoke("project", lambda: bridge.createProjectAt(str(tmp_path), "disclosure-test"))
        imported = invoke("source_imported", lambda: bridge.importSource("短章", "林舟皮肤黝黑。"))
        source_id = imported["source_id"]
        ops = Operations.open_graph_project(tmp_path / "disclosure-test.nproj")
        evidence = ops.locate(source_id, "皮肤黝黑")[0]
        revision = ops.graph_project_info()["revision"]
        graph = [
            {"kind": "source_order", "id": "so", "source_id": source_id, "ordinal": 1, "context_id": "canon"},
            {"kind": "node", "id": "n1", "node_type": "person", "name": "林舟"},
            {"kind": "node", "id": "e1", "node_type": "event", "name": "得知肤色",
             "disclosure": {"source_id": source_id, "start": evidence["start"]}},
        ]
        records = [
            {"kind": "property_definition", "id": "skin", "value_type": "string", "cardinality": "one", "persistence": "until_changed"},
            {"kind": "property_value", "id": "skin-1", "node_id": "n1", "property_id": "skin",
             "value": "黝黑", "event_id": "e1",
             "disclosure": {"source_id": source_id, "start": evidence["start"]},
             "evidence": {"source_id": source_id, "quote": "皮肤黝黑"}},
        ]
        candidate_id = ops.change_propose(graph, records, revision, source="manual")
        ops.change_accept(candidate_id, 1)

        payload = invoke(
            "property_disclosure",
            lambda: bridge.queryPropertyDisclosure(
                "n1", "skin", {"source_id": source_id, "start": evidence["start"]},
                "world", "",
            ),
        )
        assert payload["state"]["state"] == {"kind": "known", "value": "黝黑"}
        candidates = invoke("change_candidates", lambda: bridge.loadChangeCandidates(""))
        assert any(item["id"] == candidate_id for item in candidates["items"])
    finally:
        bridge.close()


def test_bridge_revises_node_type_and_retracts(tmp_path) -> None:
    """Type correction and retraction must be reachable from the UI, not scripts."""
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results: list[tuple[str, dict]] = []
    errors: list[tuple[str, str]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    def invoke(kind: str, action) -> dict:
        before = len(results)
        action()
        deadline = time.monotonic() + 8
        while len(results) == before and not errors and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not errors, errors
        assert len(results) == before + 1
        assert results[-1][0] == kind
        return results[-1][1]

    try:
        invoke("project", lambda: bridge.createProjectAt(str(tmp_path), "node-edit"))
        invoke("source_imported", lambda: bridge.importSource("短章", "甲出场了。"))
        ops = Operations.open_graph_project(tmp_path / "node-edit.nproj")
        candidate = ops.change_propose(
            [
                {"kind": "node", "id": "n_jia", "node_type": "person", "name": "甲"},
                {"kind": "node", "id": "n_fake", "node_type": "person", "name": "上帝"},
            ],
            [], ops.graph_project_info()["revision"], source="manual",
        )
        ops.change_accept(candidate, 1)

        # A wrong type is corrected through a candidate, then accepted.
        edited = invoke(
            "node_edit",
            lambda: bridge.reviseNode("n_jia", {"node_type": "item"}),
        )
        assert edited["candidate"]["status"] == "pending"
        ops.change_accept(edited["candidate"]["id"], edited["candidate"]["version"])
        node = next(item for item in ops.graph_snapshot()["nodes"] if item["id"] == "n_jia")
        assert node["node_type"] == "item"

        # A non-existent entity is retracted: gone from projections, kept in history.
        retracted = invoke(
            "node_edit",
            lambda: bridge.reviseNode("n_fake", {"retracted": True}),
        )
        ops.change_accept(retracted["candidate"]["id"], retracted["candidate"]["version"])
        visible = ops.graph_query(limit=100)["nodes"]
        assert all(item["id"] != "n_fake" for item in visible)
        stored = next(item for item in ops.graph_snapshot()["nodes"] if item["id"] == "n_fake")
        assert stored["retracted"] is True

        # Position binding is a candidate too, and lands on the node.
        source_id = ops.sources()[0]["id"]
        located = invoke(
            "node_edit",
            lambda: bridge.reviseNode("n_jia", {"disclosure": {"source_id": source_id, "start": 0}}),
        )
        ops.change_accept(located["candidate"]["id"], located["candidate"]["version"])
        node = next(item for item in ops.graph_snapshot()["nodes"] if item["id"] == "n_jia")
        assert node["disclosure"] == {"source_id": source_id, "start": 0}
    finally:
        bridge.close()


def test_a_broken_slow_report_does_not_swallow_the_result(tmp_path) -> None:
    """耗时上报只是旁路：它坏了，结果也必须送到界面。

    这条路上没有别的兜底——结果丢了，界面就永远停在「处理中」，
    看起来就是「连工程列表都加载不出来」。
    """
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results: list[str] = []
    bridge.resultReady.connect(lambda kind, payload: results.append(kind))

    def boom(kind: str, ms: int) -> None:
        raise RuntimeError("slow report is broken")

    bridge.slowOperation.connect(boom)
    # 一个必然超过上报阈值的操作。
    bridge._submit("slow-one", lambda: (time.sleep(0.25), {})[1])

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and "slow-one" not in results:
        app.processEvents()
        time.sleep(0.01)

    assert results == ["slow-one"]
    assert bridge.pendingOperations() == []


def test_a_lost_result_does_not_wedge_the_window(tmp_path) -> None:
    """结果永远不回来时，界面不能一直停在「处理中」：下一次操作前把它放弃掉。"""
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    bridge.STALE_AFTER_SECONDS = 0.2
    busy: list[bool] = []
    errors: list[tuple[str, str]] = []
    results: list[str] = []
    bridge.busyChanged.connect(lambda value: busy.append(value))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    bridge.resultReady.connect(lambda kind, payload: results.append(kind))

    def never() -> dict:
        time.sleep(3)
        return {}

    bridge._submit("stuck-one", never)
    assert busy == [True]
    time.sleep(0.3)

    # 下一次操作：先把卡住的那条丢掉，再正常提交。
    bridge._submit("fine", lambda: {})
    assert [kind for kind, _message in errors] == ["timeout"]
    assert "stuck-one" in errors[0][1]

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and "fine" not in results:
        app.processEvents()
        time.sleep(0.01)
    assert results == ["fine"]
    assert bridge.pendingOperations() == []
    assert busy[-1] is False
