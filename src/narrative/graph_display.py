"""Human-readable graph references, including private candidate batch IDs."""

from __future__ import annotations

from collections import Counter

_KINDS = {
    "node": "节点", "predicate": "谓词", "assertion": "断言", "evidence": "证据",
    "evidence_link": "证据关联", "context": "语境", "timeline": "时间轴",
    "source_order": "披露顺序",
}
_NODES = {
    "person": "人物", "item": "物品", "location": "地点", "event": "事件",
    "scene": "场景", "storyline": "故事线", "group": "群体", "other": "其他实体",
}


def display_map(snapshot: dict, candidate: dict | None = None) -> dict:
    objects = [*snapshot["objects"], *snapshot["evidence_links"]]
    if candidate:
        replaced = {item["id"] for item in candidate["objects"]}
        objects = [item for item in objects if item["id"] not in replaced] + candidate["objects"]
    by_id = {item["id"]: item for item in objects}
    sources = {item["id"]: item for item in snapshot["sources"]}
    names = Counter(item["name"] for item in objects if item["kind"] == "node")

    def label(ref_id: str | None, expected: str = "对象") -> str:
        if not ref_id or ref_id not in by_id:
            return f"未找到的{expected}"
        item = by_id[ref_id]
        if item["kind"] == "node":
            return item["name"]
        if item["kind"] == "predicate":
            return item["label"]
        if item["kind"] == "context":
            return item["label"]
        if item["kind"] == "timeline":
            return item["label"]
        if item["kind"] == "evidence":
            return item["quote"]
        if item["kind"] == "assertion":
            return f"{label(item['subject_id'],'节点')} · {label(item['predicate_id'],'谓词')}"
        return _KINDS.get(item["kind"], "对象")

    result: dict[str, dict] = {}
    for item in objects:
        kind = item["kind"]
        if kind == "node":
            name = item["name"]
            qualified = f"{name}（{item['id'][:8]}）" if names[name] > 1 else name
            summary = f"{_NODES[item['node_type']]} · {item.get('identity_note') or item.get('subtype') or item['context_id']}"
            kind_label = _NODES[item["node_type"]]
        elif kind == "predicate":
            name = item["label"]
            qualified = f"{name}（{item['id']}）"
            summary = f"{item['semantic']} · {item.get('description') or '受控谓词'}"
            kind_label = "谓词"
        elif kind == "assertion":
            object_label = label(item.get("object_id"), "节点") if item.get("object_id") else str(item.get("value", "时间边界"))
            role = item.get("qualifiers", {}).get("role")
            name = f"{label(item['subject_id'],'节点')} → {label(item['predicate_id'],'谓词')} → {object_label}"
            if role:
                name += f"（{role}）"
            qualified = name
            summary = f"{item['scope']} · {item['origin']} · {item['assessment']}"
            kind_label = "断言"
        elif kind == "evidence":
            name = item["quote"]
            qualified = name
            summary = f"{sources.get(item['source_id'], {}).get('title', '未找到的来源')} · {item['start']}–{item['end']}"
            kind_label = "证据"
        elif kind == "evidence_link":
            name = f"{label(item['assertion_id'],'断言')} ← {label(item['evidence_id'],'证据')}"
            qualified = name
            summary = item["role"]
            kind_label = "证据关联"
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
    return {"revision": snapshot["revision"], "candidate_id": candidate["id"] if candidate else None, "items": result}


def candidate_preview(snapshot: dict, candidate: dict) -> dict:
    labels = display_map(snapshot, candidate)
    rows = [
        {**labels["items"][item["id"]], "object": item}
        for item in candidate["objects"]
    ]
    return {
        "candidate": candidate,
        "rows": rows,
        "diagnostics": candidate.get("diagnostics", []),
        "display_map": labels,
    }
