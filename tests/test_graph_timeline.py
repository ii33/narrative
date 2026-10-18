from test_graph_hierarchy import _edge, _setup


def test_timeline_review_cut_unknown_and_original_direction(tmp_path):
    ops, sid, base = _setup(tmp_path)
    base += [
        {"kind": "predicate", "id": "after", "label": "后于", "semantic": "temporal", "temporal_relation": "after", "value_type": "node"},
        {"kind": "assertion", "id": "later", "subject_id": "event2", "predicate_id": "after", "object_id": "event",
         "temporal_left": {"timeline_id": "story", "form": "event_boundary", "event_id": "event2", "boundary": "start"},
         "temporal_right": {"timeline_id": "story", "form": "event_boundary", "event_id": "event", "boundary": "end"},
         "disclosure": {"source_id": sid, "start": 3}},
        {"kind": "evidence_link", "id": "time-proof", "assertion_id": "later", "evidence_id": "quote"},
    ]
    # Timeline reference must exist independently of event organization.

    rev = ops.graph_project_info()["revision"]
    cid = ops.graph_propose(base, rev)
    assert ops.graph_timeline()["events"] == []
    assert ops.graph_timeline(candidate_id=cid)["pending_excluded"] is False
    ops.graph_accept(cid, 1)
    view = ops.graph_timeline()
    edge = view["temporal_edges"][0]
    assert edge["subject_id"] == "event2"
    assert edge["display_subject_id"] == "event"
    assert edge["temporal_relation"] == "after" and edge["display_relation"] == "before"
    assert edge["evidence"][0]["quote"]
    assert view["known_time"] == []
    assert {n["id"] for n in view["relative"]} == {"event", "event2"}
    assert ops.graph_timeline(disclosure_cut={"source_id": sid, "start": 0})["events"] == []
    assert ops.graph_timeline(as_of_revision=rev)["events"] == []
    query = ops.graph_query()
    assert query["edges"][0]["temporal_relation"] == "after"

def test_coordinate_interval_preserves_unknown_end_and_no_invented_death(tmp_path):
    ops, _, base = _setup(tmp_path)
    event = next(n for n in base if n["id"] == "event")
    event["occurrence"] = {"kind": "interval", "start": {"state": "known", "ref": {"timeline_id": "story", "form": "number", "number": 5}}, "end": {"state": "unknown"}}
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    view = ops.graph_timeline()
    known = view["known_time"][0]
    assert known["time_status"] == "partial"
    assert known["coordinate_refs"][0]["number"] == 5
    assert known["occurrence"]["end"] == {"state": "unknown"}
    assert [n["id"] for n in view["unknown"]] == ["event2"]
    assert view["temporal_edges"] == []

def test_timeline_does_not_treat_supersedes_as_automatic_validity_or_misconnect_boundaries(tmp_path):
    from narrative.graph_timeline import timeline

    ops, _, base = _setup(tmp_path)
    base += [{"kind": "predicate", "id": "before", "label": "早于", "semantic": "temporal", "temporal_relation": "before", "value_type": "node"},
             {"kind": "assertion", "id": "old", "subject_id": "event", "predicate_id": "before", "object_id": "event2",
              "temporal_left": {"timeline_id": "story", "form": "event_boundary", "event_id": "event", "boundary": "start"},
              "temporal_right": {"timeline_id": "story", "form": "event_boundary", "event_id": "event2", "boundary": "end"}},
             {"kind": "evidence_link", "id": "old-proof", "assertion_id": "old", "evidence_id": "quote"}]
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    snapshot = ops._store.snapshot()
    old = next(a for a in snapshot["assertions"] if a["id"] == "old")
    successor = old | {"id": "new", "scope": "claim", "holder_id": "wolf", "supersedes_id": "old"}
    snapshot["assertions"].append(successor)
    view = timeline(snapshot)
    assert [a["id"] for a in view["relations"]] == ["old"]
    assert any(u["assertion_id"] == "new" for u in view["uncertainties"])
    old["temporal_left"]["event_id"] = "event2"
    view = timeline(snapshot)
    assert view["relations"] == []
    assert any(u["assertion_id"] == "old" for u in view["uncertainties"])


def test_mixed_symbolic_interval_is_partial_and_axes_are_separate(tmp_path):
    ops, _, base = _setup(tmp_path)
    next(n for n in base if n["id"] == "event")["occurrence"] = {
        "kind": "interval", "start": {"state": "known", "ref": {"timeline_id": "story", "form": "number", "number": 3}},
        "end": {"state": "known", "ref": {"timeline_id": "story", "form": "event_boundary", "event_id": "event2", "boundary": "start"}}}
    next(n for n in base if n["id"] == "event2")["occurrence"] = {
        "kind": "point", "point": {"timeline_id": "story", "form": "datetime", "datetime": "2020-01-01T00:00:00"}}
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    view = ops.graph_timeline()
    assert next(e for e in view["events"] if e["id"] == "event")["time_status"] == "partial"
    assert {lane["form"] for lane in view["lanes"]} == {"number", "datetime"}
    assert all(len(lane["events"]) == 1 for lane in view["lanes"])

def test_relative_axes_keep_groups_direction_same_and_weak_layout():
    from narrative.graph_timeline import _relative_axes

    def ref(event, boundary):
        return {"timeline_id": "story", "form": "event_boundary", "event_id": event, "boundary": boundary}
    def edge(identity, left, right, relation="before"):
        return {"id": identity, "temporal_relation": relation, "temporal_left": left, "temporal_right": right}
    relations = [edge("hang-stop", ref("hang", "end"), ref("stop", "start")),
                 edge("stop-back", ref("stop", "start"), ref("back", "start")),
                 edge("morning-after-back", ref("morning", "start"), ref("back", "end"), "after"),
                 edge("independent", ref("x", "start"), ref("y", "end")),
                 edge("same", ref("y", "end"), ref("z", "start"), "same"),
                 edge("overlap", ref("hang", "start"), ref("x", "start"), "overlaps")]
    axes, errors = _relative_axes(relations, {})
    assert not errors and len(axes) == 2
    axis = next(a for a in axes if "hang-stop" in a["explicit_relation_ids"])
    assert set(axis["explicit_relation_ids"]) == {"hang-stop", "stop-back", "morning-after-back"}
    ranks = {(p["event_id"], p["boundary"]): p["ordinal"] for p in axis["points"]}
    assert ranks[("hang", "end")] < ranks[("stop", "start")] < ranks[("back", "start")]
    assert ranks[("back", "start")] <= ranks[("back", "end")] < ranks[("morning", "start")]
    assert axis["layout_connections"][0]["layout_only"] is True
    assert len(axis["connections"]) == 3 and axis["ordinal_is_duration"] is False
    second = next(a for a in axes if "same" in a["explicit_relation_ids"])
    same = [p for p in second["points"] if p["event_id"] in {"y", "z"}]
    assert same[0]["same_group"] == same[1]["same_group"]
    assert len(relations) == 6


def test_relative_axes_conflict_does_not_force_order():
    from narrative.graph_timeline import _relative_axes

    a = {"timeline_id": "story", "form": "event_boundary", "event_id": "a", "boundary": "start"}
    b = a | {"event_id": "b"}
    relations = [{"id": "ab", "temporal_relation": "before", "temporal_left": a, "temporal_right": b},
                 {"id": "ba", "temporal_relation": "before", "temporal_left": b, "temporal_right": a}]
    axes, errors = _relative_axes(relations, {})
    assert axes == [] and errors[0]["assertion_ids"] == ["ab", "ba"]

def test_relative_axes_never_replaces_real_coordinates_with_ordinals():
    from narrative.graph_timeline import _relative_axes

    number = {"timeline_id": "story", "form": "number", "number": 10}
    boundary = {"timeline_id": "story", "form": "event_boundary", "event_id": "e", "boundary": "start"}
    relations = [{"id": "numeric", "temporal_relation": "before", "temporal_left": number,
                  "temporal_right": number | {"number": 100}},
                 {"id": "mixed", "temporal_relation": "before", "temporal_left": number,
                  "temporal_right": boundary}]
    axes, errors = _relative_axes(relations, {})
    assert axes == [] and errors == []
    assert relations[0]["temporal_left"]["number"] == 10
    assert relations[0]["temporal_right"]["number"] == 100


def _temporal_edge(identity, subject, target, *, timeline_id="story"):
    return [
        {"kind": "assertion", "id": identity, "subject_id": subject,
         "predicate_id": "before", "object_id": target,
         "temporal_left": {"timeline_id": timeline_id, "form": "event_boundary",
                           "event_id": subject, "boundary": "end"},
         "temporal_right": {"timeline_id": timeline_id, "form": "event_boundary",
                            "event_id": target, "boundary": "start"}},
        {"kind": "evidence_link", "id": "link-" + identity,
         "assertion_id": identity, "evidence_id": "quote"},
    ]


def test_nested_axes_have_four_event_layers_through_root_containers(tmp_path):
    ops, _, base = _setup(tmp_path)
    for identity in ("event3", "event4", "event5"):
        base.append({"kind": "node", "id": identity, "node_type": "event", "name": identity})
    for identity, number in (("event", 200), ("event3", 100)):
        next(node for node in base if node["id"] == identity)["occurrence"] = {
            "kind": "point", "point": {"timeline_id": "story", "form": "number", "number": number}}
    base += (_edge("line-scene", "line", "scene") + _edge("scene-root", "scene", "event")
             + _edge("other-root", "line2", "event2") + _edge("child", "event", "event3")
             + _edge("grandchild", "event3", "event4") + _edge("great-grandchild", "event4", "event5"))
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    view = ops.graph_timeline()
    axes = {axis["id"]: axis for axis in view["nested_axes"]["layers"]}
    assert set(axes) == {"root", "event:event", "event:event3", "event:event4"}
    assert axes["root"]["event_ids"] == ["event", "event2"]
    assert axes["event:event"]["event_ids"] == ["event3"]
    assert axes["event:event3"]["event_ids"] == ["event4"]
    assert axes["event:event4"]["event_ids"] == ["event5"]
    assert axes["event:event4"]["owner_event_id"] == "event4"
    assert axes["event:event4"]["parent_axis_id"] == "event:event3"
    events = {event["id"]: event for event in view["events"]}
    assert events["event"]["parent_id"] == "scene" and events["event"]["parent_event_id"] is None
    assert events["event3"]["axis_id"] == "event:event"
    assert events["event3"]["child_axis_id"] == "event:event3"
    assert events["event5"]["child_axis_id"] is None
    assert axes["root"]["lanes"][0]["events"][0]["coordinate_refs"][0]["number"] == 200
    assert axes["event:event"]["lanes"][0]["events"][0]["coordinate_refs"][0]["number"] == 100
    assert axes["event:event4"]["unknown"][0]["id"] == "event5"
    assert all(axis["relations"] == [] and axis["relative_axes"] == [] for axis in axes.values())
    assert not view["nested_axes"]["truncated"]


def test_nested_axes_recompute_parent_at_reading_cut_and_keep_revision(tmp_path):
    ops, sid, base = _setup(tmp_path)
    base += _edge("old", "event", "event2", disclosure={"source_id": sid, "start": 0})
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    revision = ops.graph_project_info()["revision"]
    replacement = _edge("new", "line", "event2", supersedes_id="old",
                        disclosure={"source_id": sid, "start": 8})
    ops.graph_accept(ops.graph_propose(replacement, revision), 1)
    for view in (ops.graph_timeline(disclosure_cut={"source_id": sid, "start": 2}),
                 ops.graph_timeline(as_of_revision=revision)):
        axes = {axis["id"]: axis for axis in view["nested_axes"]["layers"]}
        assert axes["root"]["event_ids"] == ["event"]
        assert axes["event:event"]["event_ids"] == ["event2"]
        assert axes["event:event"]["relations"] == []
    current = ops.graph_timeline()["nested_axes"]
    assert len(current["layers"]) == 1
    assert current["layers"][0]["event_ids"] == ["event", "event2"]
    assert current["layers"][0]["relative_axes"] == []


def test_nested_axes_do_not_roll_cross_layer_edges_up_to_parents(tmp_path):
    ops, _, base = _setup(tmp_path)
    base += [{"kind": "node", "id": identity, "node_type": "event", "name": identity}
             for identity in ("child-a", "child-b")]
    base += [{"kind": "predicate", "id": "before", "label": "早于", "semantic": "temporal",
              "temporal_relation": "before", "value_type": "node"}]
    base += (_edge("a-child", "event", "child-a") + _edge("b-child", "event2", "child-b")
             + _temporal_edge("cross-children", "child-a", "child-b")
             + _temporal_edge("parent-child", "event", "child-a"))
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    view = ops.graph_timeline()
    axes = {axis["id"]: axis for axis in view["nested_axes"]["layers"]}
    assert len(view["relations"]) == 2
    assert axes["root"]["event_ids"] == ["event", "event2"]
    assert all(axis["relations"] == [] and axis["relative_axes"] == [] for axis in axes.values())
    assert axes["root"]["cross_level_relation_count"] == 1
    assert axes["event:event"]["cross_level_relation_count"] == 2
    assert axes["event:event2"]["cross_level_relations"][0]["id"] == "cross-children"
    assert {event["id"] for event in axes["root"]["unknown"]} == {"event", "event2"}


def test_nested_axes_keep_unconnected_relative_groups_and_coordinate_systems_apart(tmp_path):
    ops, _, base = _setup(tmp_path)
    base += [{"kind": "node", "id": identity, "node_type": "event", "name": identity}
             for identity in ("a", "b", "c", "d", "other-time", "unknown")]
    base += [{"kind": "timeline", "id": "elsewhere", "label": "另一个时间系统"},
             {"kind": "predicate", "id": "before", "label": "早于", "semantic": "temporal",
              "temporal_relation": "before", "value_type": "node"}]
    for identity, timeline_id, number in (("event", "story", 30), ("event2", "story", 10),
                                          ("other-time", "elsewhere", 20)):
        next(node for node in base if node["id"] == identity)["occurrence"] = {
            "kind": "point", "point": {"timeline_id": timeline_id, "form": "number", "number": number}}
    base += _temporal_edge("ab", "a", "b") + _temporal_edge("cd", "c", "d")
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    axis = ops.graph_timeline()["nested_axes"]["layers"][0]
    assert len(axis["lanes"]) == 2
    assert {lane["timeline_id"]: {event["id"] for event in lane["events"]}
            for lane in axis["lanes"]} == {"story": {"event", "event2"}, "elsewhere": {"other-time"}}
    assert len(axis["relative_axes"]) == 2
    assert {tuple(group["explicit_relation_ids"]) for group in axis["relative_axes"]} == {("ab",), ("cd",)}
    assert [event["id"] for event in axis["unknown"]] == ["unknown"]
    assert {event["id"] for event in axis["relative"]} == {"a", "b", "c", "d"}


def test_nested_limit_does_not_promote_child_when_parent_was_truncated(tmp_path):
    ops, _, base = _setup(tmp_path)
    base += _edge("child", "event2", "event")
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    view = ops.graph_timeline(limit=1)
    assert [event["id"] for event in view["events"]] == ["event"]
    assert view["nested_axes"]["layers"][0]["events"] == []
    assert view["nested_axes"]["unplaced_event_ids"] == ["event"]
    assert view["nested_axes"]["truncated"] is True
