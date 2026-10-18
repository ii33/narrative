"""The v4 graph has one reviewed, evidence-backed claim store."""

from __future__ import annotations

import sqlite3

import pytest

from narrative.operations import Operations


def _bound(state: str, number: float | None = None) -> dict:
    if number is None:
        return {"state": state}
    return {"state": "known", "ref": {"timeline_id": "story", "form": "number", "number": number}}


def _evidence(ops, source_id: str, quote: str, evidence_id: str) -> dict:
    return ops.locate(source_id, quote)[0] | {"id": evidence_id}


def _base(ops, source_id):
    return [
        {"kind": "node", "id": "p1", "node_type": "person", "name": "甲", "aliases": ["老甲"]},
        {"kind": "node", "id": "p2", "node_type": "person", "name": "甲"},
        {"kind": "node", "id": "p3", "node_type": "person", "name": "乙"},
        {"kind": "node", "id": "key", "node_type": "item", "name": "钥匙"},
        {"kind": "node", "id": "place", "node_type": "location", "name": "码头"},
        {"kind": "node", "id": "e1", "node_type": "event", "name": "送别"},
        {"kind": "node", "id": "e2", "node_type": "event", "name": "交钥匙"},
        {"kind": "predicate", "id": "holder", "label": "持有", "semantic": "relation", "subject_types": ["item"], "object_types": ["person"], "functional": True},
        {"kind": "predicate", "id": "participates", "label": "参与", "semantic": "participation", "subject_types": ["event"], "object_types": ["person"], "value_type": "node"},
        {"kind": "predicate", "id": "occurs_at", "label": "发生于", "semantic": "relation", "subject_types": ["event"], "object_types": ["location"], "value_type": "node"},
        {"kind": "predicate", "id": "lost", "label": "丢失", "semantic": "attribute", "subject_types": ["item"], "value_type": "scalar"},
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
    path = tmp_path / "new.sqlite3"
    ops = Operations.create_graph_project(path)
    source_id = ops.import_source("一章", "甲在码头送别乙。甲说钥匙丢了。后来甲把钥匙交给乙。")
    before = ops.graph_project_info()["revision"]
    operations = _base(ops, source_id) + [
        _claim("a_part", "participates", "e1", object_id="p1", qualifiers={"role": "送行者"}),
        _link("a_part", "q1"),
        _claim("a_place", "occurs_at", "e1", object_id="place"),
        _link("a_place", "q1"),
        _claim("a_lost", "lost", "key", value=True, scope="claim", holder_id="p1", modality="reported", disclosure={"source_id": source_id, "start": 9}),
        _link("a_lost", "q2"),
        _claim("a_hold_1", "holder", "key", object_id="p1", validity={"start": _bound("unbounded"), "end": _bound("known", 2)}),
        _link("a_hold_1", "q3"),
        _claim("a_hold_2", "holder", "key", object_id="p3", validity={"start": _bound("known", 2), "end": _bound("unbounded")}),
        _link("a_hold_2", "q3"),
    ]
    assert ops.graph_precheck(operations, before) == []
    candidate_id = ops.graph_propose(operations, before, source="model")
    assert ops.graph_query()["nodes"] == []  # pending cannot leak into accepted graph
    assert ops.graph_candidate(candidate_id)["status"] == "pending"
    preview = ops.graph_candidate_preview(candidate_id)
    assert "送别 → 参与 → 甲（送行者）" == preview["display_map"]["items"]["a_part"]["label"]
    assert preview["display_map"]["items"]["p1"]["qualified_label"] != "甲"
    revision = ops.graph_accept(candidate_id, 1)
    assert revision == before + 1
    current = ops.graph_query(node_id="key")
    assert {a["id"] for a in current["assertions"]} == {"a_lost", "a_hold_1", "a_hold_2"}
    assert current["nodes"] and {n["name"] for n in current["nodes"]} == {"甲", "乙", "钥匙"}
    assert {a["id"] for a in ops.graph_query(node_id="key", story_cut={"form": "number", "number": 1})["assertions"]} == {"a_hold_1"}
    assert {a["id"] for a in ops.graph_query(node_id="key", story_cut={"form": "number", "number": 3})["assertions"]} == {"a_hold_2"}
    assert "a_lost" not in {a["id"] for a in ops.graph_query(node_id="key", disclosure_cut={"source_id": source_id, "start": 0})["assertions"]}
    early = ops.graph_query(disclosure_cut={"source_id": source_id, "start": 0})
    assert early["nodes"] == []  # no unrevealed identity label leaks through overview
    assert ops.graph_query(query="甲", disclosure_cut={"source_id": source_id, "start": 0})["nodes"] == []
    assert ops.graph_snapshot(as_of_revision=before)["nodes"] == []
    assert ops.graph_snapshot(as_of_revision=revision)["evidence_links"]
    assert ops.graph_query(node_id="e1")["edges"][0]["direct"] is True
    vicinity = ops.graph_neighborhood("p1", depth=2, node_types=["location"])
    assert [node["id"] for node in vicinity["endpoints"]] == ["place"]
    assert {node["id"] for node in vicinity["nodes"]} == {"p1", "e1", "place"}
    assert len(vicinity["paths"]) == 1
    route = vicinity["paths"][0]
    assert route["via_node_ids"] == ["e1"]
    assert route["assertion_ids"] == ["a_part", "a_place"]
    assert [step["traversal"] for step in route["steps"]] == ["reverse", "forward"]
    assert route["derived"] is True and route["direct"] is False
    assert not any({edge["subject_id"], edge["object_id"]} == {"p1", "place"} for edge in vicinity["edges"])
    assert ops.graph_neighborhood("p1", depth=1, node_types=["location"])["paths"] == []
    assert ops.graph_neighborhood("p1", depth=2, node_types=["location"], disclosure_cut={"source_id": source_id, "start": 0})["nodes"] == []
    with pytest.raises(ValueError, match="不存在"):
        Operations.open_graph_project(tmp_path / "old.sqlite3")


def test_graph_temporal_cycle_missing_reference_and_shared_evidence(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "p.sqlite3")
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
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=3")
    with pytest.raises(ValueError, match="v4"):
        Operations.open_graph_project(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3


def test_graph_event_cut_partial_order_and_dependency_revision(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "relative.sqlite3")
    sid = ops.import_source("交接", "甲把钥匙交给乙。随后乙开门。最后乙锁门。")
    q = _evidence(ops, sid, "甲把钥匙交给乙", "q")
    base = [
        {"kind": "node", "id": "key", "node_type": "item", "name": "钥匙"},
        {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
        {"kind": "node", "id": "p2", "node_type": "person", "name": "乙"},
        *({"kind": "node", "id": name, "node_type": "event", "name": name} for name in ("handoff", "open", "lock")),
        {"kind": "predicate", "id": "holder", "label": "持有", "semantic": "relation", "subject_types": ["item"], "object_types": ["person"], "value_type": "node", "functional": True},
        {"kind": "predicate", "id": "precedes", "label": "先于", "semantic": "temporal", "temporal_relation": "before", "subject_types": ["event"], "object_types": ["event"], "value_type": "node"},
        q,
    ]
    def boundary(event, phase):
        return {"timeline_id": "story", "form": "event_boundary", "event_id": event, "boundary": phase}
    base += [
        _claim("old", "holder", "key", object_id="p1", validity={"start": _bound("unbounded"), "end": {"state": "known", "ref": boundary("handoff", "end")}}),
        _link("old", "q"),
        _claim("new", "holder", "key", object_id="p2", validity={"start": {"state": "known", "ref": boundary("handoff", "end")}, "end": _bound("unbounded")}),
        _link("new", "q"),
        _claim("order1", "precedes", "handoff", object_id="open", temporal_left=boundary("handoff", "end"), temporal_right=boundary("open", "start")),
        _link("order1", "q"),
        _claim("order2", "precedes", "open", object_id="lock", temporal_left=boundary("open", "start"), temporal_right=boundary("lock", "start")),
        _link("order2", "q"),
    ]
    rev = ops.graph_project_info()["revision"]
    assert ops.graph_precheck(base, rev) == []
    ops.graph_accept(ops.graph_propose(base, rev), 1)
    at_handoff = ops.graph_query(node_id="key", story_cut=boundary("handoff", "end"))
    assert [a["id"] for a in at_handoff["assertions"]] == ["new"]
    at_lock = ops.graph_query(node_id="key", story_cut=boundary("lock", "start"))
    assert [a["id"] for a in at_lock["assertions"]] == ["new"]
    # A derived interpretation becomes stale when its accepted premise is edited later.
    extra = [
        _claim("derived", "holder", "key", object_id="p2", origin="inference", derived_from=["new"]),
        _link("derived", "q"),
    ]
    rev = ops.graph_project_info()["revision"]
    ops.graph_accept(ops.graph_propose(extra, rev), 1)
    correction = [
        _claim("new", "holder", "key", object_id="p2", assessment="retracted", validity={"start": {"state": "known", "ref": boundary("handoff", "end")}, "end": _bound("unbounded")}),
    ]
    rev = ops.graph_project_info()["revision"]
    assert ops.graph_precheck(correction, rev) == []
    ops.graph_accept(ops.graph_propose(correction, rev, source="correction"), 1)
    derived = next(a for a in ops.graph_query(node_id="key")["assertions"] if a["id"] == "derived")
    assert derived["derived_status"] == "invalid_dependency"


def test_graph_point_is_distinct_from_interval_and_unknown(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "point.sqlite3")
    sid = ops.import_source("时点", "钥匙在此刻掉落。")
    operations = [
        {"kind": "node", "id": "key", "node_type": "item", "name": "钥匙"},
        {"kind": "predicate", "id": "falls", "label": "掉落", "semantic": "attribute", "subject_types": ["item"], "value_type": "scalar"},
        _evidence(ops, sid, "钥匙在此刻掉落", "q"),
        _claim("point", "falls", "key", value=True, validity={"kind": "point", "point": {"timeline_id": "story", "form": "number", "number": 2}}),
        _link("point", "q"),
        _claim("unknown", "falls", "key", value=True),
        _link("unknown", "q"),
    ]
    rev = ops.graph_project_info()["revision"]
    ops.graph_accept(ops.graph_propose(operations, rev), 1)
    at_two = ops.graph_query(node_id="key", story_cut={"form": "number", "number": 2})
    assert [a["id"] for a in at_two["assertions"]] == ["point"]
    assert {u["assertion_id"] for u in at_two["uncertainties"]} == {"unknown"}
    at_three = ops.graph_query(node_id="key", story_cut={"form": "number", "number": 3})
    assert at_three["assertions"] == []


def test_graph_source_order_is_explicit_and_unique(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "order.sqlite3")
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
    ops = Operations.create_graph_project(tmp_path / "contexts.sqlite3")
    sid = ops.import_source("分支", "甲说钥匙在乙手里；假设甲仍持有。")
    base = [
        {"kind": "node", "id": "key", "node_type": "item", "name": "钥匙"},
        {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
        {"kind": "node", "id": "p2", "node_type": "person", "name": "乙"},
        {"kind": "context", "id": "branch", "label": "假设分支", "context_type": "branch", "parent_id": "canon"},
        {"kind": "predicate", "id": "holder", "label": "持有", "semantic": "relation", "subject_types": ["item"], "object_types": ["person"], "value_type": "node", "functional": True},
        _evidence(ops, sid, "甲说钥匙在乙手里", "q1"),
        _evidence(ops, sid, "假设甲仍持有", "q2"),
        _claim("said", "holder", "key", object_id="p2", scope="claim", holder_id="p1"),
        _link("said", "q1"),
        _claim("branch-hold", "holder", "key", object_id="p1", context_id="branch", modality="hypothetical"),
        _link("branch-hold", "q2"),
    ]
    rev = ops.graph_project_info()["revision"]
    assert ops.graph_precheck(base, rev) == []
    ops.graph_accept(ops.graph_propose(base, rev), 1)
    canon = ops.graph_query(node_id="key")
    assert {a["id"] for a in canon["assertions"]} == {"said"}
    assert canon["assertions"][0]["scope"] == "claim"
    branch = ops.graph_query(node_id="key", context_id="branch")
    assert {a["id"] for a in branch["assertions"]} == {"said", "branch-hold"}
