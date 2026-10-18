"""Qt event-loop integration over an isolated public Operations project."""

from __future__ import annotations

import time

from PySide6.QtCore import QCoreApplication

from narrative.native_bridge import NativeBridge
from narrative.operations import Operations


def test_bridge_project_source_review_and_incremental_canvas(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    bridge = NativeBridge(tmp_path)
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
        project = invoke("project", lambda: bridge.createProject("native-test"))
        assert project["project"]["revision"] >= 0
        ark = invoke("provider", bridge.loadProviderProfile)["profile"]
        invoke("provider", lambda: bridge.saveProviderProfile(ark | {"model": "saved-ark-model"}))
        invoke("provider", lambda: bridge.saveProviderProfile({
            "provider": "qwen", "model": "qwen-test", "max_tokens": 4096,
        }))
        remembered = invoke("provider", lambda: bridge.loadProviderProfileFor("ark"))["profile"]
        assert remembered["model"] == "saved-ark-model"
        assert invoke("provider", bridge.loadProviderProfile)["profile"]["provider"] == "qwen"
        imported = invoke("source_imported", lambda: bridge.importSource("章节", "甲送别乙。"))
        source_id = imported["source_id"]
        source = invoke("source", lambda: bridge.sourceText(source_id))["source"]
        assert "甲送别乙" in source["text"]

        ops = Operations.open_graph_project(tmp_path / "native-test.sqlite3")
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
        candidate = invoke("candidate", lambda: bridge.candidateDetail(candidate_id))
        assert candidate["preview"]["display_map"]["items"]["p1"]["label"] == "甲"
        candidate_hierarchy = invoke("hierarchy", lambda: bridge.loadHierarchy(candidate_id))
        assert candidate_hierarchy["candidate_id"] == candidate_id
        assert {node["id"] for node in candidate_hierarchy["nodes"]} == {"p1", "e1"}
        assert invoke("canvas", bridge.loadGraph)["nodes"] == []  # pending remains private
        assert invoke("hierarchy", bridge.loadHierarchy)["nodes"] == []
        invoke("review", lambda: bridge.acceptCandidate(candidate_id, 1))
        assert {node["id"] for node in invoke("hierarchy", bridge.loadHierarchy)["nodes"]} == {"p1", "e1"}
        overview = invoke("canvas", bridge.loadGraph)
        assert [node["id"] for node in overview["nodes"]] == ["p1"]
        bridge.setNodePosition("p1", 84.0, -17.0)
        expanded = invoke("expand", lambda: bridge.expandNode("p1"))
        assert [node["id"] for node in expanded["nodes_added"]] == ["e1"]
        assert [(edge["subject_id"], edge["object_id"]) for edge in expanded["edges_added"]] == [("e1", "p1")]
        assert invoke("expand", lambda: bridge.expandNode("p1"))["edges_added"] == []
        detail = invoke("detail", lambda: bridge.nodeDetail("e1"))
        assert detail["assertions"][0]["evidence"][0]["quote"] == "甲送别乙"
        assert detail["assertions"][0]["object_label"] == "甲"
        assert invoke("search", lambda: bridge.searchNodes("甲", ["person"]))["nodes"][0]["id"] == "p1"
    finally:
        bridge.close()


def test_bridge_query_epoch_discards_slow_filter_and_preserves_revision_zero(tmp_path, monkeypatch) -> None:
    """A completed old query cannot restore nodes, details, or timeline after a new filter."""
    from threading import Event

    from narrative.graph_operations import GraphOperations

    app = QCoreApplication.instance() or QCoreApplication([])
    bridge = NativeBridge(tmp_path)
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
        bridge.createProject("epoch")
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
