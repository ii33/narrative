"""Typed, read-only projections over the reviewed graph.

Without an assertion layer there are no edges, so a view is a filtered, grouped
and paged list of nodes. Paths, forests and relation graphs have no data source
and are not offered.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import Field

from .graph_models import DisclosureRef, GraphModel, NodeType
from .graph_query import query_graph

QUERY_SCHEMA_VERSION = "graph-view/2"


class GraphViewSpec(GraphModel):
    view: Literal["table"] = "table"
    focus_id: str | None = Field(default=None, min_length=1)
    node_types: list[NodeType] | None = None
    context_id: str = Field(default="canon", min_length=1)
    disclosure_cut: DisclosureRef | None = None
    as_of_revision: int | None = Field(default=None, ge=0, strict=True)
    limit: int = Field(default=200, ge=1, le=2000, strict=True)
    offset: int = Field(default=0, ge=0, strict=True)
    group_by: Literal["node_type"] = "node_type"


def normalize_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("结构查询须为 JSON 对象")
    return GraphViewSpec.model_validate(spec).model_dump(mode="json")


def _table(nodes: list[dict], group_by: str) -> dict:
    groups: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        key = node.get(group_by) or "other"
        groups[str(key)].append(node["id"])
    return {
        "table": {
            "columns": ["id", "name", "node_type", "aliases"],
            "rows": [
                {"id": node["id"], "name": node.get("name"),
                 "node_type": node.get("node_type"), "aliases": node.get("aliases") or []}
                for node in nodes
            ],
            "groups": dict(groups),
        }
    }


def graph_view(snapshot: dict, spec: dict) -> dict:
    """One reviewed snapshot, one projection, no SQL and no second truth store."""
    args = {"context_id": spec["context_id"], "disclosure_cut": spec["disclosure_cut"]}
    visible = query_graph(snapshot, **args, limit=None)
    nodes = visible["nodes"]

    matching = [
        node for node in nodes
        if spec["node_types"] is None or node["node_type"] in spec["node_types"]
    ]
    focus = spec["focus_id"]
    endpoints = [node["id"] for node in matching if node["id"] != focus]
    page = set(endpoints[spec["offset"]:spec["offset"] + spec["limit"]])
    selected = ([focus] if focus is not None and any(n["id"] == focus for n in nodes) else [])
    selected += [node["id"] for node in matching if node["id"] in page]
    chosen = [node for node in nodes if node["id"] in set(selected)]

    result = {
        "query_schema_version": QUERY_SCHEMA_VERSION,
        "query": spec,
        "view": spec["view"],
        "revision": snapshot["revision"],
        "context_id": spec["context_id"],
        "context_lineage": visible["context_lineage"],
        "disclosure_cut": spec["disclosure_cut"],
        "nodes": chosen,
        "uncertainties": visible["uncertainties"],
        "truncated": len(endpoints) > spec["offset"] + spec["limit"],
        "total_matching": len(endpoints),
        "focus": {
            "id": focus,
            "retained": focus is not None and any(n["id"] == focus for n in nodes),
            "matches_filter": any(node["id"] == focus for node in matching),
            "status": "none" if focus is None
            else "visible" if any(n["id"] == focus for n in nodes) else "not_visible",
        },
        "note": "视图只投影节点；图没有断言层，因此没有边、路径或组织树",
    }
    result.update(_table(chosen, spec["group_by"]))
    return result
