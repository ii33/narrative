"""Pure, readable candidate previews for adapters using the operation layer.

The preview explains the current action contract. It neither judges whether a
quote supports an interpretation nor projects a candidate into formal storage.
"""

from collections.abc import Sequence
from hashlib import sha256

from .models import ChangeSet, Diagnostic
from .state import event_effects

KINDS = {
    "entity": "实体",
    "assertion": "断言",
    "event": "事件",
    "property": "属性定义",
    "state": "世界状态",
    "relation": "关系",
    "knowledge": "人物认知",
    "rule": "世界规则",
    "scene": "场景",
    "asset_binding": "资产绑定",
    "intervention": "干预",
    "creative_intent": "创作意图",
    "event_link": "事件关系解读",
    "storyline": "故事线解读",
    "classification": "审核分类",
    "artifact_observation": "产物观察",
    "artifact_acceptance": "产物验收",
}
PERSISTENCE = {
    "until_changed": "持续到下一次明确改变",
    "bounded": "仅在明确有效期内",
    "instant": "仅在该次观察／完成边界",
    "unknown": "延续范围未知",
}
ACQUISITION = {
    "told": "听说／被告知",
    "observed": "亲自观察",
    "read": "阅读",
    "inferred": "推断",
    "initial": "初始认知",
    "unknown": "获取方式未知",
}
ATTITUDES = {
    "informed": "获悉该说法",
    "knows": "知道",
    "believes": "相信",
    "suspects": "怀疑／推测",
    "denies": "否认",
    "unknown": "态度未知",
    "forgotten": "已遗忘",
}


def _rows(snapshot: dict, key: str) -> list[dict]:
    value = snapshot.get(key, [])
    return list(value.values()) if isinstance(value, dict) else list(value)


def _references(obj: dict) -> list[str]:
    refs: list[str] = []

    def visit(value, key=""):
        if isinstance(value, dict):
            for field, item in value.items():
                if field not in {"evidence", "properties"}:
                    visit(item, field)
        elif isinstance(value, list):
            for item in value:
                visit(item, key)
        elif isinstance(value, str) and (
            key.endswith("_id") or key.endswith("_ids") or key == "derived_from"
        ):
            if value != obj["id"] and value not in refs:
                refs.append(value)

    visit(obj)
    return refs


def build_candidate_preview(
    snapshot: dict, change: ChangeSet, diagnostics: Sequence[Diagnostic | dict] | None = None
) -> dict:
    """Explain each proposed object without mutation, model calls or state guesses.

    ``state_writes`` lists intended effects, not a successful state query. Missing
    references, preconditions, time or validation errors may prevent those writes.
    Only actual ``state.query`` / ``state.query_event`` results establish a query
    answer. Object IDs also serve as stable per-operation review IDs.
    """
    current = {
        obj["id"]: obj
        for table in ("entities", "assertions", "events", "records")
        for obj in _rows(snapshot, table)
    }
    proposed = [operation.model_dump() for operation in change.operations]
    final = current | {obj["id"]: obj for obj in proposed}
    sources = {obj["id"]: obj for obj in _rows(snapshot, "sources")}
    source_hashes: dict[str, str] = {}
    diagnostic_rows = [
        diagnostic.model_dump() if isinstance(diagnostic, Diagnostic) else dict(diagnostic)
        for diagnostic in diagnostics or []
    ]

    def name(object_id: str | None) -> str:
        if object_id is None:
            return "未指定"
        obj = final.get(object_id, {})
        return obj.get("name") or obj.get("title") or f"未解析对象 {object_id}"

    def property_name(property_id: str) -> str:
        if property_id.startswith("injury:"):
            return "伤势（" + property_id.removeprefix("injury:") + "）"
        return {"location": "位置", "held_by": "持有人", "outfit": "服装"}.get(
            property_id, name(property_id)
        )

    def value_label(obj: dict) -> str:
        if obj.get("value_entity_id") is not None:
            return name(obj["value_entity_id"])
        value = obj.get("value")
        if value is None:
            return "未知"
        if type(value) is bool:
            return "是" if value else "否"
        return str(value)

    def statement(assertion_id: str) -> str:
        assertion = final.get(assertion_id, {})
        if assertion.get("kind") == "assertion":
            return (
                f"{name(assertion['subject_id'])} · {assertion['predicate']}："
                f"{value_label(assertion)}"
            )
        return name(assertion_id)

    def evidence_rows(obj: dict) -> list[dict]:
        rows = []
        for evidence in obj.get("evidence", []):
            source = sources.get(evidence["source_id"])
            exact = None
            before, after = "", ""
            if source:
                text = source["text"]
                sid = source["id"]
                if sid not in source_hashes:
                    source_hashes[sid] = sha256(text.encode("utf-8")).hexdigest()
                start, end = evidence["start"], evidence["end"]
                exact = (
                    source.get("hash") == evidence["source_hash"] == source_hashes[sid]
                    and 0 <= start < end <= len(text)
                    and text[start:end] == evidence["quote"]
                )
                before = text[max(0, start - 45) : start]
                after = text[end : end + 45]
            rows.append(
                {
                    **evidence,
                    "source_title": source["title"] if source else "原文未载入",
                    "exact": exact,
                    "before": before,
                    "after": after,
                }
            )
        return rows

    cards = []
    for obj in proposed:
        kind = obj["kind"]
        warnings: list[dict] = []

        def warn(code: str, message: str, target=warnings):
            if not any(item["code"] == code for item in target):
                target.append({"code": code, "message": message})

        subject = obj.get("subject_id") or obj.get("actor_id")
        title = (
            obj.get("name")
            or obj.get("title")
            or (f"{name(subject)} · {obj['predicate']}" if obj.get("predicate") else KINDS[kind])
        )
        ownership = "资料记录"
        summary = []
        writes = []
        knowledge = []
        evidence = evidence_rows(obj)
        if any(row["exact"] is False for row in evidence):
            warn("EVIDENCE_MISMATCH", "引文、位置或原文版本不匹配；请修正证据后再接受。")
        if obj.get("status", "accepted") != "accepted":
            warn("NON_ACCEPTED", "本对象有争议或已取代，保留记录但不用于确定状态。")

        if kind == "entity":
            ownership = "实体身份"
            entity_type = {"person": "人物", "location": "地点", "item": "物品", "other": "其他"}[
                obj["entity_type"]
            ]
            summary.append(
                f"{obj['name']}；类型：{entity_type}；别名：{'、'.join(obj['aliases']) or '无'}"
            )
        elif kind == "assertion":
            scope = obj["scope"]
            ownership = (
                "世界事实"
                if scope == "world"
                else f"{name(obj.get('holder_id'))}的{'主张' if scope == 'claim' else '认知'}"
            )
            summary.append(f"{name(obj['subject_id'])} · {obj['predicate']}：{value_label(obj)}")
            certainty = {
                "unknown": "未知",
                "certain": "确定",
                "suspects": "推测",
                "denies": "否认",
            }[obj["certainty"]]
            summary.append(f"归属：{ownership}；确定性：{certainty}")
            if scope != "world":
                warn("CLAIM_NOT_WORLD", "此内容属于人物主张／认知，不会因此成为世界事实。")
            else:
                warn(
                    "WORLD_SUPPORT", "将作为世界事实保存；请核对原句是否只是对话、书信或人物推测。"
                )
        elif kind == "event":
            action = obj["action"]
            ownership = "叙事事件" if action == "narrative" else "世界事件"
            if action != "narrative":
                summary.append(
                    f"{name(obj.get('actor_id'))} → {name(obj.get('target_id'))}；"
                    f"物品：{name(obj.get('item_id'))}；地点：{name(obj.get('location_id'))}"
                )
            if obj.get("narrative_type"):
                summary.append(f"叙事类型：{obj['narrative_type']}")
            if obj.get("participants"):
                summary.append(
                    "参与者："
                    + "、".join(
                        f"{name(item['entity_id'])}（{item['role']}）"
                        for item in obj["participants"]
                    )
                )
            if action == "narrative":
                warn("NARRATIVE_NO_WORLD_EFFECT", "叙事事件记录发生与参与者，不自动确立位置、持有或人物认知。")
            effects = event_effects(obj)
            if action == "move":
                warn(
                    "MOVE_DESTINATION",
                    "移动会把行动人物的位置写成所填地点。请确认这是到达处，而非离开处。",
                )
                if obj.get("target_id"):
                    warn(
                        "MOVE_TARGET_IGNORED",
                        "移动中的接收人物字段不会移动该人物；同行请另建事件或添加明确的位置效果。",
                    )
            elif action == "transfer":
                warn(
                    "TRANSFER_CUSTODY",
                    "交接会把物品持有人写成接收人物。“放在某处”不等于交给某人持有，请对照原句。",
                )
            elif action in {"injure", "treat"}:
                warn(
                    "WORLD_INJURY",
                    "此动作影响世界伤势；人物讲述或书信自述的伤病，应先区分主张与事实。",
                )
                if action == "treat" and obj.get("state_value") is None:
                    summary.append("处理伤势但无明确结果：伤势清为未知，不代表康复。")
            if action in {"observe", "tell"} and obj.get("assertion_id"):
                holder = obj.get("target_id") if action == "tell" else obj.get("actor_id")
                acquisition = "told" if action == "tell" else "observed"
                knowledge.append(
                    {
                        "holder_id": holder,
                        "holder_name": name(holder),
                        "assertion_id": obj["assertion_id"],
                        "attitude": obj["knowledge_attitude"],
                        "acquisition": acquisition,
                        "summary": f"{name(holder)}：{ATTITUDES[obj['knowledge_attitude']]}；{ACQUISITION[acquisition]}；内容：{statement(obj['assertion_id'])}",
                    }
                )
                warn(
                    "KNOWLEDGE_NOT_TRUTH",
                    "事件记录的是人物获取某条说法及其态度，不会证明该说法为真。",
                )
                if action == "tell":
                    warn(
                        "TELL_CHANNEL",
                        "告知会派生 told（听说）路径；书信阅读请用无主张引用的观察锚点，加显式 read 认知记录。写信本身不代表收信者已读。",
                    )
                    if any(
                        other.get("kind") == "knowledge"
                        and other.get("holder_id") == holder
                        and other.get("assertion_id") == obj["assertion_id"]
                        and (
                            other.get("event_id")
                            or (other.get("validity") or {}).get("start_event_id")
                        )
                        == obj["id"]
                        and other.get("acquisition") != "told"
                        for other in final.values()
                    ):
                        warn(
                            "KNOWLEDGE_CHANNEL_MISMATCH",
                            "同一锚点还有不同获取方式的认知记录；请核对听说与阅读路径，不会自动合并成同一种。",
                        )
            elif action == "tell":
                warn(
                    "TELL_NO_CONTENT",
                    "未指定主张引用，告知事件不会建立接收人物获悉某条内容的认知。",
                )
            if not effects and not knowledge:
                warn(
                    "EVENT_NO_EFFECT",
                    "本事件仅保存事件和证据，没有明确状态写入；标题中的同行、死亡或杀伤结果不会自动生效。",
                )
            if obj["preconditions"]:
                warn(
                    "EVENT_PRECONDITIONS",
                    "下列为意图写入；前置条件未满足或未知时，查询可能不采用效果。",
                )
            if obj["time_kind"] == "unknown":
                warn("TIME_UNRESOLVED", "故事时间与先后未定；不能由引文位置或标题猜测分钟。")
            summary.append(
                "时间："
                + {
                    "unknown": "未定",
                    "relative": "相对先后",
                    "point": "明确时间点",
                    "interval": "明确区间",
                }[obj["time_kind"]]
                + (f"；在 {name(obj['after_event_id'])} 之后" if obj.get("after_event_id") else "")
            )
            for effect in effects:
                writes.append(dict(effect))
        elif kind == "state":
            ownership = "世界状态"
            writes.append(dict(obj))
            if obj["property_id"].startswith("injury:"):
                warn(
                    "WORLD_INJURY",
                    "将提出世界伤势状态；书信自述或人物讲述不自动支持这一世界事实，请核对是否仅应保留 claim。",
                )
            validity = obj["validity"]
            if validity.get("start") is None and not validity.get("start_event_id"):
                warn(
                    "STATE_UNANCHORED",
                    "状态没有明确起始时间／事件锚点；按分钟或事件查询可能仍为未知。",
                )
        elif kind == "knowledge":
            ownership = f"{name(obj['holder_id'])}的人物认知"
            summary.append(
                f"{name(obj['holder_id'])}：{ATTITUDES[obj['attitude']]}；"
                f"{ACQUISITION[obj['acquisition']]}；内容：{statement(obj['assertion_id'])}"
            )
            warn("KNOWLEDGE_NOT_TRUTH", "人物获悉、相信或怀疑某条说法，不会把说法提升为世界事实。")
        elif kind == "relation":
            ownership = "关系记录"
            summary.append(
                f"{name(obj['subject_id'])} · {obj['predicate']} · {name(obj['object_id'])}"
            )
        elif kind == "event_link":
            ownership = "事件关系解读"
            summary.append(
                f"{name(obj['source_event_id'])} → {name(obj['target_event_id'])}；"
                f"关系：{obj['link_type']}；说明：{obj.get('description') or '未填写'}"
            )
            warn("INTERPRETATION_NOT_WORLD", "事件关系是有证据的解读，不自动建立故事时间顺序或世界状态。")
        elif kind == "storyline":
            ownership = "故事线解读"
            summary.append(
                f"{obj['title']}；类型：{obj['line_type']}；"
                "事件：" + "、".join(name(event_id) for event_id in obj["event_ids"])
            )
            if obj.get("description"):
                summary.append(obj["description"])
            warn("INTERPRETATION_NOT_WORLD", "主线／支线归属是有证据的解读，不自动成为世界事实。")
        elif kind == "classification":
            ownership = "分类解读"
            summary.append(
                f"{name(obj['target_id'])}；目标：{obj['target_kind']}；分类：{obj['category']}"
            )
            if obj.get("note"):
                summary.append(obj["note"])
            warn("INTERPRETATION_NOT_WORLD", "人物特点、关系或情节分类是有证据的解读，不自动成为世界事实。")
        else:
            summary.append(KINDS[kind] + "；详情见结构化字段。")

        rendered_writes = []
        for effect in writes:
            prop = effect["property_id"]
            definition = final.get(prop, {})
            persistence = (
                effect.get("persistence")
                or definition.get("persistence")
                or ("until_changed" if prop in {"location", "held_by"} else "unknown")
            )
            if effect.get("valid_until") is not None:
                persistence = "bounded"
            clear = effect.get("operation") == "clear"
            row = {
                **effect,
                "subject_name": name(effect["subject_id"]),
                "property_name": property_name(prop),
                "value_name": "未知（清除）" if clear else value_label(effect),
                "persistence": persistence,
                "persistence_label": PERSISTENCE[persistence],
                "effect_status": "意图写入，需通过校验与时间／前置条件",
            }
            row["summary"] = (
                f"{row['subject_name']} · {row['property_name']} → {row['value_name']}；{row['persistence_label']}"
            )
            rendered_writes.append(row)
            if persistence == "until_changed":
                warn(
                    "PERSISTENCE_UNTIL_CHANGED",
                    "状态将持续到下一次明确改变；请核对原文是否支持延续，跨场景缺失变化不能由系统补造。",
                )
            if prop == "outfit":
                warn(
                    "OUTFIT_SINGLE_VALUE",
                    "当前服装属性是单值；多件同时穿着不能当作多个互斥服装值直接写入。",
                )
            entity = final.get(effect["subject_id"], {})
            if prop in {"location", "outfit"} or prop.startswith("injury:"):
                if entity and entity.get("entity_type") != "person":
                    warn(
                        "BUILTIN_DOMAIN_LIMIT",
                        "此内置属性只支持人物；不会把动物或物品自动改成人物。需使用已批准的领域属性或保留为断言。",
                    )

        refs = _references(obj)
        missing = [
            ref
            for ref in refs
            if ref not in final
            and ref not in {"location", "held_by", "outfit"}
            and not ref.startswith("injury:")
        ]
        if missing:
            warn(
                "MISSING_REFERENCE",
                "存在未解析引用：" + "、".join(missing) + "。删除依赖对象后必须重新核验。",
            )
        cards.append(
            {
                "operation_id": obj["id"],
                "kind": kind,
                "kind_label": KINDS[kind],
                "title": title,
                "ownership": ownership,
                "summary": summary,
                "evidence": evidence,
                "state_writes": rendered_writes,
                "knowledge_writes": knowledge,
                "warnings": warnings,
                "diagnostics": [
                    item for item in diagnostic_rows if item.get("object_id") == obj["id"]
                ],
                "dependencies": refs,
                "before": current.get(obj["id"]),
                "after": obj,
                "semantic_status": "尚需人工核对原句支持程度",
            }
        )
    return {
        "change_id": change.id,
        "cards": cards,
        "warnings": [
            {
                "code": "SEMANTIC",
                "message": "证据定位和格式通过不代表语义正确。请逐项核对主张归属、目的地、持有人、持续性和认知路径。",
            }
        ],
        "diagnostics": diagnostic_rows,
        "has_errors": any(item.get("severity") == "error" for item in diagnostic_rows),
        "coverage": "预览仅解释候选的明确字段和当前动作效果；不自动纠正文意，不证明故事语义正确，不写入正式资料。",
        "projection_note": "状态写入为意图预览；实际状态仍由查询引擎根据全图、时间、有效期和前置条件计算。",
    }
