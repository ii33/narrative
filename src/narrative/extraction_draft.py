"""Deterministic draft validation and automatic apply tools for the conversation agent."""

from __future__ import annotations

import re

DRAFT_VERSION = "conversation-draft/16"

RESULT_SCHEMA = {
    "type": "object", "required": ["nodes", "properties"],
    "description": "引用优先已有正式节点ID或@1起原始nodes完整数组索引；名称仅在唯一时可解析，同名须ID/@。",
    "properties": {
        "nodes": {"type": "array", "items": {"type": "object", "required": ["name", "type"],
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string", "enum": ["person", "group", "item", "location", "storyline", "big_event", "scene", "event", "action"],
                    "description": "event记录完整发生，通常包含同一叙事目标下的过程与直接结果；不把准备、动作链或收尾单独改标event。真正独立的新展开才另分事件。action仅在明确请求动作提取时使用。"},
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
        "properties": {"type": "array", "items": {"type": "object", "required": ["node", "property", "at_event", "op", "quotes"],
            "properties": {
                "node": {"type": "string", "description": "属性主体；身份、关系或作用角色中为参照方A"}, "property": {"type": "string", "description": "关系描述对方相对node的身份、关系或作用中的角色；node=A,property=父亲,value=B表示B是A的父亲；事件地点使用属性名‘地点’并引用地点节点"},
                "op": {"type": "string", "enum": ["set", "add", "remove", "clear", "unknown"], "description": "在at_event处建立/设置、增添、移除、明确清空或变为未知；不填写有效期"},
                "value": {"description": "node/either节点引用表示被指向的对方ID；普通string/any值保持字面内容；事件地点的value引用location节点"}, "value_type": {"type": "string", "enum": ["string", "number", "boolean", "node", "either", "any"], "description": "节点引用用node/either；事件地点使用node，string/any即使内容像P编号也是字面值"},
                "quotes": {"type": "array", "items": {"type": "string"}},
                "cardinality": {"type": "string", "enum": ["one", "many"], "description": "新定义默认one；多值状态显式many且value为数组。已有定义省略沿用，显式冲突拒绝，须显式修改定义"},
                "symmetric": {"type": "boolean", "description": "该关系性质对称时显式true；单向false，不按名称猜测"},
                "category": {"type": "string"}, "scope": {"type": "string", "enum": ["world", "cognition", "claim"], "description": "world为原文支持的客观事实；事件地点为world；cognition为某人物所知所信/主观判断，claim为人物言说主张，二者须给holder；对白可有客观事实依据，不机械全记claim"},
                "holder": {"type": ["string", "null"], "description": "cognition的认知者或claim的说话人ID/@引用；node仍是被认知/被谈论的主体，可与holder不同；例如甲以为乙是教师：node乙、scope=cognition、holder甲；world不填"}, "source": {"type": "string"},
                "related_nodes": {"type": "array", "items": {"type": "string"}},
                "at_event": {"type": "string", "description": "必须给本次建立/变更/失效所在事件ID或@引用；程序按事件顺序推状态，sequence由已定位引文生成"},
            }, "additionalProperties": False}},
        "uncertainties": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "exclusions": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    }, "additionalProperties": False,
}

_PROPERTY_DRAFT_SCHEMA = RESULT_SCHEMA['properties']['properties']['items']
_PROPERTY_DRAFT_SCHEMA['oneOf'] = [
    {'properties': {'op': {'enum': ['set', 'add', 'remove']}}, 'required': ['value']},
    {'properties': {'op': {'enum': ['clear', 'unknown']}}, 'not': {'required': ['value']}},
]


def submission_input_schema() -> dict:
    """The exact public draft-call schema; no storage-layer fields or tool import."""
    from copy import deepcopy
    return {"type": "object", "additionalProperties": False, "required": ["source_id", "result"],
        "properties": {
            "source_id": {"type": "string", "description": "当前工程原文ID，放在外层，不能写进result"},
            "result": deepcopy(RESULT_SCHEMA),
            "start": {"type": "integer", "description": "本批实际原文范围的全局Unicode起点，放外层；默认0"},
            "end": {"type": "integer", "description": "本批实际原文范围的全局Unicode终点，不包含；放外层，默认原文末尾"}}}


def submission_parameters() -> dict:
    """Tool binding metadata derives from that same authoritative JSON schema."""
    schema = submission_input_schema()
    return {key: {**value, "required": key in schema["required"]}
            for key, value in schema["properties"].items()}


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
    fields = set(_PROPERTY_DRAFT_SCHEMA['properties'])
    for index, row in enumerate(result['properties']):
        if not isinstance(row, dict) or set(row)-fields:
            raise ValueError(f'properties[{index}]字段无效；只使用事件变更合同，不接受有效期或模型sequence')
        if not isinstance(row.get('at_event'), str) or not row['at_event'].strip() or not isinstance(row.get('op'), str) or row.get('op') not in {'set', 'add', 'remove', 'clear', 'unknown'}:
            raise ValueError(f'properties[{index}]必须明确at_event与有效op')
        if ('value' in row) != (row['op'] in {'set', 'add', 'remove'}):
            raise ValueError(f'properties[{index}]set/add/remove须有value，clear/unknown不能有value')
    unknown = set(result) - set(RESULT_SCHEMA["properties"])
    if unknown:
        raise ValueError("未知草稿字段：" + "、".join(sorted(unknown)))
    from copy import deepcopy

    from .extraction_refs import bind
    original = deepcopy(result)
    current_graph = reader.graph_snapshot()
    current_records = reader.property_records()
    definitions = {row['id']: row for row in current_records if row.get('kind') == 'property_definition'}
    # Resolve omitted type information from explicit definitions anywhere in this batch.
    # A later clear/unknown must not turn an earlier node reference into a string.
    for row in original['properties']:
        if isinstance(row, dict) and row.get('property') not in definitions and row.get('value_type'):
            definitions[row['property']] = row
    for row in original['properties']:
        if isinstance(row, dict) and 'value_type' not in row and row.get('property') in definitions:
            row['value_type'] = definitions[row['property']]['value_type']
        if isinstance(row, dict) and 'cardinality' not in row and row.get('property') in definitions:
            row['cardinality'] = definitions[row['property']].get('cardinality', 'one')
    data = parse_nodes(original, selected)
    for key in ("uncertainties", "exclusions"):
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
    from .extraction_staging import submit as stage_submit
    return stage_submit(ops, source_id, result, start, end)


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
