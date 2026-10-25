"""What the browsing page shows, decided outside the GUI.

The page asks one question — "which nodes are visible at this reading position,
in this granularity, filtered this way" — and renders the answer. Step lists,
the related-node rule, counting and filtering all live here, so the QML holds no
opinion about the data model and cannot drift from it.

Two things are deliberately separate:

* the **step** the reader picked (a scene or an event) — that is what the list
  offers and what 相关节点 is computed from;
* the **anchor event** the step resolves to — that is the reading position both
  the node list and the property pane cut at. A scene is read when its last
  event is read, so a scene step anchors on its last child event.
"""

from __future__ import annotations

from collections import Counter

from .graph_query import revealed
from .order_ids import order_key

GRANULARITIES = ("scene", "event")
LABELS = {"scene": "场景", "event": "事件"}


def _key(node: dict) -> tuple:
    return order_key(node["id"]) or (999,)


def _by_id(snapshot: dict) -> dict[str, dict]:
    return {node["id"]: node for node in snapshot.get("nodes", []) if isinstance(node.get("id"), str)}


def _orders(snapshot: dict) -> dict[str, int]:
    return {
        row["source_id"]: row["ordinal"]
        for row in snapshot.get("source_orders", []) or []
        if isinstance(row, dict) and "source_id" in row and "ordinal" in row
    }


def anchor_event(snapshot: dict, node: dict) -> dict:
    """The event whose reading position counts as "read this far".

    An event is its own anchor. A scene is a container, so reading it means
    reading its last event; without children it falls back to itself.
    """
    if node.get("node_type") != "scene":
        return node
    nodes = _by_id(snapshot)
    children = [
        nodes[child] for child in node.get("events") or []
        if child in nodes and nodes[child].get("node_type") == "event"
    ]
    return max(children, key=_key) if children else node


def cut_for(snapshot: dict, step_id: str) -> dict | None:
    """Resolve a step to the position both projections cut at."""
    node = _by_id(snapshot).get(step_id)
    if node is None:
        return None
    anchor = anchor_event(snapshot, node)
    disclosure = anchor.get("disclosure") or {}
    start = disclosure.get("start")
    source_id = disclosure.get("source_id")
    if not isinstance(start, int) or not isinstance(source_id, str):
        return None
    return {
        "step_id": step_id,
        "step_name": node.get("name") or step_id,
        "step_kind": node.get("node_type"),
        "event_id": anchor["id"],
        "event_name": anchor.get("name") or anchor["id"],
        "source_id": source_id,
        "start": start,
    }


def steps(snapshot: dict, granularity: str = "scene") -> list[dict]:
    """Reading positions offered for one granularity, in reading order."""
    kind = granularity if granularity in GRANULARITIES else "scene"
    picked = [
        node for node in snapshot.get("nodes", [])
        if node.get("node_type") == kind and not node.get("retracted")
    ]
    picked.sort(key=_key)
    return [
        {
            "id": node["id"],
            "name": node.get("name") or node["id"],
            "kind": kind,
            "label": LABELS[kind],
            "start": (node.get("disclosure") or {}).get("start"),
        }
        for node in picked
    ]


def related_nodes(snapshot: dict, step_id: str) -> list[str]:
    """Nodes the step is about: itself plus its participants.

    A scene has no participants of its own, so it takes the union over the events
    it contains — "related to this scene" cannot mean the scene node alone.
    """
    nodes = _by_id(snapshot)
    node = nodes.get(step_id)
    if node is None:
        return []
    targets = [node]
    if node.get("node_type") == "scene":
        targets = [
            nodes[child] for child in node.get("events") or []
            if child in nodes and nodes[child].get("node_type") == "event"
        ] or [node]
    found = {step_id}
    for item in targets:
        found.update(
            participant for participant in item.get("participants") or []
            if participant in nodes
        )
    return sorted(found, key=lambda node_id: _key(nodes[node_id]))


def visible_nodes(snapshot: dict, cut: dict | None) -> list[dict]:
    """Every node the reader has met at this position."""
    nodes = [node for node in snapshot.get("nodes", []) if not node.get("retracted")]
    if cut is None:
        return nodes
    orders = _orders(snapshot)
    return [node for node in nodes if revealed(node, cut, orders)]


def matches(node: dict, needle: str) -> bool:
    if not needle:
        return True
    text = needle.casefold()
    if text in str(node.get("name") or "").casefold():
        return True
    return any(text in str(alias).casefold() for alias in node.get("aliases") or [])


def browse_view(
    snapshot: dict,
    *,
    granularity: str = "scene",
    cut_id: str = "",
    node_type: str = "",
    query: str = "",
    related_only: bool = False,
    time_filter: bool = False,
) -> dict:
    """One answer for the browsing page: nodes, counts, steps and the cut.

    ``time_filter`` decides whether the *list* obeys the reading position. The
    cut is resolved and returned either way, because the property pane always
    obeys it.

    Counts describe everything the other conditions leave — the reading position,
    相关节点, the search — but never the type filter itself: the sidebar *is* that
    filter, and a count that only ever shows the selected type would be useless.
    """
    cut = cut_for(snapshot, cut_id) if cut_id else None
    nodes = visible_nodes(snapshot, cut if (cut is not None and time_filter) else None)
    related = set(related_nodes(snapshot, cut_id)) if (related_only and cut_id) else None
    needle = query.strip()
    candidates = [
        node for node in nodes
        if (related is None or node["id"] in related) and matches(node, needle)
    ]
    counts = Counter(node.get("node_type") or "other" for node in candidates)
    shown = [
        node for node in candidates
        if not node_type or (node.get("node_type") or "other") == node_type
    ]
    shown.sort(key=_key)
    return {
        "granularity": granularity if granularity in GRANULARITIES else "scene",
        "revision": snapshot.get("revision"),
        "steps": steps(snapshot, granularity),
        # Pickers that need an event (补录) get the full list whatever the
        # granularity is — the reading position is not the only use of events.
        "events": steps(snapshot, "event"),
        "cut": cut,
        "nodes": shown,
        "counts": {key: counts.get(key, 0) for key in sorted(counts)},
        "total": len(shown),
        # 工程里到底有没有节点，用来区分"空工程"和"筛没了"。
        "project_total": len([
            node for node in snapshot.get("nodes", []) if not node.get("retracted")
        ]),
        "related_only": bool(related_only),
        "related": sorted(related) if related is not None else [],
        "time_filter": bool(time_filter),
    }
