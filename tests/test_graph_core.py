"""The v5 graph keeps reviewed temporal/containment claims and property records.

Ordinary attribute, relation and participation assertions are retired in v5:
node state is expressed by append-only property records instead. These tests
exercise the reviewed candidate flow, disclosure/revision cuts, and the
property-state projection that replaces the old relation/attribute edges.
"""

from __future__ import annotations

import sqlite3

import pytest

from narrative.operations import Operations


def _evidence(ops, source_id: str, quote: str, evidence_id: str) -> dict:
    return ops.locate(source_id, quote)[0] | {"id": evidence_id}


def _boundary(event: str, phase: str) -> dict:
    return {"timeline_id": "story", "form": "event_boundary", "event_id": event, "boundary": phase}


def _number(value: float) -> dict:
    return {"timeline_id": "story", "form": "number", "number": value}


def _base(ops, source_id):
    return [
        {"kind": "node", "id": "p1", "node_type": "person", "name": "甲", "aliases": ["老甲"]},
        {"kind": "node", "id": "p2", "node_type": "person", "name": "甲"},
        {"kind": "node", "id": "p3", "node_type": "person", "name": "乙"},
        {"kind": "node", "id": "key", "node_type": "item", "name": "钥匙"},
        {"kind": "node", "id": "place", "node_type": "location", "name": "码头"},
        {"kind": "node", "id": "scene", "node_type": "scene", "name": "码头一景"},
        {"kind": "node", "id": "e1", "node_type": "event", "name": "送别", "occurrence": {"kind": "point", "point": _number(1)}},
        {"kind": "node", "id": "e2", "node_type": "event", "name": "交钥匙", "occurrence": {"kind": "point", "point": _number(2)}},
        {"kind": "predicate", "id": "contains", "label": "包含", "semantic": "containment", "value_type": "node"},
        {"kind": "predicate", "id": "before", "label": "先于", "semantic": "temporal", "temporal_relation": "before", "subject_types": ["event"], "object_types": ["event"], "value_type": "node"},
        _evidence(ops, source_id, "甲在码头送别乙", "q1"),
        _evidence(ops, source_id, "甲说钥匙丢了", "q2"),
        _evidence(ops, source_id, "甲把钥匙交给乙", "q3"),
        {"kind": "source_order", "id": "so1", "source_id": source_id, "ordinal": 1},
    ]


def _claim(assertion_id, predicate_id, subject_id, *, object_id=None, value=None, **extra):
    return {"kind": "assertion", "id": assertion_id, "subject_id": subject_id, "predicate_id": predicate_id, "object_id": object_id, "value": value, **extra}


def _link(assertion_id, evidence_id):
    return {"kind": "evidence_link", "id": f"link-{assertion_id}", "assertion_id": assertion_id, "evidence_id": evidence_id}


def test_graph_review_context_time_and_as_of(tmp_path):
    path = tmp_path / "new.nproj"
    ops = Operations.create_graph_project(path)
    source_id = ops.import_source("一章", "甲在码头送别乙。甲说钥匙丢了。后来甲把钥匙交给乙。")
    before = ops.graph_project_info()["revision"]

    # v4 ordinary relation/attribute/participation claims are explicitly rejected.
    retired = [
        {"kind": "predicate", "id": "holder", "label": "持有", "semantic": "relation", "subject_types": ["item"], "object_types": ["person"], "functional": True},
    ]
    assert "PREDICATE_SEMANTIC" in {d["code"] for d in ops.graph_precheck(retired, before)}
    with pytest.raises(ValueError, match="只接受"):
        ops.graph_propose(retired, before)

    # v5 equivalent graph records: containment organizes the scene, temporal
    # orders the events; node state is added separately as property records.
    operations = _base(ops, source_id) + [
        _claim("a_cont_e1", "contains", "scene", object_id="e1", disclosure={"source_id": source_id, "start": 4}),
        _link("a_cont_e1", "q1"),
        _claim("a_before", "before", "e1", object_id="e2",
               temporal_left=_boundary("e1", "end"), temporal_right=_boundary("e2", "start")),
        _link("a_before", "q3"),
    ]
    assert ops.graph_precheck(operations, before) == []
    candidate_id = ops.graph_propose(operations, before, source="model")
    assert ops.graph_query()["nodes"] == []  # pending cannot leak into accepted graph
    assert ops.graph_candidate(candidate_id)["status"] == "pending"
    preview = ops.graph_candidate_preview(candidate_id)
    assert "送别 → 先于 → 交钥匙" == preview["display_map"]["items"]["a_before"]["label"]
    assert preview["display_map"]["items"]["p1"]["qualified_label"] != "甲"
    revision = ops.graph_accept(candidate_id, 1)
    assert revision == before + 1
    current = ops.graph_query(node_id="scene")
    assert {a["id"] for a in current["assertions"]} == {"a_cont_e1"}
    assert {n["name"] for n in current["nodes"]} == {"码头一景", "送别"}
    assert ops.graph_query(node_id="e1")["edges"] and all(e["direct"] for e in ops.graph_query(node_id="e1")["edges"])
    assert ops.graph_query(node_id="scene", disclosure_cut={"source_id": source_id, "start": 0})["assertions"] == []
    early = ops.graph_query(disclosure_cut={"source_id": source_id, "start": 0})
    assert early["nodes"] == []  # no unrevealed identity label leaks through overview
    assert ops.graph_query(query="甲", disclosure_cut={"source_id": source_id, "start": 0})["nodes"] == []
    assert ops.graph_snapshot(as_of_revision=before)["nodes"] == []
    assert ops.graph_snapshot(as_of_revision=revision)["evidence_links"]
    vicinity = ops.graph_neighborhood("scene", depth=2, node_types=["event"])
    assert [node["id"] for node in vicinity["endpoints"]] == ["e1", "e2"]
    assert {node["id"] for node in vicinity["nodes"]} == {"scene", "e1", "e2"}
    assert len(vicinity["paths"]) == 2
    route = next(path for path in vicinity["paths"] if path["to_id"] == "e2")
    assert route["via_node_ids"] == ["e1"]
    assert route["assertion_ids"] == ["a_cont_e1", "a_before"]
    assert [step["traversal"] for step in route["steps"]] == ["forward", "forward"]
    assert route["derived"] is True and route["direct"] is False
    assert not any({edge["subject_id"], edge["object_id"]} == {"scene", "e2"} for edge in vicinity["edges"])
    assert ops.graph_neighborhood("scene", depth=1, node_types=["location"])["paths"] == []
    assert ops.graph_neighborhood("scene", depth=2, node_types=["location"], disclosure_cut={"source_id": source_id, "start": 0})["nodes"] == []

    # The retired holder relation is replaced by an append-only property whose
    # story-time projection answers at numeric and event-boundary cuts.
    prop_revision = ops.graph_project_info()["revision"]
    records = [
        {"kind": "property_definition", "id": "holder", "value_type": "node", "cardinality": "one", "persistence": "until_changed", "target_node_types": ["person"]},
        {"kind": "property_value", "id": "key-holder", "node_id": "key", "property_id": "holder", "value": "p1", "event_id": "e1"},
        {"kind": "property_value", "id": "handoff-holder", "node_id": "key", "property_id": "holder", "value": "p3", "event_id": "e2"},
    ]
    assert ops.property_precheck(records, prop_revision) == []
    pending = ops.property_propose(records, prop_revision, source="model")
    assert ops.property_candidate(pending)["status"] == "pending"
    ops.property_accept(pending, 1)
    # Reading-position evaluation only; story-time cuts are out of scope here.
    assert ops.property_state("key", "holder")["state"] == {"kind": "known", "value": "p3"}
    with pytest.raises(ValueError, match="不存在"):
        Operations.open_graph_project(tmp_path / "old.nproj")


def test_graph_temporal_cycle_missing_reference_and_shared_evidence(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "p.nproj")
    source_id = ops.import_source("正文", "先开门，后关门。")
    base = [
        {"kind": "node", "id": "e1", "node_type": "event", "name": "开门"},
        {"kind": "node", "id": "e2", "node_type": "event", "name": "关门"},
        {"kind": "predicate", "id": "before", "label": "先于", "semantic": "temporal", "temporal_relation": "before", "subject_types": ["event"], "object_types": ["event"], "value_type": "node"},
        _evidence(ops, source_id, "先开门，后关门", "q"),
    ]
    rev = ops.graph_project_info()["revision"]
    cid = ops.graph_propose(base, rev)
    ops.graph_accept(cid, 1)
    ref1 = {"timeline_id": "story", "form": "event_boundary", "event_id": "e1", "boundary": "end"}
    ref2 = {"timeline_id": "story", "form": "event_boundary", "event_id": "e2", "boundary": "start"}
    operations = [
        _claim("t1", "before", "e1", object_id="e2", temporal_left=ref1, temporal_right=ref2),
        _link("t1", "q"),
        _claim("t2", "before", "e2", object_id="e1", temporal_left=ref2, temporal_right=ref1),
        _link("t2", "q"),
    ]
    codes = {d["code"] for d in ops.graph_precheck(operations, ops.graph_project_info()["revision"])}
    assert "TIME_CYCLE" in codes
    operations[2]["object_id"] = "missing"
    codes = {d["code"] for d in ops.graph_precheck(operations, ops.graph_project_info()["revision"])}
    assert "REF" in codes
    assert all(link["evidence_id"] == "q" for link in (operations[1], operations[3]))


def test_graph_refuses_legacy_database_without_editing_it(tmp_path):
    path = tmp_path / "old.nproj"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=3")
    with pytest.raises(ValueError, match="v4"):
        Operations.open_graph_project(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3


def test_graph_event_cut_partial_order_and_dependency_revision(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "relative.nproj")
    sid = ops.import_source("交接", "甲把钥匙交给乙。随后乙开门。最后乙锁门。")
    q = _evidence(ops, sid, "甲把钥匙交给乙", "q")
    base = [
        {"kind": "node", "id": "key", "node_type": "item", "name": "钥匙"},
        {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
        {"kind": "node", "id": "p2", "node_type": "person", "name": "乙"},
        {"kind": "node", "id": "handoff", "node_type": "event", "name": "交接", "occurrence": {"kind": "point", "point": _number(2)}},
        {"kind": "node", "id": "open", "node_type": "event", "name": "开门", "occurrence": {"kind": "point", "point": _number(3)}},
        {"kind": "node", "id": "lock", "node_type": "event", "name": "锁门", "occurrence": {"kind": "point", "point": _number(4)}},
        {"kind": "predicate", "id": "precedes", "label": "先于", "semantic": "temporal", "temporal_relation": "before", "subject_types": ["event"], "object_types": ["event"], "value_type": "node"},
        q,
    ]
    base += [
        _claim("order1", "precedes", "handoff", object_id="open", temporal_left=_boundary("handoff", "end"), temporal_right=_boundary("open", "start")),
        _link("order1", "q"),
        _claim("order2", "precedes", "open", object_id="lock", temporal_left=_boundary("open", "start"), temporal_right=_boundary("lock", "start")),
        _link("order2", "q"),
    ]
    rev = ops.graph_project_info()["revision"]
    assert ops.graph_precheck(base, rev) == []
    ops.graph_accept(ops.graph_propose(base, rev), 1)
    # Explicit event boundary partial order, never derived from boundaries alone.
    assert ops.graph_event_compare("handoff", "open", axis="story")["relation"] == "before"
    assert ops.graph_event_compare("open", "lock", axis="story")["relation"] == "before"
    assert ops.graph_event_compare("handoff", "lock", axis="story")["relation"] == "before"

    # v5 replaces the retired holder relation with an event-driven property.
    prop_rev = ops.graph_project_info()["revision"]
    records = [
        {"kind": "property_definition", "id": "holder", "value_type": "node", "cardinality": "one", "persistence": "until_changed", "target_node_types": ["person"]},
        {"kind": "property_value", "id": "key-holder", "node_id": "key", "property_id": "holder", "value": "p1", "event_id": "open"},
        {"kind": "property_value", "id": "handoff-holder", "node_id": "key", "property_id": "holder", "value": "p2", "event_id": "handoff"},
    ]
    assert ops.property_precheck(records, prop_rev) == []
    ops.property_accept(ops.property_propose(records, prop_rev), 1)
    # Story-time evaluation is out of scope for this version; the value sequence
    # answers by reading position only.
    assert ops.property_state("key", "holder")["state"] == {"kind": "known", "value": "p2"}

    # A derived interpretation becomes stale when its accepted premise is edited later.
    extra = [
        _claim("derived", "precedes", "handoff", object_id="lock", temporal_left=_boundary("handoff", "end"), temporal_right=_boundary("lock", "start"), origin="inference", derived_from=["order1"]),
        _link("derived", "q"),
    ]
    rev = ops.graph_project_info()["revision"]
    assert ops.graph_precheck(extra, rev) == []
    ops.graph_accept(ops.graph_propose(extra, rev), 1)
    correction = [
        _claim("order1", "precedes", "handoff", object_id="open", temporal_left=_boundary("handoff", "end"), temporal_right=_boundary("open", "start"), assessment="retracted"),
    ]
    rev = ops.graph_project_info()["revision"]
    assert ops.graph_precheck(correction, rev) == []
    ops.graph_accept(ops.graph_propose(correction, rev, source="correction"), 1)
    derived = next(a for a in ops.graph_query()["assertions"] if a["id"] == "derived")
    assert derived["derived_status"] == "invalid_dependency"


def test_graph_source_order_is_explicit_and_unique(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "order.nproj")
    first = ops.import_source("甲章", "甲先发现钥匙。")
    second = ops.import_source("乙章", "乙后来得知。")
    revision = ops.graph_project_info()["revision"]
    duplicate = [
        {"kind": "source_order", "id": "o1", "source_id": first, "ordinal": 1},
        {"kind": "source_order", "id": "o2", "source_id": second, "ordinal": 1},
    ]
    assert "SOURCE_ORDER" in {item["code"] for item in ops.graph_precheck(duplicate, revision)}
    assert ops.graph_snapshot()["source_orders"] == []  # importing did not invent reading order


def test_graph_branch_and_claim_are_not_promoted_to_canon_world(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "contexts.nproj")
    sid = ops.import_source("分支", "甲说钥匙在乙手里；假设甲仍持有。")
    base = [
        {"kind": "node", "id": "key", "node_type": "item", "name": "钥匙"},
        {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
        {"kind": "node", "id": "p2", "node_type": "person", "name": "乙"},
        {"kind": "node", "id": "e1", "node_type": "event", "name": "交钥匙"},
        {"kind": "node", "id": "e2", "node_type": "event", "name": "持有钥匙"},
        {"kind": "context", "id": "branch", "label": "假设分支", "context_type": "branch", "parent_id": "canon"},
        {"kind": "predicate", "id": "before", "label": "先于", "semantic": "temporal", "temporal_relation": "before", "subject_types": ["event"], "object_types": ["event"], "value_type": "node"},
        _evidence(ops, sid, "甲说钥匙在乙手里", "q1"),
        _evidence(ops, sid, "假设甲仍持有", "q2"),
        _claim("said", "before", "e1", object_id="e2", scope="claim", holder_id="p1",
               temporal_left=_boundary("e1", "end"), temporal_right=_boundary("e2", "start")),
        _link("said", "q1"),
        _claim("branch-before", "before", "e1", object_id="e2", context_id="branch", modality="hypothetical",
               temporal_left=_boundary("e1", "end"), temporal_right=_boundary("e2", "start")),
        _link("branch-before", "q2"),
    ]
    rev = ops.graph_project_info()["revision"]
    assert ops.graph_precheck(base, rev) == []
    ops.graph_accept(ops.graph_propose(base, rev), 1)
    canon = ops.graph_query(node_id="e1")
    assert {a["id"] for a in canon["assertions"]} == {"said"}
    assert canon["assertions"][0]["scope"] == "claim"
    branch = ops.graph_query(node_id="e1", context_id="branch")
    assert {a["id"] for a in branch["assertions"]} == {"said", "branch-before"}
