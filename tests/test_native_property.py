"""Native property bridge flow through the review-gated Operations API."""

from __future__ import annotations

import json
import time

from PySide6.QtWidgets import QApplication

from narrative.native_bridge import NativeBridge
from narrative.operations import Operations


def test_native_property_candidate_and_state_history(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    project_path = tmp_path / "property-native.sqlite3"
    ops = Operations.create_graph_project(project_path)
    nodes = [
        {"kind": "node", "id": "key", "node_type": "item", "name": "Key"},
        {"kind": "node", "id": "e1", "node_type": "event", "name": "查看钥匙"},
    ]
    candidate_id = ops.graph_propose(nodes, ops.graph_project_info()["revision"])
    ops.graph_accept(candidate_id, 1)

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
        assert len(results) == before + 1, f"no asynchronous {kind} response"
        assert results[-1][0] == kind
        return results[-1][1]

    try:
        invoke("project", lambda: bridge.openProject(str(project_path)))
        context = invoke("property_context", lambda: bridge.loadPropertyContext("key"))
        assert context["revision"] == ops.graph_project_info()["revision"]
        assert "key" in {node["id"] for node in context["nodes"]}

        records = [
            {"id": "key-color", "kind": "property_definition", "value_type": "string",
             "cardinality": "one", "persistence": "until_changed", "owner_node_types": ["item"]},
            {"id": "key-color-initial", "kind": "property_value", "node_id": "key",
             "property_id": "key-color", "value": "red", "event_id": "e1"},
        ]
        staged = invoke("property_stage", lambda: bridge.proposePropertyRecords(
            json.dumps(records), ops.graph_project_info()["revision"]
        ))
        assert staged["status"] == "pending"
        assert staged["candidate"]["records"] == records
        revision = invoke("property_review", lambda: bridge.acceptPropertyCandidate(
            staged["candidate"]["id"], staged["candidate"]["version"]
        ))
        assert revision["status"] == "accepted"

        query = invoke("property_state", lambda: bridge.queryProperty("key", "key-color"))
        assert query["state"]["state"] == {"kind": "known", "value": "red"}
        assert {row["id"] for row in query["history"]} == {"key-color", "key-color-initial"}
    finally:
        bridge.close()
