"""Human-readable references for persisted graph state."""

from __future__ import annotations

from collections import Counter

_KINDS = {
    "node": "节点", "context": "语境", "timeline": "时间轴", "source_order": "披露顺序",
}
_NODES = {
    "person": "人物", "item": "物品", "location": "地点", "event": "事件", "action": "动作",
    "scene": "场景", "big_event": "大事件", "storyline": "主要故事", "group": "群体", "other": "其他实体",
}


def display_map(snapshot: dict) -> dict:
    objects = list(snapshot["objects"])
    by_id = {item["id"]: item for item in objects}
    sources = {item["id"]: item for item in snapshot["sources"]}
    names = Counter(item["name"] for item in objects if item["kind"] == "node")

    def label(ref_id: str | None, expected: str = "对象") -> str:
        if not ref_id or ref_id not in by_id:
            return f"未找到的{expected}"
        item = by_id[ref_id]
        if item["kind"] == "node":
            return item["name"]
        if item["kind"] == "context":
            return item["label"]
        if item["kind"] == "timeline":
            return item["label"]
        return _KINDS.get(item["kind"], "对象")

    result: dict[str, dict] = {}
    for item in objects:
        kind = item["kind"]
        if kind == "node":
            name = item["name"]
            qualified = f"{name}（{item['id'][:8]}）" if names[name] > 1 else name
            summary = f"{_NODES[item['node_type']]} · {item.get('identity_note') or item.get('subtype') or item['context_id']}"
            kind_label = _NODES[item["node_type"]]
        elif kind == "source_order":
            name = sources.get(item["source_id"], {}).get("title", "未找到的来源")
            qualified = name
            summary = f"阅读序号 {item['ordinal']}"
            kind_label = "披露顺序"
        else:
            name = item.get("label", _KINDS.get(kind, "对象"))
            qualified = name
            summary = item.get("context_type") or item.get("calendar") or ""
            kind_label = _KINDS.get(kind, "对象")
        result[item["id"]] = {
            "id": item["id"], "kind": kind, "kind_label": kind_label,
            "label": name, "qualified_label": qualified, "summary": summary,
        }
    return {"revision": snapshot["revision"], "items": result}
