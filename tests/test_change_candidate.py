"""Unified graph + property change candidates."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from narrative.operations import Operations


def _project(tmp_path: Path):
    ops = Operations.create_graph_project(tmp_path / "graph.sqlite3")
    ops.import_source("t", "甲把信交给乙。")
    return ops


def _mixed():
    nodes = [
        {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
        {"kind": "node", "id": "p2", "node_type": "person", "name": "乙"},
        {"kind": "node", "id": "letter", "node_type": "item", "name": "信"},
        {"kind": "node", "id": "e1", "node_type": "event", "name": "交付"},
        {"kind": "node", "id": "e2", "node_type": "event", "name": "转手"},
    ]
    records = [
        {
            "kind": "property_definition", "id": "holder", "value_type": "node",
            "cardinality": "one", "persistence": "until_changed",
            "target_node_types": ["person"],
        },
        {
            "kind": "property_value", "id": "letter-holder-init",
            "node_id": "letter", "property_id": "holder",
            "value": "p1", "event_id": "e1",
        },
        {
            "kind": "property_value", "id": "handoff",
            "node_id": "letter", "property_id": "holder",
            "value": "p2", "event_id": "e2",
        },
    ]
    return nodes, records


def test_mixed_candidate_accepts_in_one_revision(tmp_path):
    ops = _project(tmp_path)
    revision = ops.graph_project_info()["revision"]
    nodes, records = _mixed()

    assert ops.change_precheck(nodes, records, revision) == []
    candidate_id = ops.change_propose(nodes, records, revision, source="manual", note="mixed")
    assert ops.change_candidate(candidate_id)["status"] == "pending"

    next_revision = ops.change_accept(candidate_id, 1)

    assert next_revision == revision + 1
    snapshot = ops.graph_snapshot()
    assert {node["id"] for node in snapshot["nodes"]} == {"p1", "p2", "letter", "e1", "e2"}
    assert ops.property_state("letter", "holder")["state"] == {"kind": "known", "value": "p2"}
    assert ops.change_candidate(candidate_id)["status"] == "accepted"


def test_invalid_property_blocks_propose_and_writes_nothing(tmp_path):
    ops = _project(tmp_path)
    revision = ops.graph_project_info()["revision"]
    nodes, _ = _mixed()
    bad = [{
        "kind": "property_value", "id": "bad", "node_id": "ghost",
        "property_id": "holder", "value": "p1",
    }]

    diagnostics = ops.change_precheck(nodes, bad, revision)
    assert any(item["family"] == "property" and item["severity"] == "error" for item in diagnostics)
    with pytest.raises(ValueError):
        ops.change_propose(nodes, bad, revision, source="manual")
    assert ops.graph_project_info()["revision"] == revision
    assert ops.graph_snapshot()["nodes"] == []
    assert ops.property_history("letter", "holder") == []


def test_stale_candidate_is_rejected_without_partial_write(tmp_path):
    ops = _project(tmp_path)
    revision = ops.graph_project_info()["revision"]
    nodes, records = _mixed()
    stale = ops.change_propose(nodes, records, revision, source="manual")

    # Another accepted change bumps the global revision before the stale one.
    other = ops.change_propose(
        [{"kind": "node", "id": "x", "node_type": "person", "name": "丙"}],
        [],
        revision,
        source="manual",
    )
    ops.change_accept(other, 1)

    with pytest.raises(ValueError, match="版本已变化"):
        ops.change_accept(stale, 1)
    assert "letter" not in {node["id"] for node in ops.graph_snapshot()["nodes"]}
    assert ops.property_history("letter", "holder") == []


def test_graph_only_and_property_only_candidates(tmp_path):
    ops = _project(tmp_path)
    revision = ops.graph_project_info()["revision"]
    graph_only = ops.change_propose(
        [
            {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
            {"kind": "node", "id": "e1", "node_type": "event", "name": "出发"},
        ],
        [],
        revision,
        source="manual",
    )
    revision = ops.change_accept(graph_only, 1)
    assert "p1" in {node["id"] for node in ops.graph_snapshot()["nodes"]}

    property_only = ops.change_propose(
        [],
        [
            {
                "kind": "property_definition", "id": "location", "value_type": "string",
                "cardinality": "one", "persistence": "until_changed",
            },
            {
                "kind": "property_value", "id": "p1-location", "node_id": "p1",
                "property_id": "location", "value": "某处", "event_id": "e1",
            },
        ],
        revision,
        source="manual",
    )
    revision = ops.change_accept(property_only, 1)
    assert ops.property_state("p1", "location")["state"] == {"kind": "known", "value": "某处"}
    assert ops.graph_project_info()["revision"] == revision


def test_separate_graph_and_property_apis_still_work(tmp_path):
    ops = _project(tmp_path)
    revision = ops.graph_project_info()["revision"]
    nodes, records = _mixed()
    graph_candidate = ops.graph_propose(nodes, revision, source="manual")
    revision = ops.graph_accept(graph_candidate, 1)
    property_candidate = ops.property_propose(records, revision, source="manual")
    revision = ops.property_accept(property_candidate, 1)
    assert ops.property_state("letter", "holder")["state"] == {"kind": "known", "value": "p2"}
    assert ops.graph_project_info()["revision"] == revision


def test_mid_transaction_failure_rolls_back_everything(tmp_path):
    ops = _project(tmp_path)
    revision = ops.graph_project_info()["revision"]
    nodes, records = _mixed()
    records = [dict(record) for record in records]
    records[1]["id"] = "boom"  # property_initial; precheck sees a valid new id

    with sqlite3.connect(ops.graph_project_info()["path"]) as db:
        db.execute(
            "CREATE TRIGGER fail_boom BEFORE INSERT ON property_records "
            "WHEN NEW.id='boom' BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )

    candidate = ops.change_propose(nodes, records, revision, source="manual")
    with pytest.raises(sqlite3.DatabaseError):
        ops.change_accept(candidate, 1)

    assert ops.graph_project_info()["revision"] == revision
    assert ops.graph_snapshot()["nodes"] == []
    assert ops.property_history("letter", "holder") == []
    assert ops.change_candidate(candidate)["status"] == "pending"


def test_stale_candidate_version_cannot_bypass_base_revision(tmp_path):
    ops = _project(tmp_path)
    revision = ops.graph_project_info()["revision"]
    nodes, records = _mixed()
    stale = ops.change_propose(nodes, records, revision, source="manual")
    other = ops.change_propose(
        [{"kind": "node", "id": "x", "node_type": "person", "name": "丙"}],
        [],
        revision,
        source="manual",
    )
    ops.change_accept(other, 1)

    # A newer candidate version must not make the stale base revision acceptable.
    with pytest.raises(ValueError):
        ops.change_accept(stale, 2)
    assert ops.change_candidate(stale)["status"] == "pending"
    assert ops.graph_project_info()["revision"] == revision + 1


def test_opening_project_does_not_create_change_table(tmp_path):
    ops = _project(tmp_path)
    path = ops.graph_project_info()["path"]
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE change_candidates")
    reopened = Operations.open_graph_project(path)
    with sqlite3.connect(path) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='change_candidates'"
        ).fetchone()
    assert exists is None
    assert reopened.change_candidates() == []

    nodes, records = _mixed()
    candidate = reopened.change_propose(
        nodes, records, reopened.graph_project_info()["revision"], source="manual"
    )
    with sqlite3.connect(path) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='change_candidates'"
        ).fetchone()
    assert exists is not None
    assert reopened.change_candidate(candidate)["status"] == "pending"
