"""Native property bridge flow through the review-gated Operations API."""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from narrative.native_bridge import NativeBridge
from narrative.operations import Operations


def test_native_property_candidate_and_state_history(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    project_path = tmp_path / "property-native.nproj"
    ops = Operations.create_graph_project(project_path)
    nodes = [
        {"kind": "node", "id": "key", "node_type": "item", "name": "Key"},
        {"kind": "node", "id": "e1", "node_type": "event", "name": "查看钥匙"},
    ]
    candidate_id = ops.graph_propose(nodes, ops.graph_project_info()["revision"])
    ops.graph_accept(candidate_id, 1)

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


def test_a_retracted_holder_stops_appearing_on_the_old_owner(tmp_path: Path) -> None:
    """换手之后，旧持有者的「反向引用」里不该还挂着这件东西。"""
    from narrative.project_catalog import create_graph_project

    ops = create_graph_project(tmp_path / "holder.nproj")
    source_id = ops.import_source("第一章", "老周把铜钥匙交给林舟。")
    revision = ops.graph_project_info()["revision"]
    quote = {"source_id": source_id, "quote": "老周把铜钥匙交给林舟。"}
    ops.change_accept(ops.change_propose(
        [
            {"kind": "source_order", "id": "o1", "source_id": source_id,
             "ordinal": 1, "context_id": "canon"},
            {"kind": "node", "id": "E1", "node_type": "event", "name": "交钥匙",
             "disclosure": {"source_id": source_id, "start": 0}, "evidence": quote},
            {"kind": "node", "id": "P1", "node_type": "person", "name": "老周",
             "disclosure": {"source_id": source_id, "start": 0}},
            {"kind": "node", "id": "P2", "node_type": "person", "name": "林舟",
             "disclosure": {"source_id": source_id, "start": 0}},
            {"kind": "node", "id": "I1", "node_type": "item", "name": "铜钥匙",
             "disclosure": {"source_id": source_id, "start": 0}},
        ],
        [
            {"kind": "property_definition", "id": "持有者", "value_type": "either",
             "cardinality": "one", "persistence": "until_changed"},
            {"kind": "property_value", "id": "v1", "node_id": "I1", "property_id": "持有者",
             "value": "P1", "event_id": "E1",
             "disclosure": {"source_id": source_id, "start": 0}, "evidence": quote},
            {"kind": "property_value", "id": "v2", "node_id": "I1", "property_id": "持有者",
             "value": "P2", "event_id": "E1",
             "disclosure": {"source_id": source_id, "start": 6}, "evidence": quote},
            # 换手：提取侧把「旧持有者失效」和值一起带进来。
            {"kind": "property_retraction", "id": "r1", "target_id": "v1",
             "reason": "被后一条披露取代", "event_id": "E1", "basis": "audit",
             "disclosure": {"source_id": source_id, "start": 6}},
        ],
        revision,
        source="manual",
    ), 1)

    old_owner = ops.property_node_summary("P1")
    new_owner = ops.property_node_summary("P2")
    assert [row["node_name"] for row in old_owner["inbound"]] == []
    assert [row["node_name"] for row in new_owner["inbound"]] == ["铜钥匙"]
