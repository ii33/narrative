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
    objects = list(snapshot["objects"])
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


_SCOPES = {"world": "世界", "cognition": "认知", "claim": "声称"}


def candidate_rows(snapshot: dict, detail: dict) -> list[dict]:
    """一条候选里到底有什么，按人能读的方式列出来。

    候选可以只带几条属性（节点已经在图里了），所以不能只列节点——审核页要看到
    「林舟 · 身份 = 主角」这样一行，而不是一个空面板。
    """
    from .property_labels import label_for

    names = {node["id"]: node.get("name") for node in snapshot.get("nodes", [])}
    for obj in detail.get("graph_objects") or []:
        if obj.get("kind") == "node":
            names[obj["id"]] = obj.get("name")

    rows: list[dict] = []
    for obj in detail.get("graph_objects") or []:
        if obj.get("kind") != "node":
            continue
        rows.append({
            "id": obj["id"],
            "kind": _NODES.get(obj.get("node_type") or "", "节点"),
            "label": obj.get("name") or obj["id"],
            "extra": obj.get("node_type") or "",
        })
    for record in detail.get("property_records") or []:
        kind = record.get("kind")
        if kind == "property_value":
            node_id = str(record.get("node_id") or "")
            aspect = label_for(str(record.get("property_id") or ""))
            rows.append({
                "id": record.get("id") or "",
                "kind": "属性",
                "label": f"{names.get(node_id) or node_id} · {aspect} = "
                         f"{record.get('value')}",
                "extra": _SCOPES.get(record.get("scope") or "world", "世界"),
            })
        elif kind == "property_retraction":
            rows.append({
                "id": record.get("id") or "",
                "kind": "失效",
                "label": f"撤掉 {record.get('target_id') or '（空）'}",
                "extra": str(record.get("reason") or ""),
            })
    return rows
