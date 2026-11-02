"""Evidence-bound graph proposals and human review for v5 projects."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from pydantic import ValidationError

from .graph_models import (
    GRAPH_OBJECTS,
    GraphObject,
    parse_objects,
)
from .graph_store import GRAPH_SCHEMA_VERSION, GraphStore
from .property_store import (
    PROPERTY_CONTRACT_VERSION,
    PROPERTY_EFFECT_OPS,
    PROPERTY_RECORD_KINDS,
    PROPERTY_STATE_CELL_KINDS,
)


def _issue(code: str, message: str, object_id: str | None = None, severity: str = "error") -> dict:
    return {"code": code, "severity": severity, "object_id": object_id, "message": message}


def _as_dicts(objects: list[GraphObject]) -> list[dict]:
    return [item.model_dump(mode="json", exclude_none=True) for item in objects]


def _field_contracts() -> dict:
    """Publish Pydantic enum/default facts without duplicating the full schema."""
    definitions = GRAPH_OBJECTS.json_schema().get("$defs", {})

    def enum_values(field: dict) -> list | None:
        if "$ref" in field:
            return enum_values(definitions[field["$ref"].rsplit("/", 1)[-1]])
        if "enum" in field:
            return field["enum"]
        if "const" in field:
            return [field["const"]]
        choices = field.get("anyOf", [])
        values = [value for choice in choices for value in (enum_values(choice) or [])]
        return values or None

    contracts = {}
    for model_name, definition in definitions.items():
        properties = definition.get("properties", {})
        name = properties.get("kind", {}).get("const", model_name)
        enums, defaults = {}, {}
        for field_name, field in properties.items():
            values = enum_values(field)
            if values is not None:
                enums[field_name] = values
            elif "items" in field and (items := enum_values(field["items"])) is not None:
                enums[field_name + "[]"] = items
            if "default" in field:
                defaults[field_name] = field["default"]
        if enums or defaults:
            contracts[name] = {"enums": enums, "defaults": defaults}
    return contracts


class GraphService:
    def __init__(self, store: GraphStore):
        self.store = store

    def normalize(self, objects: list[dict]) -> tuple[list[dict], list[dict]]:
        """Parse and normalize graph objects without writing anything."""
        return self._normalized(objects)

    def import_source(self, title: str, text: str) -> str:
        if not title.strip() or not text.strip():
            raise ValueError("原文标题和正文不能为空")
        source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self.store.connect(write=True) as db:
            prior = db.execute(
                "SELECT id FROM sources WHERE title=? AND hash=?", (title.strip(), source_hash)
            ).fetchone()
            if prior:
                return prior[0]
            saved = db.execute("SELECT value FROM meta WHERE key='source_sequence'").fetchone()
            existing = [int(row[0][3:]) for row in db.execute("SELECT id FROM sources")
                        if row[0].startswith("SRC") and row[0][3:].isdigit()]
            sequence = max([int(saved[0]) if saved else 0, *existing]) + 1
            source_id = f"SRC{sequence}"
            db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('source_sequence',?)", (str(sequence),))
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]) + 1
            db.execute(
                "INSERT INTO sources(id,title,text,hash,imported_revision) VALUES (?,?,?,?,?)",
                (source_id, title.strip(), text, source_hash, revision),
            )
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (str(revision),))
            orders = [json.loads(row[0]) for row in db.execute("SELECT data FROM objects WHERE kind='source_order'")]
            ordinal = max((row['ordinal'] for row in orders if row.get('context_id') == 'canon'), default=-1) + 1
            order = {'kind': 'source_order', 'id': 'source-order-' + source_id,
                     'context_id': 'canon', 'source_id': source_id, 'ordinal': ordinal}
            db.execute('INSERT INTO objects(id,kind,data,accepted_revision) VALUES (?,?,?,?)',
                       (order['id'], order['kind'], json.dumps(order, ensure_ascii=False), revision))


        return source_id

    def locate(self, source_id: str, quotes: list[str], *, limit_per_quote: int = 20) -> list[dict]:
        from .source_location import locate_text
        return locate_text(self.store.source(source_id)["text"], quotes, limit_per_quote)

    def schema(self) -> dict:
        property_fields = {
            "property_definition": {
                "required": ["id", "value_type", "cardinality"],
                "optional": ["target_node_types", "evidence", "semantic", "symmetric"],
            },
            "property_value": {
                "required": ["node_id", "property_id", "value", "event_id"],
                "optional": ["id", "anchor_basis", "applies_to", "scope", "holder_id",
                             "disclosure", "evidence", "valid_from", "valid_to", "category"],
            },
        }
        return {
            "version": GRAPH_SCHEMA_VERSION,
            "schema_version": GRAPH_SCHEMA_VERSION,
            "contract_version": "graph-nesting/11",
            "property_contract_version": PROPERTY_CONTRACT_VERSION,
            "item_possession_rule": "活动物品须有当前world持有者：合法节点或显式无/未知。定义semantic=item_holder，默认属性名持有者；认知holder_id不替代。",
            "state_storage": "节点只存当前版本；属性值以valid_from/valid_to自身区间表示状态。新状态关闭旧值区间再追加；录入错误用correct/delete直接修正。",
            "property_interval": {"shape": {"source_id": "原文ID", "start": "Unicode坐标", "event_id": "可选锚点"},
                                  "bounds": "[valid_from,valid_to)，valid_to=null表示继续有效"},
            "rollback": "rollback_to(source_id,start,expected_revision,reason)保留该点，删除后续并清理引用；不重放节点旧版本。",
            "event_scene_rule": "活动事件须经包含路径归属同语境活动场景；动作必须直属恰好一个同语境活动事件，不能含子项。events不得成环。",
            "action_rule": "action按需提取，有独立A编号，支持披露、引文、参与者和可选发生区间；不由程序拆分所有事件。",
            "property_identifiers": "新取值可省id由程序分配PVn；已有取值更新用其现有id；定义id仍为语义属性名。",
            "property_record_kinds": list(PROPERTY_RECORD_KINDS),
            "property_record_fields": property_fields,
            "property_records": property_fields,
            "property_effect_ops": list(PROPERTY_EFFECT_OPS),
            "property_state_cells": list(PROPERTY_STATE_CELL_KINDS),
            "property_value_types": ["string", "number", "boolean", "node", "either", "any"],
            "property_cardinalities": ["one", "many"],
            "property_symmetry": "property_definition.symmetric为严格布尔值，默认false；true表示关系本身对称，不补写反向取值。",
            "extraction_submission": {
                "preview": "Operations.preview_extraction(source_id, result, start, end)",
                "submit": "Operations.submit_extraction(source_id, result, start, end)",
                "behavior": "同一对话任务提取与自审；程序校验完整图与属性草稿后自动入库，并保留内部审计。",
                "records": list(PROPERTY_RECORD_KINDS),
            },
            "property_evidence": {
                "shape": {
                    "source_id": "an accepted source id in this project",
                    "quote": "an exact substring of that source text",
                },
                "exactly": ["source_id", "quote"],
                "quote_rule": "the quote must occur exactly once in the accepted source text; unique exact substring only",
            },
            "object_kinds": ["node", "context", "timeline", "source_order"],
            "node_types": [
                "person", "item", "location", "event", "action", "scene", "big_event", "storyline", "group", "other"
            ],
            "time_bound_states": ["known", "unknown", "unbounded"],
            "validation": "引文、结构及预检通过后经 Operations 自动采用；模型存疑和程序诊断保留于审计。",
            "fields": {
                "node": {"required": ["kind", "id", "node_type", "name"], "optional": ["aliases", "subtype", "context_id", "identity_note", "occurrence", "disclosure", "completion", "evidence", "evidence_spans", "retracted", "participants", "predecessors", "successors", "events", "source_marks"]},
                "context": {"required": ["kind", "id", "label", "context_type"], "optional": ["parent_id", "holder_id"]},
                "timeline": {"required": ["kind", "id", "label"], "optional": ["context_id", "calendar"]},
                "source_order": {"required": ["kind", "id", "source_id", "ordinal"], "optional": ["context_id"]},
            },
            "rules": [
                "property_* 记录的 evidence 恰为 {source_id, quote}：source_id 须为已接受来源，quote 须在该来源原文中恰好出现一次；不得引用图证据对象ID或私有句柄。",
            ],
            "examples": {
                "node": {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
                "property_definition": {
                    "kind": "property_definition", "id": "持有者", "value_type": "either",
                    "cardinality": "one",
                },
                "property_value": {
                    "kind": "property_value",
                    "node_id": "key", "property_id": "持有者", "value": "p1", "scope": "world",
                    "event_id": "e1", "anchor_basis": "model",
                    "disclosure": {"source_id": "s1", "start": 120},
                    "evidence": {"source_id": "s1", "quote": "原文逐字引文"},
                },
                "evidence_link": {
                    "evidence_id": "q1", "role": "supports",
                },
                "evidence": {
                    "kind": "evidence", "id": "q1", "source_id": "source-id",
                    "start": 0, "end": 2,
                    "quote": "甲说",
                },
                "source_order": {"kind": "source_order", "id": "order1", "source_id": "source-id", "ordinal": 1},
                "event": {"kind": "node", "id": "e1", "node_type": "event", "name": "甲送别乙", "occurrence": {"start": {"state": "unknown"}, "end": {"state": "unknown"}}},
                "time_ref": {
                    "timeline_id": "story", "form": "event_boundary",
                    "event_id": "e1", "boundary": "end",
                },
                "validity": {
                    "start": {"state": "unknown"}, "end": {"state": "unbounded"}
                },
            },
        }

    def _normalized(self, objects: list[dict]) -> tuple[list[dict], list[dict]]:
        try:
            parsed = parse_objects(objects)
        except ValidationError as exc:
            return [], [_issue("FORMAT", item["msg"], ".".join(map(str, item["loc"]))) for item in exc.errors()]
        return _as_dicts(parsed), []

    def precheck(self, objects: list[dict], expected_revision: int, *, allow_cross_family: bool = False,
                 property_records: list[dict] | None = None,
                 _snapshot: dict | None = None, _accepted_properties: list[dict] | None = None) -> list[dict]:
        normalized, issues = self._normalized(objects)
        if issues:
            return issues
        snapshot = _snapshot if _snapshot is not None else self.store.snapshot()
        if expected_revision != snapshot["revision"]:
            issues.append(_issue("REVISION", "正式资料版本已变化，请刷新后重新预检"))
        if not normalized:
            issues.append(_issue("EMPTY", "候选至少须包含一条操作"))
            return issues
        ids = [item["id"] for item in normalized]
        if len(ids) != len(set(ids)):
            issues.append(_issue("DUPLICATE_ID", "同一候选内对象 ID 不得重复"))
        current = {item["id"]: item for item in snapshot["objects"]}
        merged = current | {item["id"]: item for item in normalized}
        from .containment_refs import event_scene_issues, storyline_issues

        issues.extend(event_scene_issues(list(merged.values())))
        issues.extend(storyline_issues(list(merged.values()), snapshot))
        from .item_possession import item_possession_issues
        from .property_store import PropertyStore

        accepted_properties = (_accepted_properties if _accepted_properties is not None else
                               PropertyStore(self.store).accepted_records())
        issues.extend(item_possession_issues(list(merged.values()), accepted_properties + (property_records or [])))
        groups: dict[str, dict[str, dict]] = defaultdict(dict)
        for item in merged.values():
            groups[item["kind"]][item["id"]] = item
        sources = {source["id"]: source for source in snapshot["sources"]}

        def exists(kind: str, target: str | None, owner: str, field: str) -> dict | None:
            obj = merged.get(target or "")
            if not obj or obj["kind"] != kind:
                issues.append(_issue("REF", f"{field}须引用已有或本批 {kind}", owner))
                return None
            return obj

        def check_time(ref: dict | None, owner: str) -> None:
            if not ref:
                return
            exists("timeline", ref["timeline_id"], owner, "timeline_id")
            if ref["form"] == "event_boundary":
                event = exists("node", ref["event_id"], owner, "event_id")
                if event and event["node_type"] not in {"event", "action"}:
                    issues.append(_issue("TIME_REF", "发生边界须引用事件或动作节点", owner))

        def check_extent(extent: dict | None, owner: str) -> None:
            if not extent:
                return
            if extent["kind"] == "point":
                check_time(extent["point"], owner)
                return
            for bound in (extent["start"], extent["end"]):
                check_time(bound.get("ref"), owner)
            start, end = extent["start"], extent["end"]
            if start["state"] == end["state"] == "known":
                left, right = start["ref"], end["ref"]
                if left["form"] == right["form"] == "number" and left["timeline_id"] == right["timeline_id"] and left["number"] > right["number"]:
                    issues.append(_issue("TIME_RANGE", "故事有效期终点早于起点", owner))

        for item in normalized:
            obj_id, kind = item["id"], item["kind"]
            previous = current.get(obj_id)
            if previous and previous["kind"] != kind:
                issues.append(_issue("ID_KIND", "对象 ID 不可跨类型复用", obj_id))
            if _snapshot is None and previous and kind == "source_order" and previous != item:
                issues.append(_issue("SOURCE_ORDER_IMMUTABLE", "已登记阅读顺序不可改写；请使用新的语境", obj_id))
            if _snapshot is None and previous and obj_id in {"canon", "story"} and previous != item:
                issues.append(_issue("INFRA_IMMUTABLE", "正篇语境和故事时间轴标识不可修订", obj_id))
            if kind == "node":
                exists("context", item["context_id"], obj_id, "context_id")
                check_extent(item.get("occurrence"), obj_id)
                if item.get("participants") and item["node_type"] not in {"event", "action"}:
                    issues.append(_issue("PARTICIPANTS", "只有事件和动作记录参与者", obj_id))
                if item.get("events") and item["node_type"] not in {"event", "scene", "big_event", "storyline"}:
                    issues.append(_issue("COMPOSITE", "只有主要故事、大事件、场景、事件可以包含子项", obj_id))
                for child in item.get("events") or []:
                    target = merged.get(child)
                    if not target or target["kind"] != "node":
                        issues.append(_issue("REF", "子事件须引用已有或本批节点", obj_id))
                        continue
                    if target.get("node_type") not in {"event", "scene", "action", "big_event"}:
                        issues.append(_issue("COMPOSITE", "子项须是大事件、场景、事件或动作，并遵循五层直属关系", obj_id))
                    if child == obj_id:
                        issues.append(_issue("COMPOSITE", "节点不能包含自己", obj_id))
                if (item.get("predecessors") or item.get("successors")) and item["node_type"] != "event":
                    issues.append(_issue("EVENT_CHAIN", "只有事件节点记录前驱与后继", obj_id))
                for field in ("predecessors", "successors"):
                    for neighbour in item.get(field) or []:
                        target = merged.get(neighbour)
                        if not target or target["kind"] != "node":
                            issues.append(_issue("REF", f"{field} 须引用已有或本批节点", obj_id))
                            continue
                        if target.get("node_type") != "event":
                            issues.append(_issue("EVENT_CHAIN", f"{field} 只能指向事件节点", obj_id))
                        if neighbour == obj_id:
                            issues.append(_issue("EVENT_CHAIN", "事件不能以自己为前驱或后继", obj_id))
                for participant in item.get("participants") or []:
                    target = merged.get(participant)
                    if not target or target["kind"] != "node":
                        issues.append(_issue("REF", "参与者须引用已有或本批节点", obj_id))
                        continue
                    if target.get("node_type") not in {"person", "group", "item", "location"}:
                        issues.append(_issue("PARTICIPANTS", "参与者须是人物、群体、物品或地点", obj_id))
                node_evidence = item.get("evidence")
                if node_evidence is not None:
                    source_id = node_evidence.get("source_id")
                    quote = node_evidence.get("quote")
                    if source_id not in sources:
                        issues.append(_issue("SOURCE", "节点证据来源不存在", obj_id))
                    elif not isinstance(quote, str) or not quote:
                        issues.append(_issue("EVIDENCE", "节点证据须有原文引文", obj_id))
                    else:
                        text = sources[source_id].get("text")
                        if text is None:
                            text = self.store.source(source_id)["text"]
                        if quote not in text:
                            issues.append(_issue("EVIDENCE", "节点引文不是原文子串", obj_id))
                for span in item.get("evidence_spans") or []:
                    source_id = span["source_id"]
                    if source_id not in sources:
                        issues.append(_issue("SOURCE", "节点证据跨度来源不存在", obj_id))
                        continue
                    text = sources[source_id].get("text")
                    if text is None:
                        text = self.store.source(source_id)["text"]
                    if span["end"] > len(text) or text[span["start"]:span["end"]] != span["quote"]:
                        issues.append(_issue("EVIDENCE_SPAN", "节点证据跨度与原文位置不符", obj_id))
                disclosure = item.get("disclosure")
                if disclosure is not None:
                    source_id = disclosure.get("source_id")
                    if source_id not in sources:
                        issues.append(_issue("SOURCE", "披露来源不存在", obj_id))
                    else:
                        start = disclosure.get("start")
                        length = sources[source_id].get("length", 0)
                        if not isinstance(start, int) or start < 0 or start >= length:
                            issues.append(_issue("DISCLOSURE", "披露位置超出原文范围", obj_id))
                completion = item.get("completion")
                if completion is not None:
                    source_id, start = completion.get("source_id"), completion.get("start")
                    if source_id not in sources:
                        issues.append(_issue("SOURCE", "主要故事闭合来源不存在", obj_id))
                    elif type(start) is not int or not 0 <= start <= sources[source_id].get("length", 0):
                        issues.append(_issue("COMPLETION", "主要故事闭合边界超出原文范围", obj_id))
            elif kind == "context":
                if item.get("parent_id"):
                    exists("context", item["parent_id"], obj_id, "parent_id")
                if item.get("holder_id"):
                    holder = exists("node", item["holder_id"], obj_id, "holder_id")
                    if holder and holder["node_type"] != "person":
                        issues.append(_issue("HOLDER", "观点持有人须为人物节点", obj_id))
            elif kind == "timeline":
                exists("context", item["context_id"], obj_id, "context_id")
            elif kind == "source_order":
                if item["source_id"] not in sources:
                    issues.append(_issue("SOURCE", "披露顺序来源不存在", obj_id))
                exists("context", item["context_id"], obj_id, "context_id")

        self._check_context_cycles(groups, issues)
        self._check_source_order(groups, issues)
        return issues

    def validate_snapshot(self, snapshot: dict, records: list[dict]) -> list[dict]:
        """Read-only validation of active objects at an already captured revision."""
        active, issues = [], []
        for obj in snapshot["objects"]:
            if obj.get("retracted"):
                continue
            normalized, diagnostics = self._normalized([obj])
            issues.extend(row | {"object_id": obj.get("id"), "field": row.get("object_id")}
                          for row in diagnostics)
            active.extend(normalized)
        if active:
            issues.extend(self.precheck(active, snapshot["revision"], _snapshot=snapshot,
                                        _accepted_properties=records))
        return issues

    def _check_context_cycles(self, groups, issues):
        parents = {obj["id"]: obj.get("parent_id") for obj in groups["context"].values()}
        for node in parents:
            seen: set[str] = set()
            cursor: str | None = node
            while cursor:
                if cursor in seen:
                    issues.append(_issue("CONTEXT_CYCLE", "语境继承不能形成循环", node))
                    break
                seen.add(cursor)
                cursor = parents.get(cursor)

    @staticmethod
    def _check_source_order(groups, issues):
        occupied: dict[tuple[str, int], str] = {}
        seen_sources: dict[tuple[str, str], str] = {}
        for item in groups["source_order"].values():
            key = (item["context_id"], item["ordinal"])
            prior = occupied.get(key)
            if prior and prior != item["source_id"]:
                issues.append(_issue("SOURCE_ORDER", "同一语境的披露序号不能对应不同来源", item["id"]))
            occupied[key] = item["source_id"]
            source_key = (item["context_id"], item["source_id"])
            if source_key in seen_sources:
                issues.append(_issue("SOURCE_ORDER", "同一来源在一条阅读序列中只能登记一次", item["id"]))
            seen_sources[source_key] = item["id"]
