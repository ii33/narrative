"""Qt-independent projections for the native graph canvas and detail panels."""

from __future__ import annotations

from dataclasses import dataclass, field

_NODE_PRIORITY = {"person": 0, "group": 1, "storyline": 2, "event": 3}


def _node(item: dict, labels: dict[str, dict], positions: dict[str, tuple[float, float]]) -> dict:
    view = {
        "id": item["id"], "name": item["name"], "node_type": item["node_type"],
        "kind_label": labels.get(item["id"], {}).get("kind_label", "节点"),
        "summary": labels.get(item["id"], {}).get("summary", ""),
        "context_id": item.get("context_id", "canon"),
        **{key: item.get(key) for key in (
            "occurrence", "depth", "parent_id", "disclosure", "evidence",
            "identity_note", "participants", "predecessors", "successors",
            "events", "aliases",
        )},
    }
    if item["id"] in positions:
        view["x"], view["y"] = positions[item["id"]]
    return view


def _edge(item: dict) -> dict:
    """Preserve direction and epistemic status of an accepted direct assertion."""
    if item.get("direct") is not True or not item.get("object_id"):
        raise ValueError("画布边只能来自已接受的直接节点断言")
    view = {key: item.get(key) for key in (
        "assertion_id", "subject_id", "object_id", "predicate_id", "label",
        "semantic", "role", "scope", "holder_id", "origin", "derived_status", "direct",
        "temporal_relation", "temporal_left", "temporal_right", "evidence", "validity",
        "disclosure", "polarity", "modality", "assessment", "order_index",
    )}
    view["temporal_certain"] = (item.get("semantic") == "temporal"
                                and item.get("scope") == "world"
                                and item.get("polarity") == "positive"
                                and item.get("modality") == "actual"
                                and item.get("assessment") != "retracted"
                                and item.get("derived_status") == "valid"
                                and not any((item.get(key) or {}).get("form") == "event_boundary"
                                            and item[key]["event_id"] != item[node_key]
                                            for key, node_key in (("temporal_left", "subject_id"),
                                                                  ("temporal_right", "object_id"))))
    # ListModel fixes role types from its first row; ordinary edges precede time edges.
    view["temporal_relation"] = item.get("temporal_relation") or ""
    view["temporal_left"] = item.get("temporal_left") or {}
    view["temporal_right"] = item.get("temporal_right") or {}
    return view


@dataclass
class CanvasState:
    """Only a view cache. Node coordinates belong to the user's canvas."""

    revision: int | None = None
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: dict[str, dict] = field(default_factory=dict)
    positions: dict[str, tuple[float, float]] = field(default_factory=dict)
    expanded: set[str] = field(default_factory=set)

    anchor_edges: set[str] = field(default_factory=set)
    anchors: set[str] = field(default_factory=set)
    contributions: dict[str, tuple[set[str], set[str]]] = field(default_factory=dict)

    def clear(self, *, preserve_positions: bool = False) -> None:
        self.revision = None
        self.nodes.clear()
        self.edges.clear()
        self.expanded.clear()
        self.anchors.clear()
        self.anchor_edges.clear()
        self.contributions.clear()
        if not preserve_positions:
            self.positions.clear()

    def set_position(self, node_id: str, x: float, y: float) -> None:
        if node_id in self.nodes:
            self.positions[node_id] = (x, y)

    def replace_query(self, view: dict, labels: dict[str, dict]) -> dict:
        """Replace a bounded Operations query, never union it with old neighborhoods."""
        self.clear(preserve_positions=True)
        self.revision = view["revision"]
        self.nodes = {item["id"]: _node(item, labels, self.positions) for item in view["nodes"]}
        self.edges = {item["assertion_id"]: _edge(item) for item in view["edges"]}
        self.anchors = set(self.nodes)
        self.anchor_edges = set(self.edges)
        return {"reset": True, "revision": self.revision, "nodes": list(self.nodes.values()),
                "edges": list(self.edges.values()), "truncated": view.get("truncated", False),
                "pending_excluded": True, "focus": view.get("focus", {})}

    def seed(self, view: dict, labels: dict[str, dict], *, limit: int = 24, prefer_events: bool = False) -> dict:
        self.clear(preserve_positions=True)
        self.revision = view["revision"]
        candidates = sorted(
            view["nodes"],
            key=lambda item: (_NODE_PRIORITY.get(item["node_type"], 4), item["name"], item["id"]),
        )
        anchors = [item for item in candidates if item["node_type"] in {"person", "group", "storyline"}]
        if prefer_events:
            anchors = [item for item in candidates if item["node_type"] in {"storyline", "scene", "event"}]
        if not anchors:
            anchors = candidates
        for item in anchors[:limit]:
            self.nodes[item["id"]] = _node(item, labels, self.positions)
        self.anchors = set(self.nodes)
        if prefer_events:
            for item in view.get("edges", []):
                if item.get("semantic") == "temporal" and item["subject_id"] in self.nodes and item["object_id"] in self.nodes:
                    edge = _edge(item)
                    self.edges[edge["assertion_id"]] = edge
        self.anchor_edges = set(self.edges)
        return {
            "reset": True, "revision": self.revision, "nodes": list(self.nodes.values()),
            "edges": list(self.edges.values()), "pending_excluded": view.get("pending_excluded", True),
            "anchor_count": len(self.nodes), "total_visible_nodes": len(view["nodes"]),
            "truncated": len(anchors) > limit,
        }

    def expand(self, view: dict, labels: dict[str, dict]) -> dict:
        if view["revision"] != self.revision:
            raise ValueError("正式图已变化，请重新加载画布")
        center_id = view["center_id"]
        if view["center_status"] != "visible":
            raise ValueError("该节点在当前正式图中不可见")
        was_expanded = center_id in self.expanded
        nodes_added = []
        for item in view["nodes"]:
            if item["id"] not in self.nodes:
                node = _node(item, labels, self.positions)
                self.nodes[item["id"]] = node
                nodes_added.append(node)
        edges_added = []
        for item in view["edges"]:
            edge = _edge(item)
            if edge["assertion_id"] not in self.edges:
                self.edges[edge["assertion_id"]] = edge
                edges_added.append(edge)
        self.expanded.add(center_id)
        self.contributions[center_id] = (
            {n["id"] for n in view["nodes"]} | {center_id},
            {e["assertion_id"] for e in view["edges"]},
        )
        return {
            "center_id": center_id, "revision": self.revision,
            "nodes_added": nodes_added, "edges_added": edges_added,
            "already_expanded": was_expanded, "pending_excluded": view.get("pending_excluded", True),
            "truncated": view.get("truncated", False), "uncertainties": view.get("uncertainties", []),
        }


    def collapse(self, center_id: str) -> dict:
        already = center_id not in self.expanded
        self.expanded.discard(center_id)
        self.contributions.pop(center_id, None)
        kept_nodes = set(self.anchors) | set(self.expanded)
        kept_edges: set[str] = set(self.anchor_edges)
        for node_ids, edge_ids in self.contributions.values():
            kept_nodes.update(node_ids)
            kept_edges.update(edge_ids)
        removed_edges = sorted(set(self.edges) - kept_edges)
        for edge_id in removed_edges:
            del self.edges[edge_id]
        for edge in self.edges.values():
            kept_nodes.update((edge["subject_id"], edge["object_id"]))
        removed_nodes = sorted(set(self.nodes) - kept_nodes)
        for node_id in removed_nodes:
            del self.nodes[node_id]
        return {"center_id": center_id, "revision": self.revision,
                "nodes_removed": removed_nodes, "edges_removed": removed_edges,
                "already_collapsed": already}

    def project(self, filters: dict | None = None) -> dict:
        """Apply reversible display filters without changing accepted graph/cache."""
        filters = filters or {}
        types = None if filters.get("node_types") is None else set(filters["node_types"])
        semantics = None if filters.get("semantics") is None else set(filters["semantics"])
        nodes = {key: value for key, value in self.nodes.items()
                 if (types is None or value["node_type"] in types)
                 and (filters.get("max_event_depth") is None or value["node_type"] != "event"
                      or value.get("depth") is None or value["depth"] <= filters["max_event_depth"])}
        edges = [e for e in self.edges.values()
                 if e["subject_id"] in nodes and e["object_id"] in nodes
                 and (semantics is None or e.get("semantic") in semantics)
                 and (not filters.get("temporal_only") or e.get("semantic") == "temporal")]
        if filters.get("temporal_only"):
            ids = {e[k] for e in edges for k in ("subject_id", "object_id")}
            nodes = {key: value for key, value in nodes.items() if key in ids}
        return {"revision": self.revision, "nodes": list(nodes.values()), "edges": edges,
                "expanded": sorted(self.expanded), "display_only": True}


def node_detail(
    view: dict, node_id: str, labels: dict[str, dict], sources: list[dict] | None = None,
) -> dict:
    """Build a readable node dossier from the node itself.

    There is no assertion layer, so the dossier carries no relations: only the
    node, its evidence and whatever the node records about itself.
    """
    by_id = {item["id"]: item for item in view["nodes"]}
    if node_id not in by_id:
        raise ValueError("节点在当前正式图中不存在或不可见")
    assertions = []
    related_ids: set[str] = set()
    source_titles = {item["id"]: item["title"] for item in sources or []}
    for assertion in view.get("assertions", []):
        row = dict(assertion)
        row["role"] = row.get("qualifiers", {}).get("role")
        row["evidence"] = [
            item | {"source_title": source_titles.get(item.get("source_id"), "未找到的来源")}
            for item in row.get("evidence", [])
        ]
        row["subject_label"] = labels.get(row["subject_id"], {}).get("label", "未找到的节点")
        object_id = row.get("object_id")
        row["object_label"] = labels.get(object_id, {}).get("label", "未找到的节点") if object_id else str(row.get("value", ""))
        holder_id = row.get("holder_id")
        row["holder_label"] = labels.get(holder_id, {}).get("label", "未找到的持有人") if holder_id else ""
        row["organization_label"] = {
            "active": "当前归属", "superseded": "历史归属", "unresolved": "归属待确认",
        }.get(row.get("organization_status", ""), "")
        assertions.append(row)
        if row.get("organization_status", "active") != "active":
            continue
        for ref in (row["subject_id"], object_id, holder_id):
            if ref and ref != node_id and ref in by_id:
                related_ids.add(ref)
    related = [_node(by_id[ref], labels, {}) for ref in related_ids]
    related.sort(key=lambda item: (
        0 if item["node_type"] in {"event", "scene", "storyline", "group"} else 1,
        item["name"], item["id"],
    ))
    return {
        "node": _node(by_id[node_id], labels, {}), "revision": view["revision"],
        "assertions": assertions,
        "related_nodes": related,
        "uncertainties": view.get("uncertainties", []),
        "pending_excluded": view.get("pending_excluded", True),
    }


def candidate_summary(candidate: dict) -> dict:
    return {key: candidate.get(key) for key in (
        "id", "status", "version", "source", "note", "created_at",
        "expected_revision", "result_revision", "parent_candidate_id", "run_id",
    )} | {"object_count": len(candidate.get("objects", []))}


def search_result(view: dict, labels: dict[str, dict], types: list[str]) -> dict:
    allowed = set(types)
    return {
        "revision": view["revision"],
        "nodes": [_node(item, labels, {}) for item in view["nodes"] if not allowed or item["node_type"] in allowed],
        "pending_excluded": view.get("pending_excluded", True),
        "uncertainties": view.get("uncertainties", []),
    }

