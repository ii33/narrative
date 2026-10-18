"""Containment is reviewed reading structure, separate from temporal claims."""

from __future__ import annotations

import pytest

from narrative.operations import Operations


def _setup(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "nested.sqlite3")
    sid = ops.import_source("狼第一则", "狼来到河边。小狼看见羊。")
    evidence = ops.locate(sid, "狼来到河边")[0] | {"id": "quote"}
    base = [
        {"kind": "node", "id": "line", "node_type": "storyline", "name": "主线"},
        {"kind": "node", "id": "line2", "node_type": "storyline", "name": "支线"},
        {"kind": "node", "id": "scene", "node_type": "scene", "name": "河边"},
        {"kind": "node", "id": "event", "node_type": "event", "name": "狼来到河边"},
        {"kind": "node", "id": "event2", "node_type": "event", "name": "小狼看见羊"},
        {"kind": "node", "id": "wolf", "node_type": "person", "name": "狼"},
        {"kind": "predicate", "id": "contains", "label": "包含", "semantic": "containment", "value_type": "node"},
        evidence,
        {"kind": "source_order", "id": "order", "source_id": sid, "ordinal": 0},
    ]
    return ops, sid, base


def _edge(aid, parent, child, **more):
    return [
        {"kind": "assertion", "id": aid, "subject_id": parent, "predicate_id": "contains", "object_id": child,
         "disclosure": more.pop("disclosure", None), **more},
        {"kind": "evidence_link", "id": "link-" + aid, "assertion_id": aid, "evidence_id": "quote"},
    ]


def _codes(ops, objects):
    return {x["code"] for x in ops.graph_precheck(objects, ops.graph_project_info()["revision"])}


def test_hierarchy_preview_review_and_old_flat(tmp_path):
    ops, sid, base = _setup(tmp_path)
    assert ops.graph_schema()["contract_version"] == "graph-nesting/2"
    assert ops.graph_hierarchy()["nodes"] == []
    rev = ops.graph_project_info()["revision"]
    ops.graph_accept(ops.graph_propose(base, rev), 1)
    flat = ops.graph_hierarchy()
    assert set(flat["roots"]) == {"line", "line2", "scene", "event", "event2", "wolf"}
    pair = _edge("a1", "line", "scene", order_index=1) + _edge("a2", "scene", "event", order_index=0)
    cid = ops.graph_propose(pair, ops.graph_project_info()["revision"])
    assert ops.graph_hierarchy()["edges"] == []
    preview = ops.graph_candidate_preview(cid)["hierarchy"]
    assert preview["candidate_id"] == cid and preview["pending_excluded"] is False
    assert [x["id"] for x in preview["edges"]] == ["a1", "a2"]
    ops.graph_accept(cid, 1)
    assert ops.graph_candidate_preview(cid)["hierarchy"]["revision"] == ops.graph_project_info()["revision"]
    tree = ops.graph_hierarchy()
    assert next(n for n in tree["nodes"] if n["id"] == "event")["depth"] == 2
    assert next(n for n in tree["nodes"] if n["id"] == "wolf")["parent_id"] is None
    assert ops.graph_hierarchy(as_of_revision=rev + 1)["edges"] == []
    early = ops.graph_hierarchy(disclosure_cut={"source_id": sid, "start": 0})
    assert early["nodes"] == []


def test_stale_and_rejected_candidate_details_remain_readable(tmp_path):
    ops, _, base = _setup(tmp_path)
    cid = ops.graph_propose(base, ops.graph_project_info()["revision"])
    ops.graph_reject(cid, 1)
    preview = ops.graph_candidate_preview(cid)
    assert preview["rows"] and preview["hierarchy"]["status"] == "unavailable"
    with pytest.raises(ValueError):
        ops.graph_hierarchy(candidate_id=cid)
    stale = ops.graph_propose(base, ops.graph_project_info()["revision"])
    ops.import_source("后续", "另一段。")
    assert ops.graph_candidate_preview(stale)["hierarchy"]["status"] == "unavailable"


def test_hierarchy_constraints_and_replacement(tmp_path):
    ops, sid, base = _setup(tmp_path)
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    assert "CONTAINMENT_SELF" in _codes(ops, _edge("a", "event", "event"))
    assert "CONTAINMENT_DOMAIN" in _codes(ops, _edge("a", "wolf", "scene"))
    assert "CONTAINMENT_CYCLE" in _codes(ops, _edge("a", "event", "event2") + _edge("b", "event2", "event"))
    assert "CONTAINMENT_PARENT" in _codes(ops, _edge("a", "line", "event") + _edge("b", "scene", "event"))
    assert "CONTAINMENT_DUPLICATE" in _codes(ops, _edge("a", "line", "scene") + _edge("b", "line", "scene"))
    assert "REF" in _codes(ops, _edge("a", "line", "scene", origin="inference", derived_from=["missing"]))
    assert "CONTAINMENT_STATUS" in _codes(ops, _edge("a", "line", "scene", origin="creative"))
    assert "CONTAINMENT_EVIDENCE" in _codes(ops, [{"kind": "assertion", "id": "a", "subject_id": "line", "predicate_id": "contains", "object_id": "scene"},
                                                {"kind": "evidence_link", "id": "refute", "assertion_id": "a", "evidence_id": "quote", "role": "refutes"}])
    assert "FORMAT" in _codes(ops, _edge("a", "line", "scene", order_index=-1))
    ops.graph_accept(ops.graph_propose(_edge("old", "line", "scene", disclosure={"source_id": sid, "start": 0}), ops.graph_project_info()["revision"]), 1)
    assert "CONTAINMENT_SUPERSEDES" in _codes(ops, _edge("wrong", "line", "event", supersedes_id="old"))
    replacement = _edge("new", "line2", "scene", supersedes_id="old", disclosure={"source_id": sid, "start": 8})
    assert _codes(ops, replacement) == set()
    ops.graph_accept(ops.graph_propose(replacement, ops.graph_project_info()["revision"]), 1)
    assert [a["id"] for a in ops.graph_hierarchy()["edges"]] == ["new"]
    revised = [{"kind": "assertion", "id": "new", "subject_id": "line2", "predicate_id": "contains", "object_id": "scene", "assessment": "retracted", "supersedes_id": "old", "disclosure": {"source_id": sid, "start": 8}}]
    assert _codes(ops, revised) == set()
    ops.graph_accept(ops.graph_propose(revised, ops.graph_project_info()["revision"], source="correction"), 1)
    assert ops.graph_hierarchy()["edges"] == []
    assert ops.graph_hierarchy()["uncertainties"]


def test_siblings_follow_reading_order_not_story_time(tmp_path):
    ops, _, base = _setup(tmp_path)
    batch = base + _edge("later_id", "line", "event", order_index=2) + _edge("earlier_id", "line", "scene", order_index=1)
    ops.graph_accept(ops.graph_propose(batch, ops.graph_project_info()["revision"]), 1)
    tree = ops.graph_hierarchy()
    assert next(n for n in tree["nodes"] if n["id"] == "line")["child_ids"] == ["scene", "event"]
    assert [edge["id"] for edge in tree["edges"]] == ["earlier_id", "later_id"]


def test_inference_edge_loses_projection_when_dependency_changes(tmp_path):
    ops, _, base = _setup(tmp_path)
    base += [
        {"kind": "predicate", "id": "seen", "label": "见到", "semantic": "attribute", "subject_types": ["event"], "value_type": "scalar"},
        {"kind": "assertion", "id": "premise", "subject_id": "event", "predicate_id": "seen", "value": True},
        {"kind": "evidence_link", "id": "link-premise", "assertion_id": "premise", "evidence_id": "quote"},
        {"kind": "assertion", "id": "bridge", "subject_id": "event2", "predicate_id": "seen", "value": True,
         "origin": "inference", "derived_from": ["premise"]},
        {"kind": "evidence_link", "id": "link-bridge", "assertion_id": "bridge", "evidence_id": "quote"},
    ]
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    inferred = _edge("inferred", "line", "scene", origin="inference", derived_from=["bridge"])
    assert _codes(ops, inferred) == set()
    ops.graph_accept(ops.graph_propose(inferred, ops.graph_project_info()["revision"]), 1)
    assert [edge["id"] for edge in ops.graph_hierarchy()["edges"]] == ["inferred"]
    correction = [{"kind": "assertion", "id": "premise", "subject_id": "event", "predicate_id": "seen", "value": False}]
    ops.graph_accept(ops.graph_propose(correction, ops.graph_project_info()["revision"], source="correction"), 1)
    assert ops.graph_hierarchy()["edges"] == []  # stale transitive dependency
    retract = [{"kind": "assertion", "id": "premise", "subject_id": "event", "predicate_id": "seen", "value": False, "assessment": "retracted"}]
    ops.graph_accept(ops.graph_propose(retract, ops.graph_project_info()["revision"], source="correction"), 1)
    assert ops.graph_hierarchy()["edges"] == []  # invalid transitive dependency
    assert "CONTAINMENT_DEPENDENCY" in _codes(ops, _edge("another", "line", "event", origin="inference", derived_from=["bridge"]))


def test_story_cut_uses_full_temporal_context(tmp_path):
    ops, _, base = _setup(tmp_path)
    boundary = {"timeline_id": "story", "form": "event_boundary", "event_id": "event", "boundary": "end"}
    point = {"timeline_id": "story", "form": "number", "number": 2}
    base += [
        {"kind": "predicate", "id": "same_time", "label": "同时", "semantic": "temporal", "temporal_relation": "same", "value_type": "node"},
        {"kind": "assertion", "id": "time", "subject_id": "event", "predicate_id": "same_time", "object_id": "event2", "temporal_left": boundary, "temporal_right": point},
        {"kind": "evidence_link", "id": "link-time", "assertion_id": "time", "evidence_id": "quote"},
        *_edge("nested", "line", "scene", validity={"start": {"state": "known", "ref": boundary}, "end": {"state": "unbounded"}}),
    ]
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    cut = {"form": "number", "number": 2}
    assert "nested" in {a["id"] for a in ops.graph_query(story_cut=cut)["assertions"]}
    assert [a["id"] for a in ops.graph_hierarchy(story_cut=cut)["edges"]] == ["nested"]


def test_limit_never_turns_truncated_children_into_roots(tmp_path):
    ops, _, base = _setup(tmp_path)
    base += _edge("seed", "line", "scene")
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    snapshot = ops.graph_snapshot()
    template = next(a for a in snapshot["assertions"] if a["id"] == "seed")
    for index in range(2001):
        child = f"child-{index:04}"
        aid = f"edge-{index:04}"
        node = {"kind": "node", "id": child, "node_type": "event", "name": child, "context_id": "canon"}
        assertion = template | {"id": aid, "object_id": child}
        snapshot["nodes"].append(node)
        snapshot["objects"].extend([node, assertion])
        snapshot["assertions"].append(assertion)
        snapshot["evidence_links"].append({"kind": "evidence_link", "id": f"link-{aid}", "assertion_id": aid, "evidence_id": "quote", "role": "supports"})
    from narrative.graph_hierarchy import hierarchy

    tree = hierarchy(snapshot, limit=2)
    assert tree["truncated"] is True
    assert "child-2000" not in tree["roots"]
    assert tree["roots"] == ["line"]


def test_evolving_parent_preserves_reading_cut_revision_and_tip(tmp_path):
    ops, sid, base = _setup(tmp_path)
    old = _edge("old", "event", "event2", disclosure={"source_id": sid, "start": 0})
    ops.graph_accept(ops.graph_propose(base + old, ops.graph_project_info()["revision"]), 1)
    revision = ops.graph_project_info()["revision"]
    new = _edge("new", "line", "event2", supersedes_id="old", disclosure={"source_id": sid, "start": 8})
    ops.graph_accept(ops.graph_propose(new, revision), 1)
    assert [a["id"] for a in ops.graph_hierarchy(disclosure_cut={"source_id": sid, "start": 2})["edges"]] == ["old"]
    assert [a["id"] for a in ops.graph_hierarchy(as_of_revision=revision)["edges"]] == ["old"]
    index = ops.graph_event_index(focus_id="event2")
    child = next(e for e in index["events"] if e["id"] == "event2")
    assert child["parent_edge_id"] == child["active_parent_edge_id"] == child["chain_tip_id"] == "new"
    assert child["relative_depth"] == 0 and len(child["history"]) == 2
    assert "CONTAINMENT_BRANCH" in _codes(ops, _edge("fork", "line2", "event2", supersedes_id="old", disclosure={"source_id": sid, "start": 9}))
    assert "CONTAINMENT_DISCLOSURE" in _codes(ops, _edge("back", "line2", "event2", supersedes_id="new", disclosure={"source_id": sid, "start": 1}))
    assert "CONTAINMENT_IMMUTABLE" in _codes(ops, _edge("old", "line2", "event2", disclosure={"source_id": sid, "start": 0}))


def test_conflict_at_earlier_reading_boundary_is_rejected(tmp_path):
    ops, sid, base = _setup(tmp_path)
    batch = (base + _edge("first", "line", "event", disclosure={"source_id": sid, "start": 0})
             + _edge("second", "scene", "event", disclosure={"source_id": sid, "start": 2})
             + _edge("latest", "line2", "event", supersedes_id="first", disclosure={"source_id": sid, "start": 8})
             + _edge("second-end", "scene", "event", supersedes_id="second", assessment="retracted", disclosure={"source_id": sid, "start": 8}))
    assert "CONTAINMENT_PARENT" in _codes(ops, batch)


def test_reorganization_preserves_identity_but_plain_correction_remains_allowed(tmp_path):
    ops, _, base = _setup(tmp_path)
    ops.graph_accept(ops.graph_propose(base, ops.graph_project_info()["revision"]), 1)
    event = next(n for n in ops.graph_snapshot()["nodes"] if n["id"] == "event")
    correction = event | {"name": "修正名称"}
    assert not _codes(ops, [correction])
    assert "REORGANIZATION_IDENTITY" in _codes(ops, [correction] + _edge("organize", "line", "event"))


def test_legacy_unknown_parent_can_be_revised_without_inventing_early_disclosure(tmp_path):
    ops, sid, base = _setup(tmp_path)
    ops.graph_accept(ops.graph_propose(base + _edge("legacy", "line", "event"), ops.graph_project_info()["revision"]), 1)
    replacement = _edge("known", "scene", "event", supersedes_id="legacy", disclosure={"source_id": sid, "start": 8})
    diagnostics = ops.graph_precheck(replacement, ops.graph_project_info()["revision"])
    assert not [d for d in diagnostics if d["severity"] == "error"]
    assert "CONTAINMENT_LEGACY_DISCLOSURE" in {d["code"] for d in diagnostics}
    ops.graph_accept(ops.graph_propose(replacement, ops.graph_project_info()["revision"]), 1)
    assert ops.graph_hierarchy(disclosure_cut={"source_id": sid, "start": 2})["edges"] == []
    assert [a["id"] for a in ops.graph_hierarchy()["edges"]] == ["known"]
    assert "CONTAINMENT_DISCLOSURE" in _codes(ops, _edge("unknown", "line2", "event", supersedes_id="known"))


def test_unchanged_legacy_revision_chain_does_not_block_unrelated_candidate(tmp_path):
    ops, _, base = _setup(tmp_path)
    # Reproduce a /1 store through the old contract checker, without changing a real database.
    service = ops._service
    original = service._check_hierarchy
    service._check_hierarchy = lambda *args: None
    try:
        objects = base + _edge("legacy-old", "line", "event") + _edge("legacy-new", "scene", "event", supersedes_id="legacy-old")
        ops.graph_accept(ops.graph_propose(objects, ops.graph_project_info()["revision"]), 1)
    finally:
        service._check_hierarchy = original
    unrelated = [{"kind": "node", "id": "unrelated", "name": "旁支", "node_type": "event"}]
    diagnostics = ops.graph_precheck(unrelated, ops.graph_project_info()["revision"])
    assert not [d for d in diagnostics if d["severity"] == "error"]
    assert "CONTAINMENT_LEGACY_DISCLOSURE" in {d["code"] for d in diagnostics}
    ops.graph_accept(ops.graph_propose(unrelated, ops.graph_project_info()["revision"]), 1)


def test_current_query_and_neighborhood_keep_only_active_parent_edges(tmp_path):
    ops, sid, base = _setup(tmp_path)
    old = _edge("old", "line", "event", disclosure={"source_id": sid, "start": 0})
    ops.graph_accept(ops.graph_propose(base + old, ops.graph_project_info()["revision"]), 1)
    new = _edge("new", "scene", "event", supersedes_id="old", disclosure={"source_id": sid, "start": 8})
    ops.graph_accept(ops.graph_propose(new, ops.graph_project_info()["revision"]), 1)
    query = ops.graph_query()
    assert {a["id"]: a["organization_status"] for a in query["assertions"]} == {"old": "superseded", "new": "active"}
    assert [e["assertion_id"] for e in query["edges"]] == ["new"]
    # Filtering/limiting away the successor must not revive its predecessor.
    assert ops.graph_query(node_id="line", limit=1)["edges"] == []
    assert [e["assertion_id"] for e in ops.graph_neighborhood(center_id="event")["edges"]] == ["new"]
    early = {"source_id": sid, "start": 2}
    assert [e["assertion_id"] for e in ops.graph_query(disclosure_cut=early)["edges"]] == ["old"]
    assert [e["assertion_id"] for e in ops.graph_neighborhood(center_id="event", disclosure_cut=early)["edges"]] == ["old"]
