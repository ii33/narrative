"""Readable, revision-scoped labels for UI references and review batches."""

from __future__ import annotations

from collections import Counter

from .review import KINDS

_LINKS = {
    "precedes": "先于",
    "causes": "导致",
    "motivates": "促使",
    "contrasts": "对照",
    "echoes": "呼应",
    "other": "关联",
}
_LINES = {"main": "主线", "branch": "支线", "unknown": "故事线"}
_RUN_STATUS = {
    "awaiting_results": "待提取", "running": "提取中", "complete": "已完成",
    "partial": "部分完成", "failed": "失败", "interrupted": "中断", "cancelled": "已取消",
}
_CANDIDATE_STATUS = {"pending": "待审", "accepted": "已接受", "rejected": "已拒绝"}
_ENTITY_TYPES = {"person": "人物", "location": "地点", "item": "物品", "other": "其他实体"}


def _short(value: str) -> str:
    return value[:8]


def _kind_label(obj: dict) -> str:
    if obj.get("kind") == "entity":
        return _ENTITY_TYPES.get(str(obj.get("entity_type")), "实体")
    return KINDS.get(str(obj.get("kind")), "记录")


def build_display_map(
    snapshot: dict,
    *,
    proposed: list[dict] | None = None,
    candidate_id: str | None = None,
    candidates: list[dict] | None = None,
    runs: list[dict] | None = None,
    candidate_summaries: dict[str, list[dict]] | None = None,
) -> dict:
    """Overlay one unaccepted batch on formal records without storing it."""
    objects = {
        obj["id"]: obj
        for table in ("sources", "entities", "assertions", "events", "records")
        for obj in snapshot[table]
    }
    layers = {object_id: "formal" for object_id in objects}
    for source in snapshot["sources"]:
        layers[source["id"]] = "source"
    for obj in proposed or []:
        objects[obj["id"]] = obj
        layers[obj["id"]] = "candidate"

    missing: dict[str, str] = {}

    def ref(object_id: str | None, expected: str = "实体") -> str:
        if not object_id:
            return "未指定"
        obj = objects.get(object_id)
        if obj is None:
            missing.setdefault(object_id, expected)
            return f"未找到的{expected}"
        return str(obj.get("name") or obj.get("title") or obj.get("category") or obj.get("predicate") or _short(object_id))

    items: dict[str, dict] = {}
    for object_id, obj in objects.items():
        kind = obj.get("kind", "source" if "text" in obj else "other")
        summary = ""
        if kind == "entity":
            label = str(obj["name"])
        elif kind == "source":
            label = str(obj["title"])
        elif kind == "event":
            label = str(obj["title"])
            participants = "、".join(
                f"{ref(row['entity_id'], '实体')}（{row['role']}）"
                for row in obj.get("participants", [])
            )
            summary = label + (f" · 参与：{participants}" if participants else "")
        elif kind == "assertion":
            label = f"{ref(obj.get('subject_id'), '人物')} · {obj['predicate']}"
        elif kind == "relation":
            label = f"{ref(obj['subject_id'], '人物')} → {obj['predicate']} → {ref(obj['object_id'], '人物')}"
        elif kind == "event_link":
            label = f"{ref(obj['source_event_id'], '事件')} · {_LINKS.get(obj['link_type'], obj['link_type'])} · {ref(obj['target_event_id'], '事件')}"
        elif kind == "storyline":
            label = f"{_LINES.get(obj['line_type'], '故事线')}：{obj['title']}"
        elif kind == "classification":
            expected = {"entity": "人物", "event": "事件", "relation": "关系", "storyline": "故事线", "assertion": "主张"}.get(obj["target_kind"], "对象")
            label = f"{ref(obj['target_id'], expected)} · {obj['category']}"
        elif kind == "state":
            label = f"{ref(obj.get('subject_id'), '实体')} · {obj.get('property_id', '状态')}"
        elif kind == "knowledge":
            label = f"{ref(obj.get('holder_id'), '人物')} · 关于 {ref(obj.get('assertion_id'), '主张')} 的认知"
        else:
            label = str(obj.get("title") or obj.get("name") or obj.get("predicate") or KINDS.get(kind, "记录"))
        items[object_id] = {
            "id": object_id,
            "kind": kind,
            "kind_label": "原文" if kind == "source" else _kind_label(obj),
            "label": label,
            "qualified_label": label,
            "summary": summary or label,
            "layer": layers[object_id],
            "origin": obj.get("origin"),
        }

    candidate_summaries = candidate_summaries or {}
    for candidate in candidates or []:
        counts = Counter(_kind_label(obj) for obj in candidate_summaries.get(candidate["id"], []))
        details = "、".join(f"{kind}{count}" for kind, count in counts.items())
        status = _CANDIDATE_STATUS.get(candidate["status"], candidate["status"])
        label = f"{status}候选 · {details or '内容待查看'} · 基于修订{candidate['expected_revision']}"
        items[candidate["id"]] = {
            "id": candidate["id"], "kind": "candidate", "kind_label": "候选批次",
            "label": label, "qualified_label": label, "summary": label,
            "layer": "review", "origin": candidate.get("source"),
        }
    for run in runs or []:
        source = ref(run.get("source_id"), "来源")
        run_status = str(run.get("status") or "任务")
        status = _RUN_STATUS.get(run_status, run_status)
        label = f"{source} · {status}"
        items[run["id"]] = {
            "id": run["id"], "kind": "run", "kind_label": "提取任务",
            "label": label, "qualified_label": label, "summary": label,
            "layer": "task", "origin": None,
        }

    for object_id, expected in missing.items():
        if object_id not in items:
            label = f"未找到的{expected}"
            items[object_id] = {
                "id": object_id, "kind": "missing", "kind_label": "未解析引用",
                "label": label, "qualified_label": label, "summary": label,
                "layer": "missing", "origin": None,
            }

    counts = Counter(item["label"] for item in items.values())
    same_kind_counts = Counter((item["kind"], item["label"]) for item in items.values())
    for item in items.values():
        if same_kind_counts[item["kind"], item["label"]] > 1:
            item["qualified_label"] = f"{item['label']} · {item['kind_label']} {_short(item['id'])}"
        elif counts[item["label"]] > 1:
            item["qualified_label"] = f"{item['kind_label']} · {item['label']}"

    return {"revision": snapshot["revision"], "candidate_id": candidate_id, "items": items}
