"""Structural queries preserve direct evidence, slices and independent roots."""
import json
import sys

import pytest
from test_graph_hierarchy import _edge, _setup

from narrative.cli import main


def _reviewed(tmp_path):
    ops, sid, base = _setup(tmp_path)
    base += [
        {"kind": "node", "id": "item", "node_type": "item", "name": "物件"},
        {"kind": "predicate", "id": "participates", "label": "参与", "semantic": "participation", "value_type": "node"},
        {"kind": "predicate", "id": "owns", "label": "持有", "semantic": "relation", "value_type": "node"},
        {"kind": "predicate", "id": "color", "label": "颜色", "semantic": "attribute", "value_type": "scalar"},
    ]
    base += _edge("parent", "line", "event") + _edge("child", "event", "event2")
    for aid, subject, predicate, target in [("participant", "event2", "participates", "wolf"),
                                             ("ownership", "wolf", "owns", "item")]:
        base += [{"kind": "assertion", "id": aid, "subject_id": subject, "predicate_id": predicate,
                  "object_id": target, "qualifiers": {"role": "参与者"},
                  "disclosure": {"source_id": sid, "start": 1}},
                 {"kind": "evidence_link", "id": "proof-" + aid, "assertion_id": aid, "evidence_id": "quote"}]
    base += [{"kind": "assertion", "id": "red", "subject_id": "item", "predicate_id": "color", "value": "红色"},
             {"kind": "evidence_link", "id": "proof-red", "assertion_id": "red", "evidence_id": "quote"}]
    revision = ops.graph_project_info()["revision"]
    candidate = ops.graph_propose(base, revision)
    ops.graph_accept(candidate, 1)
    return ops, sid, revision


def test_any_node_focus_direction_type_endpoints_and_forbidden_paths(tmp_path):
    ops, _, _ = _reviewed(tmp_path)
    query = {"focus_id": "item", "node_types": ["event"], "depth": 3}
    view = ops.graph_view(query)
    assert {n["id"] for n in view["nodes"]} == {"item", "event", "event2"}
    assert next(n for n in view["nodes"] if n["id"] == "item")["focus_only"]
    route = next(p for p in view["paths"] if p["to_id"] == "event2")
    assert route["via_node_ids"] == ["wolf"]
    assert route["assertion_ids"] == ["ownership", "participant"] and route["derived"]
    assert all(e["subject_id"] != "item" for e in view["edges"])
    assert ops.graph_view(query | {"direction": "out"})["paths"] == []
    assert ops.graph_view(query | {"relation_semantics": ["participation"]})["paths"] == []
    assert ops.graph_view(query | {"predicate_ids": ["participates"]})["paths"] == []
    assert ops.graph_view(query | {"node_types": []})["paths"] == []


@pytest.mark.parametrize("empty", ["node_types", "relation_semantics", "predicate_ids"])
def test_empty_filter_is_empty_and_focus_explicit(tmp_path, empty):
    ops, _, _ = _reviewed(tmp_path)
    assert ops.graph_view({empty: []})["nodes"] == []
    focused = ops.graph_view({empty: [], "focus_id": "wolf"})
    assert [n["id"] for n in focused["nodes"]] == ["wolf"]
    assert focused["nodes"][0]["focus_only"] is True
    assert not focused["edges"] and not focused["paths"]


def test_forest_and_timeline_preserve_filtered_event_ancestors(tmp_path):
    ops, _, _ = _reviewed(tmp_path)
    query = {"focus_id": "wolf", "depth": 1, "node_types": ["event"],
             "relation_semantics": ["participation"]}
    forest = ops.graph_view(query | {"view": "forest"})
    by_id = {n["id"]: n for n in forest["nodes"]}
    assert by_id["event2"]["parent_id"] == "event"
    assert by_id["event"]["structural_only"]
    assert by_id["event2"]["root_id"] == "line"
    assert "event2" not in forest["roots"] and "line" in forest["roots"]
    assert all(e["structural_only"] for e in forest["organization_edges"])
    timeline = ops.graph_view(query | {"view": "timeline"})
    events = {n["id"]: n for n in timeline["events"]}
    assert events["event2"]["parent_event_id"] == "event"
    assert events["event"]["structural_only"]
    assert events["event2"]["axis_id"] == "event:event"
    assert not timeline["temporal_edges"]
    assert len(ops.graph_view({"view": "forest"})["roots"]) > 1


def test_table_same_query_nodes_explicit_attributes_and_page(tmp_path):
    ops, _, _ = _reviewed(tmp_path)
    query = {"focus_id": "wolf", "node_types": ["item"], "group_by": "predicate"}
    graph = ops.graph_view(query)
    table = ops.graph_view(query | {"view": "table"})
    assert [n["id"] for n in graph["nodes"]] == [n["id"] for n in table["rows"]]
    assert table["groups"] == [{"key": "color", "label": "颜色", "node_ids": ["item"], "count": 1}]
    assert next(n for n in table["rows"] if n["id"] == "item")["attributes"][0]["assertion_id"] == "red"
    first = ops.graph_view({"limit": 1})
    second = ops.graph_view({"limit": 1, "offset": 1})
    assert first["truncated"] and first["nodes"][0]["id"] != second["nodes"][0]["id"]
    assert first["query"]["direction"] == "both"
    assert first["query_schema_version"] == "graph-view/1"


def test_disclosure_revision_and_context_do_not_leak_focus(tmp_path):
    ops, sid, old_revision = _reviewed(tmp_path)
    assert ops.graph_view({"focus_id": "item", "as_of_revision": old_revision})["nodes"] == []
    assert ops.graph_view({"focus_id": "wolf", "disclosure_cut": {"source_id": sid, "start": 0}})["nodes"] == []
    private = [{"kind": "context", "id": "private", "label": "假设", "context_type": "hypothesis", "parent_id": "canon"},
               {"kind": "node", "id": "secret", "node_type": "person", "name": "秘密", "context_id": "private"}]
    ops.graph_accept(ops.graph_propose(private, ops.graph_project_info()["revision"]), 1)
    assert ops.graph_view({"focus_id": "secret"})["focus"]["status"] == "not_visible"
    assert ops.graph_view({"focus_id": "secret", "context_id": "private"})["focus"]["retained"]


@pytest.mark.parametrize("spec", [{"depth": True}, {"depth": 4}, {"limit": 0}, {"offset": -1},
                                  {"node_types": ["mage"]}, {"relation_semantics": ["magic"]},
                                  {"direction": "sideways"}, {"sql": "SELECT *"}, {"limit": "3"}])
def test_typed_query_rejects_invalid_inputs(tmp_path, spec):
    ops, _, _ = _reviewed(tmp_path)
    with pytest.raises(ValueError):
        ops.graph_view(spec)


def test_cli_structured_query_uses_operations(tmp_path, monkeypatch, capsys):
    ops, _, _ = _reviewed(tmp_path)
    spec_path = tmp_path / "query.json"
    spec_path.write_text(json.dumps({"view": "table", "node_types": ["item"]}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["narrative", "--db", str(ops.project_info().path), "graph-view", "--spec-file", str(spec_path)])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["rows"][0]["id"] == "item" and result["revision"] == ops.graph_project_info()["revision"]


def test_query_contract_discoverable_and_candidate_revision_not_overridden(tmp_path):
    ops, _, _ = _reviewed(tmp_path)
    schema = ops.graph_schema()
    assert schema["query_schema_version"] == "graph-view/1"
    assert schema["query_schema"]["properties"]["direction"]["enum"] == ["in", "out", "both"]
    revision = ops.graph_project_info()["revision"]
    objects = [{"kind": "node", "id": "pending", "node_type": "event", "name": "待审事件"}]
    cid = ops.graph_propose(objects, revision)
    assert not ops.graph_view({"focus_id": "pending"})["nodes"]
    assert ops.graph_view({"focus_id": "pending", "candidate_id": cid})["pending_excluded"] is False
    accepted_revision = ops.graph_accept(cid, 1)
    assert ops.graph_view({"candidate_id": cid})["revision"] == accepted_revision
    with pytest.raises(ValueError, match="修订不一致"):
        ops.graph_view({"candidate_id": cid, "as_of_revision": revision})
    assert "pending" not in ops.graph_display_map(as_of_revision=revision)["items"]


def test_event_index_focus_before_limit_and_excludes_other_roots(tmp_path):
    ops, _, _ = _reviewed(tmp_path)
    focused = ops.graph_event_index(focus_id="event2", limit=1)
    assert [n["id"] for n in focused["events"]] == ["event2"]
    assert focused["events"][0]["relative_depth"] == 0
    assert focused["events"][0]["parent_id"] == "event"
    assert focused["truncated"]
    local = ops.graph_event_index(focus_id="event2")
    assert {n["id"] for n in local["events"]} == {"event", "event2"}


def test_late_focus_is_selected_before_global_projection_bound(tmp_path):
    ops, _, _ = _reviewed(tmp_path)
    extras = [{"kind": "node", "id": f"earlier-{index:04d}", "node_type": "event", "name": f"早期{index}"}
              for index in range(2001)]
    extras.append({"kind": "node", "id": "zz-focus", "node_type": "event", "name": "末位焦点"})
    ops.graph_accept(ops.graph_propose(extras, ops.graph_project_info()["revision"]), 1)
    for view in ("forest", "timeline"):
        result = ops.graph_view({"view": view, "focus_id": "zz-focus", "limit": 1})
        assert [node["id"] for node in result["nodes"]] == ["zz-focus"]
    assert ops.graph_event_index(focus_id="zz-focus", limit=1)["events"][0]["id"] == "zz-focus"
