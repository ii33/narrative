"""Event pool: branch, merge, parallel, isolated, containment."""

from __future__ import annotations

from pathlib import Path

from narrative.project_catalog import create_graph_project

TEXT = "甲。乙。丙。丁。戊。己。庚。"
NODES = [
    {"kind": "node", "id": "a", "node_type": "event", "name": "甲",
     "disclosure": {"source_id": None, "start": 0}, "successors": ["b", "c"]},
    {"kind": "node", "id": "b", "node_type": "event", "name": "乙",
     "disclosure": {"source_id": None, "start": 2}, "predecessors": ["a"], "successors": ["d"]},
    {"kind": "node", "id": "c", "node_type": "event", "name": "丙",
     "disclosure": {"source_id": None, "start": 4}, "predecessors": ["a"], "successors": ["d"]},
    {"kind": "node", "id": "d", "node_type": "event", "name": "丁",
     "disclosure": {"source_id": None, "start": 6}, "predecessors": ["b", "c"]},
    {"kind": "node", "id": "e", "node_type": "event", "name": "戊",
     "disclosure": {"source_id": None, "start": 8}},
]


def _project(tmp_path: Path):
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", TEXT)
    nodes = []
    for item in NODES:
        node = dict(item)
        node["disclosure"] = {"source_id": source_id, "start": item["disclosure"]["start"]}
        nodes.append(node)
    candidate = ops.change_propose(
        [
            {"kind": "source_order", "id": "o1", "source_id": source_id,
             "ordinal": 1, "context_id": "canon"},
            *nodes,
        ],
        [], ops.graph_project_info()["revision"], source="manual",
    )
    ops.change_accept(candidate, 1)
    return ops, source_id


def test_branch_and_merge_produce_parallel(tmp_path: Path) -> None:
    """b and c leave a together and meet at d: they are parallel, not ordered."""
    ops, _ = _project(tmp_path)
    result = ops.graph_event_compare("b", "c", axis="story")
    assert result["relation"] == "parallel"
    assert result["basis"] == "chain"
    assert "共同前驱" in result["reason"] or "同一后继" in result["reason"]


def test_branch_ancestor_is_before_both_branches(tmp_path: Path) -> None:
    ops, _ = _project(tmp_path)
    assert ops.graph_event_compare("a", "b", axis="story")["relation"] == "before"
    assert ops.graph_event_compare("a", "d", axis="story")["relation"] == "before"
    assert ops.graph_event_compare("b", "a", axis="story")["relation"] == "after"
    assert ops.graph_event_compare("d", "b", axis="story")["relation"] == "after"


def test_isolated_event_is_unknown(tmp_path: Path) -> None:
    ops, _ = _project(tmp_path)
    result = ops.graph_event_compare("e", "a", axis="story")
    assert result["relation"] == "unknown"
    assert "孤立" in result["reason"]


def test_containment_reports_contains_and_contained_by(tmp_path: Path) -> None:
    """A parent event that organizes a child reports contains, not before."""
    ops, source_id = _project(tmp_path)
    quote = "己。庚。"
    start = TEXT.index(quote)
    candidate = ops.change_propose(
        [
            {"kind": "predicate", "id": "contains_event", "label": "包含事件",
             "semantic": "containment", "value_type": "node",
             "subject_types": ["scene", "event"], "object_types": ["event"]},
            {"kind": "node", "id": "f", "node_type": "event", "name": "己",
             "disclosure": {"source_id": source_id, "start": start}},
            {"kind": "node", "id": "g", "node_type": "event", "name": "庚",
             "disclosure": {"source_id": source_id, "start": start + 2}},
            {"kind": "evidence", "id": "ev_fg", "source_id": source_id,
             "source_hash": ops.source(source_id)["hash"],
             "start": start, "end": start + len(quote), "quote": quote},
            {"kind": "assertion", "id": "a_fg", "subject_id": "f", "predicate_id": "contains_event",
             "object_id": "g", "order_index": 0, "scope": "world", "context_id": "canon",
             "polarity": "positive", "modality": "actual", "origin": "original",
             "assessment": "supported",
             "disclosure": {"source_id": source_id, "start": start}},
            {"kind": "evidence_link", "id": "lk_fg", "assertion_id": "a_fg",
             "evidence_id": "ev_fg", "role": "supports"},
        ],
        [], ops.graph_project_info()["revision"], source="manual",
    )
    ops.change_accept(candidate, 1)

    assert ops.graph_event_compare("f", "g", axis="story")["relation"] == "contains"
    assert ops.graph_event_compare("g", "f", axis="story")["relation"] == "contained_by"
