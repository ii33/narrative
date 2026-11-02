"""Deterministic draft validation and automatic apply tools for the conversation agent."""

from __future__ import annotations

import json
import re

DRAFT_VERSION = "conversation-draft/13"

RESULT_SCHEMA = {
    "type": "object", "required": ["nodes", "properties"],
    "description": "引用优先已有正式节点ID或@1起原始nodes完整数组索引；名称仅在唯一时可解析，同名须ID/@。",
    "properties": {
        "nodes": {"type": "array", "items": {"type": "object", "required": ["name", "type"],
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string", "enum": ["person", "group", "item", "location", "storyline", "big_event", "scene", "event", "action"]},
                "quotes": {"type": "array", "items": {"type": "string"}},
                "closing_quote": {"type": "string", "description": "storyline完整结束后总结时须给出逐字唯一闭合证据，程序计算completion"},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "existing_id": {"type": "string", "description": "查证后引用本工程已有节点；不新建同一事件"},
                "new_occurrence": {"type": "boolean", "description": "同名但原文明确是另一次发生时声明"},
                "context_id": {"type": "string"},
                "participants": {"type": "array", "items": {"type": "string"},
                    "description": "事件中原文明示出场或有实际作用的人物引用；包含次要或无名人物，不含地点"},
                "children": {"type": "array", "items": {"type": "string"}, "description": "容器直接子项ID/@，可归入已有子项而不重列原文；按T→B→S→E→A校验"},
                "scene": {"type": "string", "description": "同语境场景的正式ID或本批@序号；唯一名亦可"},
                "event": {"type": "string", "description": "action直属事件正式ID或本批@序号；唯一名亦可，仅按需动作提取使用"},
                "big_event": {"type": "string", "description": "scene所属阶段性大事件的B编号或本批@序号"},
                "storyline": {"type": "string", "description": "big_event所属已结束主要故事的T编号或本批@序号；未总结篇章可省略"},
            }, "additionalProperties": False}},
        "properties": {"type": "array", "items": {"type": "object", "required": ["node", "property", "value", "quotes"],
            "properties": {
                "node": {"type": "string", "description": "属性主体；身份、关系或作用角色中为参照方A"}, "property": {"type": "string", "description": "关系描述对方相对node的身份、关系或作用中的角色；node=A,property=父亲,value=B表示B是A的父亲；事件地点使用属性名‘地点’并引用地点节点"},
                "value": {"description": "node/either节点引用表示被指向的对方ID；普通string/any值保持字面内容；事件地点的value引用location节点"}, "value_type": {"type": "string", "enum": ["string", "number", "boolean", "node", "either", "any"], "description": "节点引用用node/either；事件地点使用node，string/any即使内容像P编号也是字面值"},
                "quotes": {"type": "array", "items": {"type": "string"}},
                "symmetric": {"type": "boolean", "description": "该关系性质对称时显式true；单向false，不按名称猜测"},
                "category": {"type": "string"}, "scope": {"type": "string", "enum": ["world", "cognition", "claim"], "description": "world为原文支持的客观事实；事件地点为world；cognition为某人物所知所信/主观判断，claim为人物言说主张，二者须给holder；对白可有客观事实依据，不机械全记claim"},
                "holder": {"type": ["string", "null"], "description": "cognition的认知者或claim的说话人ID/@引用；node仍是被认知/被谈论的主体，可与holder不同；例如甲以为乙是教师：node乙、scope=cognition、holder甲；world不填"}, "source": {"type": "string"},
                "related_nodes": {"type": "array", "items": {"type": "string"}},
                "at_event": {"type": "string", "description": "属性披露锚点事件；事件地点须锚到支持地点证据的同一事件"},
            }, "additionalProperties": False}},
        "expirations": {"type": "array", "items": {"type": "object",
            "properties": {"record_id": {"type": "string"}, "node": {"type": "string"},
                           "property": {"type": "string"}, "value": {}, "at_event": {"type": "string"},
                           "valid_to": {"type": "object", "properties": {
                               "source_id": {"type": "string"}, "start": {"type": "integer"},
                               "event_id": {"type": "string"}}, "additionalProperties": False},
                           "reason": {"type": "string"}}, "additionalProperties": False}},
        "uncertainties": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "exclusions": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    }, "additionalProperties": False,
}


def _source_range(ops, source_id: str, start: int, end: int | None):
    source = ops.source(source_id)
    text = source["text"]
    if isinstance(start, bool) or not isinstance(start, int):
        raise ValueError("start必须是整数Unicode字符位置")
    if end is not None and (isinstance(end, bool) or not isinstance(end, int)):
        raise ValueError("end必须是整数Unicode字符位置")
    end = len(text) if end is None else end
    if not 0 <= start <= end <= len(text):
        raise ValueError("提取范围超出原文；start/end必须是原文Unicode字符位置")
    return source, text[start:end], start, end


def _prepare(ops, source_id: str, result: dict, start: int, end: int | None):
    from .extraction import build_extraction, parse_nodes

    snapshot = ops.capture_changes()
    reader = snapshot.reader
    source, selected, start, end = _source_range(reader, source_id, start, end)
    if (not isinstance(result, dict) or not isinstance(result.get("nodes"), list)
            or not isinstance(result.get("properties"), list)):
        raise ValueError("完整草稿需要nodes和properties数组")
    allowed_node_fields = set(RESULT_SCHEMA["properties"]["nodes"]["items"]["properties"])
    for index, row in enumerate(result["nodes"]):
        if not isinstance(row, dict):
            raise ValueError(f"nodes[{index}]必须是对象")
        if "closing_quote" in row and row.get("type") != "storyline":
            raise ValueError(f"nodes[{index}]仅主要故事使用closing_quote")
        expected_parent = {"scene": "big_event", "big_event": "storyline", "event": "scene", "action": "event"}.get(str(row.get("type") or ""))
        invalid_parent = {key for key in ("storyline", "big_event", "scene", "event") if key in row and key != expected_parent}
        if invalid_parent:
            raise ValueError(f"nodes[{index}]父级字段不适用于该类型：" + "、".join(sorted(invalid_parent)))
        unknown_node = set(row) - allowed_node_fields
        if unknown_node:
            raise ValueError(f"nodes[{index}]未知字段：" + "、".join(sorted(unknown_node)))
    unknown = set(result) - set(RESULT_SCHEMA["properties"])
    if unknown:
        raise ValueError("未知草稿字段：" + "、".join(sorted(unknown)))
    from copy import deepcopy

    from .extraction_refs import bind
    original = deepcopy(result)
    current_graph = reader.graph_snapshot()
    current_records = reader.property_records()
    definitions = {row['id']: row for row in current_records if row.get('kind') == 'property_definition'}
    for row in original['properties']:
        if isinstance(row, dict) and 'value_type' not in row and row.get('property') in definitions:
            row['value_type'] = definitions[row['property']]['value_type']
    data = parse_nodes(original, selected)
    for key in ("expirations", "uncertainties", "exclusions"):
        rows = result.get(key) or []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"{key}必须是对象数组")
        data[key] = rows
    data = bind(data, current_graph, current_records)
    issues = list(data.get("rejected") or []) + content_diagnostics(data, selected, source["title"])
    for kind in ("nodes", "properties"):
        for row in data[kind]:
            row["start"] += start
            if kind == "nodes":
                if row.get("closing_span"):
                    row["closing_span"]["start"] += start
                    row["closing_span"]["end"] += start
                for span in row.get("evidence_spans") or []:
                    span["start"] += start
                    span["end"] += start
    data.update(source_id=source_id, chapter=source["title"],
                slice_text=selected, slice_start=start, slice_end=end)
    built = build_extraction(reader, data)
    issues += list(built.get("skipped") or [])
    prepared = None
    diagnostics = [{"severity": "error", "code": "EXTRACTION_CONTENT",
                    "object_id": item.get("name"), "message": item.get("reason") or str(item)} for item in issues]
    if not issues and not built.get("empty"):
        prepared = ops.prepare_changes(built["objects"], built["property_records"], snapshot.revision, snapshot=snapshot)
        diagnostics += prepared.diagnostics
    valid = not any(row.get("severity") == "error" for row in diagnostics)
    outcome = {"valid": valid, "revision": snapshot.revision,
               "node_count": built.get("node_count", 0), "property_count": built.get("property_count", 0),
               "uncertainty_count": len(data["uncertainties"]), "diagnostics": diagnostics}
    return data, built, prepared, outcome


def _compact(outcome: dict) -> dict:
    result = {key: value for key, value in outcome.items() if key != "diagnostics"}
    diagnostics = outcome.get("diagnostics") or []
    actionable = [row for row in diagnostics if row.get("severity") in {"error", "warning"}]
    if actionable:
        result["diagnostics"] = [{key: row[key] for key in ("severity", "code", "object_id", "message")
                                  if row.get(key) is not None} for row in actionable]
    info_count = sum(row.get("severity") == "info" for row in diagnostics)
    if info_count:
        result["info_count"] = info_count
    return result


def preview(ops, source_id: str, result: dict, start: int = 0, end: int | None = None) -> dict:
    return _compact(_prepare(ops, source_id, result, start, end)[3])


def submit(ops, source_id: str, result: dict, start: int = 0, end: int | None = None) -> dict:
    from .conversation_session import record_business_action, use_chat

    with use_chat(ops) as chat:
        data, built, prepared, outcome = _prepare(ops, source_id, result, start, end)
        outcome["status"] = "rejected"
        if not outcome["valid"]:
            return _compact(outcome)
        if built.get("empty"):
            outcome["status"] = "unchanged"
            if built.get("node_ids"):
                outcome["node_ids"] = built["node_ids"]
            return _compact(outcome)
        note = {"purpose": "统一提取自动入库", "uncertainties": result.get("uncertainties") or [],
                "exclusions": result.get("exclusions") or [], "expirations": result.get("expirations") or []}
        committed = ops.commit_changes(prepared, json.dumps(note, ensure_ascii=False))
        outcome.update(status=committed["status"], revision=committed["revision"])
        if built.get("node_ids"):
            outcome["node_ids"] = built["node_ids"]
        try:
            record_business_action(ops, {"action": "automatic_extraction", "chat_id": chat["id"],
                                       "source_id": source_id, "result": outcome})
        except Exception as exc:
            outcome["feedback_error"] = {"type": type(exc).__name__, "message": str(exc)}
        return _compact(outcome)


def _organization_headings(text: str, title: str = "") -> list[dict]:
    """Identify whole heading lines; quoted mentions inside prose do not count."""
    headings = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        label = content.strip().lstrip("\ufeff")
        numbered = re.fullmatch(
            r"第[0-9０-９零〇一二三四五六七八九十百千万两]+[章卷部集节回篇]"
            r"(?:\s+[^。！？!?“”‘’「」\r\n]+)?", label,
        )
        if label and (label == title.strip() or numbered or re.match(r"^#{1,6}\s+\S", label)):
            headings.append({"text": label, "start": cursor, "end": cursor + len(content)})
        cursor += len(line)
    return headings

def _only_heading_evidence(quotes: list[str], text: str, headings: list[dict]) -> bool:
    if not quotes or not headings:
        return False
    for quote in quotes:
        offset = 0
        found = False
        while (start := text.find(quote, offset)) >= 0:
            found = True
            end = start + len(quote)
            if not any(row["start"] <= start and end <= row["end"] for row in headings):
                return False
            offset = start + max(1, len(quote))
        if not found:
            return False
    return True

def content_diagnostics(data: dict, text: str, title: str = "") -> list[dict]:
    """Check structural content boundaries without building records or writing."""
    rejected = []
    node_types = dict(data.get("reference_types") or {})
    node_types.update({node.get("name"): node.get("type") for node in data.get("nodes") or []})
    for prop in data.get("properties") or []:
        base = re.split(r"[（(]", str(prop.get("property") or ""), maxsplit=1)[0].strip()
        value = prop.get("value")
        if (base == "关系"
                and node_types.get(prop.get("node")) == "person"
                and isinstance(value, str) and node_types.get(value) == "person"):
            rejected.append({
                "name": f"{prop.get('node')}.{prop.get('property')}",
                "node": prop.get("node"), "property": prop.get("property"), "value": prop.get("value"),
                "reason": "人物关系的 property 需要具体关系性质，不能把分类名“关系”当作性质；"
                          "请依据原文判断，无法确定则说明存疑而非编造",
            })
    if text:
        headings = _organization_headings(text, title)
        for node in data.get("nodes") or []:
            if node.get("type") == "big_event" and _only_heading_evidence(
                node.get("quotes") or [], text, headings,
            ):
                rejected.append({
                    "name": node.get("display_name", node.get("name")), "type": "big_event",
                    "reason": "大事件引文只来自卷章组织标题，缺少正文发生过程证据",
                })
    return rejected
