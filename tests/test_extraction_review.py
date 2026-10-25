"""Second-pass property review: only what is genuinely new survives."""

from __future__ import annotations

from pathlib import Path

from narrative.extraction_review import _apply, _stored_values
from narrative.project_catalog import create_graph_project

TEXT = "林舟脸色黝黑。林舟脸色变得白净。"
NODES = [
    {"kind": "node", "id": "e1", "node_type": "event", "name": "林舟出场",
     "disclosure": {"source_id": None, "start": 0}},
    {"kind": "node", "id": "e2", "node_type": "event", "name": "林舟被改造",
     "disclosure": {"source_id": None, "start": 6}, "predecessors": ["e1"]},
    {"kind": "node", "id": "p1", "node_type": "person", "name": "林舟",
     "disclosure": {"source_id": None, "start": 0}},
]


def _project(tmp_path: Path):
    ops = create_graph_project(tmp_path / "graph.sqlite3")
    source_id = ops.import_source("第一章", TEXT)
    nodes = []
    for item in NODES:
        node = dict(item)
        node["disclosure"] = {"source_id": source_id, "start": item["disclosure"]["start"]}
        nodes.append(node)
    ops.change_accept(ops.change_propose(
        [
            {"kind": "source_order", "id": "o1", "source_id": source_id,
             "ordinal": 1, "context_id": "canon"},
            *nodes,
        ],
        [
            {"kind": "property_definition", "id": "skin", "value_type": "string",
             "cardinality": "one", "persistence": "until_changed"},
            {"kind": "property_value", "id": "v1", "node_id": "p1", "property_id": "skin",
             "value": "黝黑", "event_id": "e1",
             "evidence": {"source_id": source_id, "quote": "林舟脸色黝黑"}},
        ],
        ops.graph_project_info()["revision"], source="manual",
    ), 1)
    return ops, source_id


def test_stored_values_only_cover_proposed_properties(tmp_path: Path) -> None:
    ops, _ = _project(tmp_path)
    data = {"properties": [
        {"node": "林舟", "property": "skin", "value": "白净", "quotes": ["x"]},
        {"node": "林舟", "property": "age", "value": 29, "quotes": ["y"]},
    ]}
    rows = _stored_values(ops, data)
    assert [(row["node"], row["property"], row["value"]) for row in rows] == [
        ("林舟", "skin", "黝黑")
    ]


def test_apply_keeps_only_approved_values(tmp_path: Path) -> None:
    ops, _ = _project(tmp_path)
    data = {"properties": [
        {"node": "林舟", "property": "skin", "value": "黝黑", "quotes": ["a"], "at_event": ""},
        {"node": "林舟", "property": "skin", "value": "白净", "quotes": ["b"], "at_event": ""},
    ]}
    result = _apply(
        data,
        keep=[{"node": "林舟", "property": "skin", "value": "白净", "at_event": "林舟被改造"}],
        drop=[{"node": "林舟", "property": "skin", "value": "黝黑", "reason": "与已记录值相同"}],
        rounds=2,
    )
    assert [item["value"] for item in result["properties"]] == ["白净"]
    assert result["properties"][0]["at_event"] == "林舟被改造"
    assert result["dropped"][0]["reason"] == "与已记录值相同"
    assert result["rounds"] == 2


def test_apply_reports_unresolved_separately(tmp_path: Path) -> None:
    """A value the model still cannot judge is kept but flagged, not silently dropped."""
    ops, _ = _project(tmp_path)
    data = {"properties": [
        {"node": "林舟", "property": "skin", "value": "白净", "quotes": ["b"], "at_event": ""},
    ]}
    result = _apply(
        data,
        keep=[{"node": "林舟", "property": "skin", "value": "白净", "at_event": ""}],
        drop=[],
        rounds=3,
        unresolved=[{"node": "林舟", "property": "skin", "value": "白净",
                     "question": "不确定是否同义"}],
    )
    assert len(result["properties"]) == 1
    assert result["unresolved"][0]["question"] == "不确定是否同义"
    assert result["rounds"] == 3
