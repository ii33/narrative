"""Temporal consistency is about boundaries, not event-level or container cycles."""
from test_graph_hierarchy import _edge, _setup


def _temporal(aid, left_event, left_boundary, right_event, right_boundary, **kwargs):
    return [{"kind": "assertion", "id": aid, "subject_id": left_event, "predicate_id": "before",
             "object_id": right_event,
             "temporal_left": {"timeline_id": "story", "form": "event_boundary", "event_id": left_event, "boundary": left_boundary},
             "temporal_right": {"timeline_id": "story", "form": "event_boundary", "event_id": right_event, "boundary": right_boundary},
             **kwargs},
            {"kind": "evidence_link", "id": "proof-" + aid, "assertion_id": aid, "evidence_id": "quote"}]


def _base(tmp_path):
    ops, sid, base = _setup(tmp_path)
    base.append({"kind": "predicate", "id": "before", "label": "先于", "semantic": "temporal", "temporal_relation": "before", "value_type": "node"})
    return ops, sid, base


def _codes(ops, objects):
    return {i["code"] for i in ops.graph_precheck(objects, ops.graph_project_info()["revision"]) if i["severity"] == "error"}


def test_crossing_interval_boundaries_are_not_a_cycle(tmp_path):
    ops, _, base = _base(tmp_path)
    base += _temporal("cross1", "event", "start", "event2", "end")
    base += _temporal("cross2", "event2", "start", "event", "end")
    assert not _codes(ops, base)
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    index = ops.graph_event_index()
    event = next(n for n in index["events"] if n["id"] == "event")
    assert {link["assertion_id"] for link in event["temporal_links"]} == {"cross1", "cross2"}
    assert event["parent_id"] is None and event["root_id"] == "event"


def test_true_interval_end_before_start_cycle_and_same_event_constraints(tmp_path):
    ops, _, base = _base(tmp_path)
    assert not _codes(ops, base + _temporal("duration", "event", "start", "event", "end"))
    assert "TIME_CYCLE" in _codes(ops, base + _temporal("backward", "event", "end", "event", "start"))
    pairs = _temporal("one", "event", "end", "event2", "start")
    pairs += _temporal("two", "event2", "end", "event", "start")
    assert "TIME_CYCLE" in _codes(ops, base + pairs)
    # Denial or a hypothetical relation is not an additional actual-world order.
    negative = _temporal("two", "event2", "end", "event", "start", polarity="negative")
    assert not _codes(ops, base + _temporal("one", "event", "end", "event2", "start") + negative)


def test_known_coordinates_constrain_boundaries_but_parent_time_does_not_transfer(tmp_path):
    ops, _, base = _base(tmp_path)
    next(n for n in base if n["id"] == "event")["occurrence"] = {
        "kind": "interval", "start": {"state": "known", "ref": {"timeline_id": "story", "form": "number", "number": 10}},
        "end": {"state": "known", "ref": {"timeline_id": "story", "form": "number", "number": 20}}}
    next(n for n in base if n["id"] == "event2")["occurrence"] = {
        "kind": "point", "point": {"timeline_id": "story", "form": "number", "number": 5}}
    assert "TIME_CYCLE" in _codes(ops, base + _temporal("backward", "event", "end", "event2", "start"))
    assert not _codes(ops, base + _temporal("valid", "event2", "end", "event", "start"))
    # Reading containment can join events with unrelated times without inheriting them.
    base += _edge("parent", "event", "event2")
    assert not _codes(ops, base)
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    event = next(n for n in ops.graph_timeline()["events"] if n["id"] == "event2")
    assert event["coordinate_refs"][0]["number"] == 5


def test_before_can_branch_and_converge_without_immediate_next_facts(tmp_path):
    ops, _, base = _base(tmp_path)
    base += [{"kind": "node", "id": eid, "node_type": "event", "name": eid} for eid in ("event3", "event4")]
    for index, (a, b) in enumerate([("event", "event2"), ("event", "event3"), ("event2", "event4"), ("event3", "event4")]):
        base += _temporal(f"branch{index}", a, "end", b, "start")
    assert not _codes(ops, base)
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    view = ops.graph_view({"view": "timeline"})
    assert len(view["temporal_edges"]) == 4
    assert all(e["temporal_relation"] == "before" for e in view["temporal_edges"])
    assert len(ops.graph_event_index()["roots"]) > 1


def test_incorrect_person_claim_does_not_conflict_with_world_occurrence(tmp_path):
    ops, _, base = _base(tmp_path)
    for node in base:
        if node["id"] in {"event", "event2"}:
            node["occurrence"] = {"kind": "point", "point": {"timeline_id": "story", "form": "number",
                "number": 10 if node["id"] == "event" else 5}}
    mistaken = _temporal("mistake", "event", "end", "event2", "start", scope="claim", holder_id="wolf")
    assert not _codes(ops, base + mistaken)
    ops.graph_accept(ops.graph_propose(base + mistaken, ops.graph_project_info()["revision"]), 1)
    assert not ops.graph_timeline()["temporal_edges"]


def test_retracted_or_stale_inference_is_not_a_constraint(tmp_path):
    ops, _, base = _base(tmp_path)
    base += _temporal("support", "event", "end", "event2", "start")
    base += _temporal("inferred", "event", "end", "event2", "start", origin="inference", derived_from=["support"])
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    support = next(a for a in ops.graph_snapshot()["assertions"] if a["id"] == "support")
    retraction = support | {"assessment": "retracted"}
    opposite = _temporal("opposite", "event2", "end", "event", "start")
    assert not _codes(ops, [retraction, *opposite])
    # Changing the premise makes the existing inference stale in the same
    # proposal; its old conclusion must not reject the newly supported order.
    reversed_support = support | {"subject_id": "event2", "object_id": "event",
        "temporal_left": opposite[0]["temporal_left"], "temporal_right": opposite[0]["temporal_right"]}
    assert not _codes(ops, [reversed_support])
