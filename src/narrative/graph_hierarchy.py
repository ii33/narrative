"""Reviewed containment projection; structure never implies time or participation."""

from __future__ import annotations

from collections import defaultdict

from .graph_query import query_graph


def preview_snapshot(snapshot: dict, candidate: dict) -> dict:
    """Overlay a prechecked pending candidate without changing the accepted store."""
    replaced = {obj["id"] for obj in candidate["objects"]}
    objects = [obj for obj in snapshot["objects"] if obj["id"] not in replaced]
    links = [link for link in snapshot["evidence_links"] if link["id"] not in replaced]
    for obj in candidate["objects"]:
        (links if obj["kind"] == "evidence_link" else objects).append(obj)
    groups = {key: [] for key in ("nodes", "predicates", "assertions", "evidences", "contexts", "timelines", "source_orders")}
    for obj in objects:
        groups[obj["kind"] + "s"].append(obj)
    revisions = snapshot["object_revisions"] | {
        obj["id"]: snapshot["revision"] + 1 for obj in candidate["objects"]
        if obj["kind"] != "evidence_link"
    }
    return snapshot | {"objects": objects, "evidence_links": links, "object_revisions": revisions, **groups}


def hierarchy(
    snapshot: dict,
    *,
    candidate_id: str | None = None,
    context_id: str = "canon",
    story_cut: dict | None = None,
    disclosure_cut: dict | None = None,
    limit: int = 2000,
    focus_id: str | None = None,
    _complete: bool = False,
) -> dict:
    if limit < 1 or limit > 2000:
        raise ValueError("层级节点数量须在 1–2000 之间")
    view = query_graph(
        snapshot, context_id=context_id, story_cut=story_cut,
        disclosure_cut=disclosure_cut, limit=None,
    )
    cut = story_cut is not None or disclosure_cut is not None
    visible_ids = ({n["id"] for n in view["nodes"]} if cut else
                   {n["id"] for n in snapshot["nodes"] if n["context_id"] in view["context_lineage"]})
    # An uncut overview may include isolated entities; a cut must use only
    # identities that the cut query exposed.
    available = [n for n in snapshot["nodes"] if n["id"] in visible_ids]
    containment_predicates = {p["id"] for p in snapshot["predicates"] if p["semantic"] == "containment"}
    containment_view = query_graph(
        snapshot, predicate_ids=list(containment_predicates),
        context_id=context_id, story_cut=story_cut,
        disclosure_cut=disclosure_cut, limit=None,
    )
    active = [a for a in containment_view["assertions"] if a["semantic"] == "containment"
              and a["scope"] == "world" and a["polarity"] == "positive"
              and a["modality"] == "actual" and a["origin"] in {"original", "inference"}
              and a["assessment"] != "retracted" and a["derived_status"] == "valid"
              and any(e["role"] == "supports" for e in a["evidence"])]
    # A disclosed revision invalidates its prior organization even when the
    # successor is withdrawn or its inference later becomes stale.
    replaced = {a["supersedes_id"] for a in containment_view["assertions"] if a.get("supersedes_id")}
    active = [a for a in active if a["id"] not in replaced
              and a["subject_id"] in visible_ids and a.get("object_id") in visible_ids]
    source_order: dict[str, int] = {}
    for layer in reversed(view["context_lineage"]):
        for row in snapshot["source_orders"]:
            if row["context_id"] == layer:
                source_order[row["source_id"]] = row["ordinal"]

    def position(a: dict) -> tuple:
        disclosure = a.get("disclosure")
        if disclosure is None:
            evidence = a.get("evidence", [])
            disclosure = min(({"source_id": e["source_id"], "start": e["start"]} for e in evidence),
                             key=lambda e: (source_order.get(e["source_id"], float("inf")), e["start"]),
                             default={})
        ordinal = source_order.get(disclosure.get("source_id", ""))
        return (ordinal, disclosure.get("start", float("inf"))) if ordinal is not None else (float("inf"), float("inf"))

    def sort_key(a: dict) -> tuple:
        return (a.get("order_index") if a.get("order_index") is not None else float("inf"),
                *position(a), a["id"])

    parent: dict[str, str] = {}
    children: dict[str, list[str]] = defaultdict(list)
    edge_by_child: dict[str, dict] = {}
    uncertainties = list(view["uncertainties"])
    active_ids = {a["id"] for a in active}
    uncertainties.extend({"assertion_id": a["id"], "reason": "已披露组织修订当前无有效归属；恢复旧父须显式新增修订"}
                         for a in containment_view["assertions"]
                         if a.get("supersedes_id") and a["id"] not in replaced and a["id"] not in active_ids)
    for a in sorted(active, key=sort_key):
        child = a["object_id"]
        if child in parent or child == a["subject_id"]:
            uncertainties.append({"assertion_id": a["id"], "reason": "组织边冲突，未用于层级投影"})
            continue
        parent[child] = a["subject_id"]
        children[a["subject_id"]].append(child)
        edge_by_child[child] = a
    node_positions: dict[str, tuple] = {}
    for a in view["assertions"]:
        for node_id in (a["subject_id"], a.get("object_id")):
            if node_id:
                node_positions[node_id] = min(node_positions.get(node_id, (float("inf"), float("inf"))), position(a))
    roots = sorted((n["id"] for n in available if n["id"] not in parent),
                   key=lambda node_id: (*node_positions.get(node_id, (float("inf"), float("inf"))), node_id))
    by_id = {n["id"]: n for n in available}
    ordered: list[dict] = []
    seen: set[str] = set()

    def visit(node_id: str, start_depth: int = 0, root_id: str | None = None,
              descend: bool = True) -> None:
        stack = [(node_id, start_depth)]
        while stack and (_complete or len(ordered) < limit):
            current, depth = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            ordered.append(by_id[current] | {"parent_id": parent.get(current),
                                             "child_ids": children.get(current, []), "depth": depth,
                                             "root_id": root_id or node_id,
                                             "parent_edge_id": edge_by_child.get(current, {}).get("id")})
            if descend:
                stack.extend((child, depth + 1) for child in reversed(children.get(current, [])))

    projection_count = len(available)
    if focus_id is not None:
        if focus_id not in by_id:
            raise ValueError("视点节点在当前语境、版本或阅读进度中不可见")
        ancestry, ancestor_seen = [], {focus_id}
        current = parent.get(focus_id)
        while current is not None and current in by_id and current not in ancestor_seen:
            ancestry.append(current)
            ancestor_seen.add(current)
            current = parent.get(current)
        root_id = ancestry[-1] if ancestry else focus_id
        # Retain the focus before applying the bound, then its ancestors and
        # descendants. Unrelated roots and sibling branches are outside scope.
        visit(focus_id, len(ancestry), root_id, descend=False)
        for index, ancestor in enumerate(ancestry):
            visit(ancestor, len(ancestry) - index - 1, root_id, descend=False)
        descendants, stack = set(), list(children.get(focus_id, []))
        while stack:
            node_id = stack.pop()
            if node_id not in descendants and node_id not in ancestor_seen:
                descendants.add(node_id)
                stack.extend(children.get(node_id, []))
        projection_count = len(ancestor_seen | descendants)
        for child in children.get(focus_id, []):
            visit(child, len(ancestry) + 1, root_id)
    else:
        for root in roots:
            visit(root)
        # Defensive fallback for legacy or externally written cyclic data.
        for node_id in sorted(by_id):
            visit(node_id)
    if focus_id is not None:
        focused = next((n for n in ordered if n["id"] == focus_id), None)
        if focused is None:
            raise ValueError("视点节点在当前语境、版本或阅读进度中不可见")
        for node in ordered:
            node["relative_depth"] = node["depth"] - focused["depth"]
    selected = {n["id"] for n in ordered}
    edges = [edge_by_child[n["id"]] for n in ordered
             if n["id"] in edge_by_child and parent[n["id"]] in selected]
    for node in ordered:
        node["child_ids"] = [child for child in node["child_ids"] if child in selected]
    return {
        "revision": snapshot["revision"], "candidate_id": candidate_id, "focus_id": focus_id,
        "pending_excluded": candidate_id is None, "roots": [r for r in roots if r in selected],
        "nodes": ordered, "edges": edges,
        "truncated": not _complete and projection_count > limit,
        "uncertainties": uncertainties,
    }
