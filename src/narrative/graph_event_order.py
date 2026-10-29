"""事件先后：只按阅读顺序（节点编号）比较。

没有断言层，就没有故事时间偏序。这里能回答的只有一件事：读者先读到哪个事件。

**同类型内按编号排**：编号是按 ``(原文序, 字符偏移)`` 发的，落在两个节点之间就发
``prev.0.k``（如 ``E1.0.1``），从不追加到末尾，所以同一类型里编号顺序就是阅读顺序。
这里只处理事件，事件都在同一个序列里，于是比编号就够——人工挪过披露位置也不改编号，
顺序跟着编号走。

跨类型（人物和事件混在一起）编号之间没有先后，那种列表按 ``(原文序, 披露位置)`` 排，
见 ``browse_view``。
"""

from __future__ import annotations

from .graph_query import query_graph
from .order_ids import order_key


def _visible(snapshot: dict, context_id: str, disclosure_cut: dict | None) -> dict[str, dict]:
    view = query_graph(
        snapshot, context_id=context_id, disclosure_cut=disclosure_cut, limit=None
    )
    return {
        node["id"]: node
        for node in view["nodes"]
        if node.get("node_type") == "event"
    }


def event_lines(
    snapshot: dict, *, context_id: str = "canon", disclosure_cut: dict | None = None
) -> dict:
    """阅读顺序下的事件列表，每个事件一条。"""
    events = _visible(snapshot, context_id, disclosure_cut)
    ordered = sorted(events.values(), key=lambda node: order_key(node["id"]) or ())
    return {
        "revision": snapshot["revision"],
        "context_id": context_id,
        "disclosure_cut": disclosure_cut,
        "events": [
            {
                "id": node["id"],
                "name": node.get("name", node["id"]),
                "node_type": "event",
                "reading_order": index,
                "disclosure": node.get("disclosure"),
            }
            for index, node in enumerate(ordered, start=1)
        ],
        "relations": [],
        "note": "只有阅读顺序；没有断言层，因此没有事件间关系",
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

    left, right = order_key(left_id), order_key(right_id)
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
        "reason": "按节点编号比较阅读顺序",
    }
