"""Typed, read-only structural queries over the reviewed graph's existing truth."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Literal

from pydantic import Field

from .graph_hierarchy import hierarchy
from .graph_models import DisclosureRef, GraphModel, NodeType, TimeRef
from .graph_query import query_graph
from .graph_timeline import _axis_projection, _nested_axes, timeline

QUERY_SCHEMA_VERSION = "graph-view/1"


class GraphViewSpec(GraphModel):
    view: Literal["graph", "timeline", "table", "forest"] = "graph"
    focus_id: str | None = Field(default=None, min_length=1)
    candidate_id: str | None = Field(default=None, min_length=1)
    depth: int = Field(default=2, ge=1, le=3, strict=True)
    direction: Literal["in", "out", "both"] = "both"
    node_types: list[NodeType] | None = None
    relation_semantics: list[Literal["attribute", "relation", "participation", "temporal", "containment"]] | None = None
    predicate_ids: list[str] | None = None
    context_id: str = Field(default="canon", min_length=1)
    story_cut: TimeRef | None = None
    disclosure_cut: DisclosureRef | None = None
    as_of_revision: int | None = Field(default=None, ge=0, strict=True)
    limit: int = Field(default=200, ge=1, le=2000, strict=True)
    offset: int = Field(default=0, ge=0, strict=True)
    group_by: Literal["node_type", "predicate", "subject"] = "node_type"


def normalize_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("结构查询须为 JSON 对象")
    return GraphViewSpec.model_validate(spec).model_dump(mode="json")


def _allowed(assertion: dict, spec: dict) -> bool:
    return ((spec["relation_semantics"] is None or assertion["semantic"] in spec["relation_semantics"])
            and (spec["predicate_ids"] is None or assertion["predicate_id"] in spec["predicate_ids"]))


def _routes(nodes: dict, edges: list[dict], spec: dict) -> dict[str, list[dict]]:
    focus = spec["focus_id"]
    if focus not in nodes:
        return {}
    adjacent: dict[str, list[tuple[str, dict, str]]] = defaultdict(list)
    for edge in sorted(edges, key=lambda e: e["assertion_id"]):
        a, b = edge["subject_id"], edge["object_id"]
        if a not in nodes or b not in nodes:
            continue
        if spec["direction"] in {"out", "both"}:
            adjacent[a].append((b, edge, "forward"))
        if spec["direction"] in {"in", "both"}:
            adjacent[b].append((a, edge, "reverse"))
    found: dict[str, list[dict]] = {focus: []}
    queue = deque([focus])
    while queue:
        current = queue.popleft()
        route = found[current]
        if len(route) >= spec["depth"]:
            continue
        for target, edge, traversal in adjacent[current]:
            if target not in found:
                found[target] = [*route, {"from_id": current, "to_id": target,
                                        "traversal": traversal, "edge": edge}]
                queue.append(target)
    return found


def _paths(found: dict, ids: list[str], focus: str | None) -> list[dict]:
    return [{"from_id": focus, "to_id": node_id,
             "via_node_ids": [step["to_id"] for step in found[node_id][:-1]],
             "assertion_ids": [step["edge"]["assertion_id"] for step in found[node_id]],
             "steps": found[node_id], "length": len(found[node_id]),
             "direct": len(found[node_id]) == 1, "derived": len(found[node_id]) > 1,
             "contains_claim_or_inference": any(step["edge"]["scope"] != "world"
                 or step["edge"]["origin"] != "original" or step["edge"]["derived_status"] != "valid"
                 for step in found[node_id])}
            for node_id in ids if node_id != focus and node_id in found]


def _ancestors(selected: set[str], structure: dict) -> set[str]:
    result = set(selected)
    for node_id in selected:
        current = structure.get(node_id, {})
        seen = {node_id}
        while (parent := current.get("parent_id")) and parent not in seen:
            seen.add(parent)
            if parent not in structure:
                break
            result.add(parent)
            current = structure[parent]
    return result


def _table(nodes: list[dict], assertions: list[dict], edges: list[dict], group_by: str) -> dict:
    attrs: dict[str, list[dict]] = defaultdict(list)
    for assertion in assertions:
        if (assertion["semantic"] == "attribute" and assertion["scope"] == "world"
                and assertion["polarity"] == "positive" and assertion["modality"] == "actual"
                and assertion["assessment"] != "retracted" and assertion["derived_status"] == "valid"):
            attrs[assertion["subject_id"]].append(assertion | {"assertion_id": assertion["id"],
                                                            "label": assertion["predicate_label"]})
    rows: list[dict] = []
    for node in nodes:
        incident = [a for a in assertions if node["id"] in {a["subject_id"], a.get("object_id")}]
        rows.append(node | {"attributes": attrs[node["id"]],
                           "relations": [edge for edge in edges if node["id"] in {edge["subject_id"], edge["object_id"]}],
                           "evidence_count": len({e["id"] for a in incident for e in a["evidence"]})})
    grouped: dict[str, list[str]] = defaultdict(list)
    labels: dict[str, str] = {}
    for row in rows:
        if group_by == "predicate":
            for attribute in row["attributes"]:
                key = attribute["predicate_id"]
                if row["id"] not in grouped[key]:
                    grouped[key].append(row["id"])
                labels[key] = attribute["label"]
        else:
            key = row["node_type"] if group_by == "node_type" else row["id"]
            grouped[key].append(row["id"])
            labels[key] = key if group_by == "node_type" else row["name"]
    return {"rows": rows, "groups": [{"key": key, "label": labels[key], "node_ids": ids,
                                        "count": len(ids)} for key, ids in sorted(grouped.items())]}


def graph_view(snapshot: dict, spec: dict) -> dict:
    """One reviewed snapshot, four projections, no SQL/eval or secondary truth store."""
    args = {key: spec[key] for key in ("context_id", "story_cut", "disclosure_cut")}
    visible = query_graph(snapshot, **args, limit=None)
    nodes = {n["id"]: n for n in visible["nodes"]}
    assertions = [a for a in visible["assertions"] if _allowed(a, spec)]
    allowed_edges = [e for e in visible["edges"] if _allowed(e, spec)]
    found = _routes(nodes, allowed_edges, spec)
    available = list(found) if spec["focus_id"] is not None else sorted(nodes)
    if spec["focus_id"] is None and (spec["relation_semantics"] is not None or spec["predicate_ids"] is not None):
        incident = {nid for a in assertions for nid in (a["subject_id"], a.get("object_id")) if nid in nodes}
        available = [nid for nid in available if nid in incident]
    if spec["relation_semantics"] == [] or spec["predicate_ids"] == []:
        available = []
    matching = [nid for nid in available if spec["node_types"] is None
                or nodes[nid]["node_type"] in spec["node_types"]]
    focus = spec["focus_id"]
    endpoint_ids = [nid for nid in matching if nid != focus]
    page = endpoint_ids[spec["offset"]:spec["offset"] + spec["limit"]]
    # The retained focus consumes no endpoint page slot and is explicitly marked.
    selected: list[str] = ([focus] if focus is not None and focus in nodes else []) + page
    selected_set = set(selected)
    output_nodes = [nodes[nid] | {"focus_only": nid == focus and nid not in matching,
                                "path_only": False, "structural_only": False} for nid in selected]
    edges = [e for e in allowed_edges if e["subject_id"] in selected_set and e["object_id"] in selected_set]
    result = {"query_schema_version": QUERY_SCHEMA_VERSION, "query": spec,
              "view": spec["view"], "revision": snapshot["revision"],
              "context_id": spec["context_id"], "context_lineage": visible["context_lineage"],
              "story_cut": visible["story_cut"], "disclosure_cut": spec["disclosure_cut"],
              "nodes": output_nodes, "edges": edges,
              "assertions": [a for a in assertions if a["subject_id"] in selected_set
                             and (not a.get("object_id") or a["object_id"] in selected_set)],
              "paths": _paths(found, selected, focus),
              "uncertainties": visible["uncertainties"],
              "truncated": len(endpoint_ids) > spec["offset"] + spec["limit"],
              "total_matching": len(endpoint_ids), "pending_excluded": True,
              "focus": {"id": focus, "retained": focus in nodes,
                        "matches_filter": focus in matching,
                        "status": "none" if focus is None else "visible" if focus in nodes else "not_visible"},
              "note": "筛选仅影响投影；多跳路径是直接断言的遍历，不是新断言。"}
    if spec["view"] == "table":
        result.update(_table(output_nodes, result["assertions"], edges, spec["group_by"]))
    if spec["view"] not in {"forest", "timeline"}:
        return result
    tree = hierarchy(snapshot, **args, limit=2000, _complete=True)
    structure = {n["id"]: n for n in tree["nodes"]}
    empty_filter = any(spec[key] == [] for key in ("node_types", "relation_semantics", "predicate_ids"))
    expanded = selected_set if empty_filter else _ancestors(selected_set, structure)
    structural_truncated = len(expanded) > max(2000, len(selected_set))
    if structural_truncated:
        expanded = selected_set | set(sorted(expanded - selected_set)[:max(0, 2000 - len(selected_set))])
    structural = expanded - selected_set
    organization = [e | {"assertion_id": e["id"], "direct": True,
                          "structural_only": not _allowed(e | {"semantic": "containment"}, spec)}
                    for e in tree["edges"] if e["subject_id"] in expanded and e["object_id"] in expanded]
    result["organization_edges"] = organization
    result["truncated"] = result["truncated"] or structural_truncated
    if spec["view"] == "forest":
        forest_nodes = []
        for node in tree["nodes"]:
            if node["id"] not in expanded:
                continue
            root = node
            seen = set()
            while root.get("parent_id") in structure and root["id"] not in seen:
                seen.add(root["id"])
                root = structure[root["parent_id"]]
            forest_nodes.append(node | {"root_id": root["id"],
                "child_ids": [nid for nid in node["child_ids"] if nid in expanded],
                "structural_only": node["id"] in structural,
                "focus_only": node["id"] == focus and node["id"] not in matching})
        result.update(nodes=forest_nodes, roots=[nid for nid in tree["roots"] if nid in expanded])
        return result
    full = timeline(snapshot, **args, limit=2000, _event_ids=expanded, _tree=tree)
    events = [e | {"structural_only": e["id"] in structural,
                    "focus_only": e["id"] == focus and e["id"] not in matching}
              for e in full["events"] if e["id"] in expanded]
    event_ids = {e["id"] for e in events}
    relations = [e for e in full["relations"] if _allowed(e, spec)
                 and e["subject_id"] in event_ids and e["object_id"] in event_ids]
    labels = {t["id"]: t["label"] for t in snapshot["timelines"]
              if t.get("context_id", "canon") in visible["context_lineage"]}
    projection = _axis_projection(events, relations, labels)
    nested = _nested_axes(events, relations, structure, labels, hierarchy_truncated=tree["truncated"])
    result.update({key: value for key, value in projection.items() if key != "uncertainties"})
    result.update(nodes=events + [node for node in output_nodes if node["node_type"] != "event"],
                  nested_axes=nested, temporal_edges=relations)
    result["uncertainties"] += projection["uncertainties"]
    result["truncated"] = result["truncated"] or full["truncated"]
    return result
