"""Context-aware read projection for accepted records.

The graph is nodes plus property records. There is no assertion layer, so a
query answers "which nodes are visible here" and nothing about edges.
"""

from __future__ import annotations


def _contexts(objects: dict[str, dict], selected: str) -> list[str]:
    lineage, seen = [], set()
    current = selected
    while current:
        if current in seen or current not in objects or objects[current]["kind"] != "context":
            raise ValueError("查询语境不存在或形成循环")
        seen.add(current)
        lineage.append(current)
        current = objects[current].get("parent_id")
    return lineage


def _revealed(node: dict, cut: dict, orders: dict[str, int] | None = None) -> bool:
    """A node is visible once the reader has reached its disclosure position.

    ``orders`` maps a source to its reading ordinal. Without it a node from
    another source is always visible — true for a single-chapter project, wrong
    for a book: a chapter read later is not known yet.
    """
    disclosure = node.get("disclosure")
    if not isinstance(disclosure, dict):
        return False
    if disclosure.get("source_id") != cut.get("source_id"):
        if orders:
            source = disclosure.get("source_id")
            cut_source = cut.get("source_id")
            left = orders.get(source) if isinstance(source, str) else None
            right = orders.get(cut_source) if isinstance(cut_source, str) else None
            if left is not None and right is not None:
                return left < right
        return True
    start = disclosure.get("start")
    return isinstance(start, int) and start <= cut.get("start", 0)


def query_graph(
    snapshot: dict,
    *,
    node_id: str | None = None,
    query: str = "",
    node_ids: list[str] | None = None,
    kinds: list[str] | None = None,
    context_id: str = "canon",
    disclosure_cut: dict | None = None,
    limit: int | None = 200,
) -> dict:
    """Visible nodes in one context, narrowed by name or by reading position."""
    if limit is not None and (limit < 1 or limit > 2000):
        raise ValueError("检索数量须在 1–2000 之间")
    by_id = {item["id"]: item for item in snapshot["objects"]}
    lineage = _contexts(by_id, context_id)
    visible_contexts = set(lineage)
    nodes = {
        item["id"]: item
        for item in snapshot.get("nodes", [])
        if item["context_id"] in visible_contexts and not item.get("retracted")
    }

    needle = query.strip().casefold()
    selected = set(node_ids or [])
    if node_id:
        selected.add(node_id)
    if needle:
        selected |= {
            key for key, node in nodes.items()
            if needle in str(node.get("name", "")).casefold()
            or any(needle in str(alias).casefold() for alias in node.get("aliases") or [])
        }

    result = (
        [node for key, node in nodes.items() if key in selected] if (selected or needle)
        else list(nodes.values())
    )
    if kinds:
        result = [node for node in result if node.get("node_type") in kinds]
    if disclosure_cut is not None:
        orders = {
            row["source_id"]: row["ordinal"]
            for row in snapshot.get("source_orders", []) or []
            if isinstance(row, dict) and "source_id" in row and "ordinal" in row
        }
        result = [node for node in result if _revealed(node, disclosure_cut, orders)]
    if limit is not None:
        result = result[:limit]

    return {
        "revision": snapshot["revision"],
        "context_id": context_id,
        "context_lineage": lineage,
        "disclosure_cut": disclosure_cut,
        "nodes": result,
        "uncertainties": [],
        "note": "图只有节点与属性记录；没有断言层，因此不返回边",
    }
