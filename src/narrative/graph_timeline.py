"""Reading-order timeline.

The graph has no story-time coordinates and no event relations, so the timeline
is the disclosure chain: events sorted by their own number. That is a real
order, not a placeholder — it is the order the reader meets them.
"""
from __future__ import annotations

from .graph_query import query_graph
from .order_ids import order_key


def timeline(
    snapshot: dict,
    *,
    context_id: str = "canon",
    disclosure_cut: dict | None = None,
    limit: int = 2000,
    _event_ids: set[str] | None = None,
) -> dict:
    if not 1 <= limit <= 2000:
        raise ValueError("时间轴节点数量须在 1–2000 之间")
    view = query_graph(
        snapshot, context_id=context_id, disclosure_cut=disclosure_cut, limit=None
    )
    events = [node for node in view["nodes"] if node.get("node_type") == "event"]
    if _event_ids is not None:
        events = [node for node in events if node["id"] in _event_ids]
    events.sort(key=lambda node: order_key(node["id"]) or ())

    ordered = [
        node | {
            "title": node.get("name"),
            "reading_order": index,
            "has_disclosure": bool(node.get("disclosure")),
        }
        for index, node in enumerate(events, start=1)
    ]
    truncated = len(ordered) > limit
    ordered = ordered[:limit]
    return {
        "revision": snapshot["revision"],
        "context_id": context_id,
        "disclosure_cut": disclosure_cut,
        "events": ordered,
        "nodes": ordered,
        "event_ids": [node["id"] for node in ordered],
        "reading_order": ordered,
        "unknown": [],
        "lanes": [],
        "known_time": [],
        "relative": [],
        "relations": [],
        "temporal_edges": [],
        "organization_edges": [],
        "nested_axes": [],
        "uncertainties": [],
        "truncated": truncated,
        "message": "时间轴即阅读顺序：按节点编号排列，不含故事时间坐标与事件间关系",
    }
