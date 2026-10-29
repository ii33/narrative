"""Qt event-loop integration over an isolated public Operations project."""

from __future__ import annotations

import time

from PySide6.QtWidgets import QApplication

from narrative.native_bridge import NativeBridge


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
        if spec.get("node_types") == ["person"] and spec.get("view") == "table":
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
        payload = next(p for k, p in results if k == "graph_view")
        canvas = payload["canvas"]
        # 慢的那次被丢掉：回来的只有新条件的结果，而且仍钉在 revision 0。
        assert canvas["query"]["node_types"] == []
        assert canvas["query"]["as_of_revision"] == 0
        assert canvas["revision"] == 0
        release.set()
        until(lambda: not bridge._requests)
        assert len([k for k, _ in results if k == "graph_view"]) == 1
        assert not errors
    finally:
        release.set()
        bridge.close()
