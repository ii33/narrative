"""阅读先后按正式原文顺序与字符位置比较，不以节点编号或故事时间推断。"""

from __future__ import annotations

from .browse_view import _members, _reading_key
from .graph_query import query_graph


def _visible(snapshot: dict, context_id: str, disclosure_cut: dict | None) -> dict[str, dict]:
    view = query_graph(
        snapshot, context_id=context_id, disclosure_cut=disclosure_cut, limit=None
    )
    return {
        node["id"]: node
        for node in view["nodes"]
        if node.get("node_type") in {"event", "action"}
    }


def _storyline_spans(
    snapshot: dict, context_id: str, disclosure_cut: dict | None
) -> list[dict]:
    """start/end 是稳定相邻阅读区间；first/last 是当前可见内容成员。"""
    from .browse_view import _orders
    from .storyline_ranges import analyze, reading_starts
    starts, orders = reading_starts(snapshot), _orders(snapshot)
    all_nodes = {node["id"]: node for node in snapshot["nodes"]}
    def visible(identifier):
        node = all_nodes[identifier]
        point = node.get("disclosure") if node.get("node_type") == "storyline" else starts.get(identifier)
        if node.get("node_type") == "storyline" and not point and node.get("completion"):
            point = {"source_id": node["completion"]["source_id"], "start": node["completion"]["start"] - 1}
        return not disclosure_cut or not point or (orders.get(point["source_id"], -1), point["start"]) <= (orders.get(disclosure_cut["source_id"], -1), disclosure_cut["start"])
    ranges = {row['id']: row for row in analyze(snapshot)[0] if row['context_id'] == context_id}
    nodes = {node["id"]: node for node in snapshot["nodes"]
             if not node.get("retracted") and node.get("context_id", "canon") == context_id
             and visible(node["id"])}
    events = {node_id for node_id, node in nodes.items() if node.get("node_type") in {"event", "action"}}
    spans: list[dict] = []
    for node in nodes.values():
        if node.get("node_type") not in {"storyline", "big_event"}:
            continue
        scenes = [row for row in _members(nodes, node) if row.get("node_type") == "scene" and visible(row["id"])]
        members = [row["id"] for row in _members(nodes, node) if row["id"] in events]
        if not members:
            continue
        ordered = sorted(members, key=lambda identifier: _reading_key(snapshot)(nodes[identifier]))
        spans.append({
            "id": node["id"],
            "node_type": node["node_type"],
            "parent_id": ranges.get(node["id"], {}).get("parent_id"),
            "start": ranges.get(node["id"], {}).get("start"),
            "end": ranges.get(node["id"], {}).get("end"),
            "name": node.get("name", node["id"]),
            "scene_count": len(scenes),
            "event_count": len(ordered),
            "first": ordered[0],
            "last": ordered[-1],
            "scenes": [scene["id"] for scene in scenes],
        })
    rank = {identifier: index for index, identifier in enumerate(ranges)}
    return sorted(spans, key=lambda row: rank.get(row["id"], len(rank)))


def event_lines(
    snapshot: dict, *, context_id: str = "canon", disclosure_cut: dict | None = None
) -> dict:
    """阅读顺序下的事件列表，每个事件一条。"""
    events = _visible(snapshot, context_id, disclosure_cut)
    ordered = sorted(events.values(), key=_reading_key(snapshot))
    spans = _storyline_spans(snapshot, context_id, disclosure_cut)
    return {
        "revision": snapshot["revision"],
        "context_id": context_id,
        "disclosure_cut": disclosure_cut,
        "events": [
            {
                "id": node["id"],
                "name": node.get("name", node["id"]),
                "node_type": node["node_type"],
                "reading_order": index,
                "disclosure": node.get("disclosure"),
            }
            for index, node in enumerate(ordered, start=1)
        ],
        "relations": [],
        # 大事件是一段，不是一点：从它第一个场景的第一个事件，到最后一个场景的最后一个事件。
        "storylines": [row for row in spans if row["node_type"] == "storyline"],
        "big_events": [row for row in spans if row["node_type"] == "big_event"],
        "note": "只有阅读顺序；事件之间没有存储的关系",
    }


def compare_events(
    snapshot: dict,
    left_id: str,
    right_id: str,
    *,
    context_id: str = "canon",
    disclosure_cut: dict | None = None,
    axis: str = "disclosure",
) -> dict:
    """比较两个事件的阅读顺序。

    只剩 ``axis="disclosure"``：读者先读到哪一个。``axis="story"`` 没有数据来源，
    一律返回 ``unknown``。
    """
    events = _visible(snapshot, context_id, disclosure_cut)
    base = {
        "left_id": left_id,
        "right_id": right_id,
        "context_id": context_id,
        "disclosure_cut": disclosure_cut,
        "axis": axis,
        "revision": snapshot["revision"],
    }
    if left_id not in events or right_id not in events:
        return base | {
            "relation": "unknown",
            "basis": "disclosure",
            "reason": "事件不在当前语境/披露切片可见视图中",
        }
    if axis != "disclosure":
        return base | {
            "relation": "unknown",
            "basis": "none",
            "reason": "当前模型不产出事件间关系，故事轴没有依据",
        }

    left = _reading_key(snapshot)(events[left_id])[:2]
    right = _reading_key(snapshot)(events[right_id])[:2]
    if left is None or right is None:
        return base | {
            "relation": "unknown",
            "basis": "disclosure",
            "reason": "节点缺少阅读编号",
        }
    if left == right:
        relation = "same"
    elif left < right:
        relation = "before"
    else:
        relation = "after"
    return base | {
        "relation": relation,
        "basis": "disclosure",
        "reason": "按原文来源顺序和披露位置比较阅读顺序",
    }
