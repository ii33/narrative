"""The canvas renders reviewed direct claims without inventing graph edges."""

from __future__ import annotations

import pytest

from narrative.native_models import CanvasState, node_detail, search_result

NODES = [
    {"id": "p", "name": "甲", "node_type": "person", "context_id": "canon"},
    {"id": "e", "name": "送别", "node_type": "event", "context_id": "canon"},
    {"id": "l", "name": "码头", "node_type": "location", "context_id": "canon"},
]
LABELS = {
    item["id"]: {"label": item["name"], "kind_label": item["node_type"], "summary": item["name"]}
    for item in NODES
}


def _edge(assertion_id: str, left: str, right: str) -> dict:
    return {
        "assertion_id": assertion_id, "subject_id": left, "object_id": right,
        "predicate_id": "participates", "label": "参与", "scope": "world", "origin": "original",
        "derived_status": "valid", "direct": True,
    }


def test_incremental_canvas_keeps_positions_and_direct_edges() -> None:
    canvas = CanvasState()
    overview = canvas.seed({"revision": 2, "nodes": NODES, "pending_excluded": True}, LABELS)
    assert [item["id"] for item in overview["nodes"]] == ["p"]
    assert overview["edges"] == []
    assert overview["pending_excluded"] is True
    canvas.set_position("p", 123, -45)

    adjacent = {
        "revision": 2, "center_id": "p", "center_status": "visible",
        "nodes": NODES[:2], "edges": [_edge("a1", "e", "p")], "pending_excluded": True,
    }
    first = canvas.expand(adjacent, LABELS)
    assert [node["id"] for node in first["nodes_added"]] == ["e"]
    assert [(edge["subject_id"], edge["object_id"]) for edge in first["edges_added"]] == [("e", "p")]
    assert canvas.expand(adjacent, LABELS)["edges_added"] == []
    assert canvas.expand(adjacent, LABELS)["already_expanded"] is True

    canvas.clear(preserve_positions=True)
    refreshed = canvas.seed({"revision": 3, "nodes": NODES}, LABELS)
    assert refreshed["nodes"][0]["x"] == 123
    assert refreshed["nodes"][0]["y"] == -45
    with pytest.raises(ValueError, match="变化"):
        canvas.expand(adjacent, LABELS)


def test_canvas_rejects_inferred_edge_and_does_not_draw_indirect_relation() -> None:
    canvas = CanvasState()
    canvas.seed({"revision": 1, "nodes": NODES}, LABELS)
    route = {
        "revision": 1, "center_id": "p", "center_status": "visible",
        "nodes": NODES, "edges": [_edge("a1", "e", "p"), _edge("a2", "e", "l")],
        "paths": [{"to_id": "l", "via_node_ids": ["e"], "derived": True}],
    }
    delta = canvas.expand(route, LABELS)
    assert len(delta["edges_added"]) == 2
    assert not any(edge["subject_id"] == "p" and edge["object_id"] == "l" for edge in delta["edges_added"])
    route["edges"].append(_edge("fake", "p", "l") | {"direct": False})
    with pytest.raises(ValueError, match="直接节点断言"):
        canvas.expand(route, LABELS)


def test_detail_keeps_evidence_scope_and_clickable_refs() -> None:
    view = {
        "revision": 2, "nodes": NODES[:2], "pending_excluded": True,
        "assertions": [{
            "id": "a1", "subject_id": "e", "object_id": "p", "predicate_id": "participates",
            "scope": "claim", "holder_id": "p", "origin": "inference",
            "evidence": [{"id": "q1", "quote": "甲送别", "source_id": "s1"}],
        }],
    }
    detail = node_detail(view, "e", LABELS, [{"id": "s1", "title": "第一章"}])
    assert detail["node"]["name"] == "送别"
    assert detail["assertions"][0]["scope"] == "claim"
    assert detail["assertions"][0]["evidence"][0]["quote"] == "甲送别"
    assert detail["assertions"][0]["evidence"][0]["source_title"] == "第一章"
    assert detail["assertions"][0]["object_label"] == "甲"
    assert [node["id"] for node in detail["related_nodes"]] == ["p"]
    assert [node["id"] for node in search_result(view, LABELS, ["person"])["nodes"]] == ["p"]

def test_collapse_keeps_shared_neighbors_and_positions():
    canvas = CanvasState()
    canvas.seed({"revision": 1, "nodes": NODES}, LABELS)
    first = {"revision": 1, "center_id": "p", "center_status": "visible", "nodes": NODES, "edges": [_edge("a", "p", "e"), _edge("b", "e", "l")]}
    canvas.expand(first, LABELS)
    canvas.set_position("l", 5, 6)
    canvas.expand(first | {"center_id": "e", "nodes": NODES[1:], "edges": [_edge("b", "e", "l")]}, LABELS)
    assert canvas.collapse("p")["edges_removed"] == ["a"]
    assert set(canvas.nodes) == {"p", "e", "l"}
    canvas.collapse("e")
    assert set(canvas.nodes) == {"p"}
    canvas.expand(first, LABELS)
    assert canvas.nodes["l"]["x"] == 5
    assert canvas.project({"temporal_only": True})["nodes"] == []
    assert len(canvas.nodes) == 3

def test_seed_events_draws_temporal_edges_and_collapse_preserves_anchors():
    canvas = CanvasState()
    nodes = [NODES[1], {"id": "e2", "name": "后来", "node_type": "event"}]
    edge = _edge("t", "e", "e2") | {"semantic": "temporal", "temporal_relation": "after", "temporal_left": {"boundary": "start"}}
    seeded = canvas.seed({"revision": 1, "nodes": nodes, "edges": [edge]}, LABELS, prefer_events=True)
    assert seeded["edges"][0]["temporal_relation"] == "after"
    assert seeded["edges"][0]["temporal_left"] == {"boundary": "start"}
    canvas.collapse("e")
    assert "t" in canvas.edges
    assert len(canvas.project({"node_types": ["person"]})["nodes"]) == 0
    assert len(canvas.project()["nodes"]) == 2

def test_explicit_empty_filters_hide_all_without_changing_cache():
    canvas = CanvasState()
    canvas.seed({"revision": 1, "nodes": NODES}, LABELS)
    canvas.expand({"revision": 1, "center_id": "p", "center_status": "visible", "nodes": NODES, "edges": [_edge("a", "p", "e")]}, LABELS)
    assert canvas.project({"node_types": []})["nodes"] == []
    assert canvas.project({"semantics": []})["edges"] == []
    assert len(canvas.project()["nodes"]) == 3
    assert canvas.project()["edges"][0]["temporal_certain"] is False

def test_canvas_mismatched_temporal_boundary_is_not_certain():
    from narrative.native_models import _edge as project_edge

    edge = _edge("time", "e", "other") | {"semantic": "temporal", "polarity": "positive", "modality": "actual",
        "temporal_left": {"form": "event_boundary", "event_id": "other", "boundary": "start"}}
    assert project_edge(edge)["temporal_certain"] is False
    edge["temporal_left"]["event_id"] = "e"
    assert project_edge(edge)["temporal_certain"] is True


def test_detail_keeps_history_evidence_but_only_active_parent_is_related():
    nodes = [{"id": key, "name": key, "node_type": "event", "context_id": "canon"}
             for key in ("child", "old", "new", "pending")]
    labels = {n["id"]: {"label": n["name"]} for n in nodes}
    view = {"revision": 3, "nodes": nodes, "assertions": [
        {"id": status, "subject_id": parent, "object_id": "child", "semantic": "containment",
         "organization_status": status, "evidence": [{"quote": "原文", "source_id": "s1"}]}
        for status, parent in (("superseded", "old"), ("active", "new"), ("unresolved", "pending"))
    ]}
    detail = node_detail(view, "child", labels, [{"id": "s1", "title": "章一"}])
    assert [n["id"] for n in detail["related_nodes"]] == ["new"]
    assert [a["organization_label"] for a in detail["assertions"]] == ["历史归属", "当前归属", "归属待确认"]
    assert all(a["evidence"][0]["source_title"] == "章一" for a in detail["assertions"])

