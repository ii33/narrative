"""Evidence-bound graph proposals and human review for v4 projects."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from uuid import uuid4

from pydantic import ValidationError

from .graph_models import GRAPH_OBJECTS, GraphObject, TimeExtent, parse_objects
from .graph_store import GraphStore


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
            source_id = uuid4().hex
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]) + 1
            db.execute(
                "INSERT INTO sources(id,title,text,hash,imported_revision) VALUES (?,?,?,?,?)",
                (source_id, title.strip(), text, source_hash, revision),
            )
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (str(revision),))
            db.execute(
                "INSERT INTO revisions(revision,data) VALUES (?,?)",
                (revision, json.dumps({"import_source": source_id, "operations": []})),
            )
        return source_id

    def locate(self, source_id: str, quote: str) -> list[dict]:
        if not quote:
            return []
        source = self.store.source(source_id)
        result, cursor = [], 0
        while (start := source["text"].find(quote, cursor)) >= 0:
            result.append(
                {
                    "kind": "evidence",
                    "id": uuid4().hex,
                    "source_id": source_id,
                    "source_hash": source["hash"],
                    "start": start,
                    "end": start + len(quote),
                    "quote": quote,
                }
            )
            cursor = start + 1
        return result

    def schema(self) -> dict:
        contracts = _field_contracts()
        return {
            "version": 4,
            "contract_version": "graph-nesting/2",
            "object_kinds": [
                "node", "predicate", "context", "timeline", "evidence", "evidence_link",
                "assertion", "source_order",
            ],
            "node_types": [
                "person", "item", "location", "event", "scene", "storyline", "group", "other"
            ],
            "assertion_scopes": ["world", "claim", "knowledge"],
            "predicate_semantics": contracts["predicate"]["enums"]["semantic"],
            "predicate_value_types": contracts["predicate"]["enums"]["value_type"],
            "temporal_relations": contracts["predicate"]["enums"]["temporal_relation"],
            "field_contracts": contracts,
            "time_bound_states": ["known", "unknown", "unbounded"],
            "review": "预检无 error 后仅生成待审候选；人工接受才进入正式图",
            "fields": {
                "node": {"required": ["kind", "id", "node_type", "name"], "optional": ["aliases", "subtype", "context_id", "identity_note", "occurrence"]},
                "predicate": {"required": ["kind", "id", "label", "semantic", "temporal_relation 当 semantic=temporal"], "optional": ["subject_types", "object_types", "value_type", "functional", "description"]},
                "assertion": {"required": ["kind", "id", "subject_id", "predicate_id", "object_id 或 value"], "optional": ["scope", "holder_id", "context_id", "polarity", "modality", "certainty", "validity", "disclosure", "origin", "assessment", "derived_from", "supersedes_id", "qualifiers", "temporal_left", "temporal_right", "order_index"]},
                "evidence": {"required": ["kind", "id", "source_id", "source_hash", "start", "end", "quote"], "optional": []},
                "evidence_link": {"required": ["kind", "id", "assertion_id", "evidence_id"], "optional": ["role"]},
                "context": {"required": ["kind", "id", "label", "context_type"], "optional": ["parent_id", "holder_id"]},
                "timeline": {"required": ["kind", "id", "label"], "optional": ["context_id", "calendar"]},
                "source_order": {"required": ["kind", "id", "source_id", "ordinal"], "optional": ["context_id"]},
            },
            "rules": [
                "参与、地点和先后都以 assertion 表达，没有独立事实表。",
                "field_contracts 的 enums/defaults 来自实际模型字段；数组元素枚举以 [] 标记。predicate.value_type=node|scalar|either，默认node；标量属性明确设scalar或either。",
                "predicate.semantic=temporal 必须 temporal_relation；其他semantic禁止 temporal_relation；containment必须value_type=node。",
                "participation 谓词需事件 subject、节点 object、qualifiers.role。",
                "temporal 谓词需 temporal_left 和 temporal_right；TimeRef.event_boundary 要 event_id 与 boundary=start|end。",
                "每个断言至少通过 evidence_link 关联一份准确原文；inference 还需 derived_from。",
                "scope=claim|knowledge 必须 holder_id；world 不可有 holder_id。",
                "containment 是阅读组织，不是 temporal contains；parent=subject，child=object，order_index 仅表示同父阅读顺序，不推出故事时间。",
                "containment 只允许 storyline→storyline|scene|event、scene→scene|event、event→event；人物和物品通过参与断言共享引用。",
                "有效 containment 必须 positive/world/actual、original 或有有效依据的 inference、同 context、非 retracted、有 supports 原文证据；creative/production 不作组织边。不得自包含、成环、重复边或多父。显式 supersedes_id 可修订同子节点旧组织边；须新ID、新边披露和已登记source_order；旧前驱披露未知可衔接并警告，不猜补历史；双方已知时禁止倒退及分叉。后继撤回不自动复活旧边。事件没有固定主子等级，复用稳定事件ID并新增上层事件。",
            ],
            "examples": {
                "node": {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
                "predicate": {
                    "kind": "predicate", "id": "participates", "label": "参与",
                    "semantic": "participation", "subject_types": ["event"],
                    "object_types": ["person", "item", "other"], "value_type": "node",
                },
                "assertion": {
                    "kind": "assertion", "id": "a1", "subject_id": "e1",
                    "predicate_id": "participates", "object_id": "p1",
                    "qualifiers": {"role": "送行者"}, "scope": "world",
                },
                "claim": {
                    "kind": "assertion", "id": "a2", "subject_id": "key1",
                    "predicate_id": "lost", "value": True, "scope": "claim",
                    "holder_id": "p1", "modality": "reported",
                },
                "evidence_link": {
                    "kind": "evidence_link", "id": "link1", "assertion_id": "a1",
                    "evidence_id": "q1", "role": "supports",
                },
                "evidence": {
                    "kind": "evidence", "id": "q1", "source_id": "source-id",
                    "source_hash": "sha256-of-exact-source", "start": 0, "end": 2,
                    "quote": "甲说",
                },
                "source_order": {"kind": "source_order", "id": "order1", "source_id": "source-id", "ordinal": 1},
                "event": {"kind": "node", "id": "e1", "node_type": "event", "name": "甲送别乙", "occurrence": {"start": {"state": "unknown"}, "end": {"state": "unknown"}}},
                "temporal_predicate": {"kind": "predicate", "id": "before", "label": "先于", "semantic": "temporal", "temporal_relation": "before", "subject_types": ["event"], "object_types": ["event"], "value_type": "node"},
                "temporal_assertion": {"kind": "assertion", "id": "a_before", "subject_id": "e1", "predicate_id": "before", "object_id": "e2", "temporal_left": {"timeline_id": "story", "form": "event_boundary", "event_id": "e1", "boundary": "end"}, "temporal_right": {"timeline_id": "story", "form": "event_boundary", "event_id": "e2", "boundary": "start"}},
                "time_ref": {
                    "timeline_id": "story", "form": "event_boundary",
                    "event_id": "e1", "boundary": "end",
                },
                "validity": {
                    "start": {"state": "unknown"}, "end": {"state": "unbounded"}
                },
                "containment_predicate": {"kind": "predicate", "id": "contains_node", "label": "包含节点", "semantic": "containment", "value_type": "node"},
                "containment_assertion": {"kind": "assertion", "id": "a_child", "subject_id": "storyline1", "predicate_id": "contains_node", "object_id": "scene1", "order_index": 0, "scope": "world", "modality": "actual", "polarity": "positive"},
            },
        }

    def _normalized(self, objects: list[dict]) -> tuple[list[dict], list[dict]]:
        try:
            parsed = parse_objects(objects)
        except ValidationError as exc:
            return [], [_issue("FORMAT", item["msg"], ".".join(map(str, item["loc"]))) for item in exc.errors()]
        return _as_dicts(parsed), []

    def precheck(self, objects: list[dict], expected_revision: int) -> list[dict]:
        normalized, issues = self._normalized(objects)
        if issues:
            return issues
        snapshot = self.store.snapshot()
        if expected_revision != snapshot["revision"]:
            issues.append(_issue("REVISION", "正式资料版本已变化，请刷新后重新预检"))
        if not normalized:
            issues.append(_issue("EMPTY", "候选至少须包含一条操作"))
            return issues
        ids = [item["id"] for item in normalized]
        if len(ids) != len(set(ids)):
            issues.append(_issue("DUPLICATE_ID", "同一候选内对象 ID 不得重复"))
        current = {item["id"]: item for item in snapshot["objects"]}
        current.update({item["id"]: item for item in snapshot["evidence_links"]})
        merged = current | {item["id"]: item for item in normalized}
        groups: dict[str, dict[str, dict]] = defaultdict(dict)
        for item in merged.values():
            groups[item["kind"]][item["id"]] = item
        sources = {source["id"]: source for source in snapshot["sources"]}
        source_texts: dict[str, dict] = {}

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
                if event and event["node_type"] != "event":
                    issues.append(_issue("TIME_REF", "事件边界须引用事件节点", owner))

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

        reorganizing = any(item["kind"] == "assertion"
                           and groups["predicate"].get(item["predicate_id"], {}).get("semantic") == "containment"
                           and item != current.get(item["id"]) for item in normalized)
        for item in normalized:
            obj_id, kind = item["id"], item["kind"]
            previous = current.get(obj_id)
            if previous and previous["kind"] != kind:
                issues.append(_issue("ID_KIND", "对象 ID 不可跨类型复用", obj_id))
            if previous and kind == "predicate" and previous != item:
                issues.append(_issue("PREDICATE_IMMUTABLE", "已采纳谓词定义不可改写；请新建带版本的谓词 ID", obj_id))
            if previous and kind == "source_order" and previous != item:
                issues.append(_issue("SOURCE_ORDER_IMMUTABLE", "已登记阅读顺序不可改写；请使用新的语境", obj_id))
            if previous and kind == "assertion" and groups["predicate"].get(previous["predicate_id"], {}).get("semantic") == "containment":
                immutable = ("subject_id", "object_id", "predicate_id", "context_id", "disclosure", "supersedes_id", "order_index")
                if any(previous.get(key) != item.get(key) for key in immutable):
                    issues.append(_issue("CONTAINMENT_IMMUTABLE", "组织关系及披露不可原地改写；请新增带 supersedes_id 的修订边", obj_id))
            if previous and obj_id in {"canon", "story"} and previous != item:
                issues.append(_issue("INFRA_IMMUTABLE", "正篇语境和故事时间轴标识不可修订", obj_id))
            if kind == "node":
                if reorganizing and previous and previous.get("node_type") == "event":
                    if any(previous.get(key) != item.get(key) for key in ("name", "node_type", "context_id", "occurrence", "aliases")):
                        issues.append(_issue("REORGANIZATION_IDENTITY", "重组批次不可同时改写既有事件身份及名称；请复用原事件并新增上层事件", obj_id))
                exists("context", item["context_id"], obj_id, "context_id")
                check_extent(item.get("occurrence"), obj_id)
            elif kind == "predicate":
                pass
            elif kind == "context":
                if item.get("parent_id"):
                    exists("context", item["parent_id"], obj_id, "parent_id")
                if item.get("holder_id"):
                    holder = exists("node", item["holder_id"], obj_id, "holder_id")
                    if holder and holder["node_type"] != "person":
                        issues.append(_issue("HOLDER", "观点持有人须为人物节点", obj_id))
            elif kind == "timeline":
                exists("context", item["context_id"], obj_id, "context_id")
            elif kind == "evidence":
                if item["source_id"] not in sources:
                    issues.append(_issue("SOURCE", "证据来源不存在", obj_id))
                else:
                    source = source_texts.setdefault(item["source_id"], self.store.source(item["source_id"]))
                    if item["source_hash"] != source["hash"] or source["text"][item["start"]:item["end"]] != item["quote"]:
                        issues.append(_issue("EVIDENCE", "证据引文、位置或哈希与原文不一致", obj_id))
            elif kind == "evidence_link":
                exists("assertion", item["assertion_id"], obj_id, "assertion_id")
                exists("evidence", item["evidence_id"], obj_id, "evidence_id")
            elif kind == "source_order":
                if item["source_id"] not in sources:
                    issues.append(_issue("SOURCE", "披露顺序来源不存在", obj_id))
                exists("context", item["context_id"], obj_id, "context_id")
            elif kind == "assertion":
                self._check_assertion(item, groups, exists, check_time, check_extent, sources, issues)

        self._check_dependencies(groups, issues)
        self._check_context_cycles(groups, issues)
        self._check_source_order(groups, issues)
        constraint_revisions = snapshot["object_revisions"] | {oid: snapshot["revision"] + 1 for oid in ids}
        self._check_temporal_cycles(groups, issues, constraint_revisions)
        self._check_hierarchy(groups, issues, snapshot["object_revisions"], set(ids), snapshot["revision"] + 1)
        self._check_functional_conflicts(groups, normalized, issues)
        return issues

    def _check_assertion(self, item, groups, exists, check_time, check_extent, sources, issues):
        obj_id = item["id"]
        subject = exists("node", item["subject_id"], obj_id, "subject_id")
        predicate = exists("predicate", item["predicate_id"], obj_id, "predicate_id")
        target = exists("node", item.get("object_id"), obj_id, "object_id") if item.get("object_id") else None
        context = exists("context", item["context_id"], obj_id, "context_id")
        visible_contexts: set[str] = set()
        cursor = item["context_id"]
        while cursor and cursor not in visible_contexts:
            visible_contexts.add(cursor)
            cursor = groups["context"].get(cursor, {}).get("parent_id")
        for endpoint in (subject, target):
            if endpoint and endpoint["context_id"] not in visible_contexts:
                issues.append(_issue("CONTEXT_REF", "断言不能引用该语境中不可见的节点", obj_id))
        if context and context["context_type"] == "viewpoint" and (item["scope"] == "world" or item.get("holder_id") != context["holder_id"]):
            issues.append(_issue("VIEWPOINT", "人物观点语境只能存对应人物的主张或认知", obj_id))
        if item.get("holder_id"):
            holder = exists("node", item["holder_id"], obj_id, "holder_id")
            if holder and holder["node_type"] != "person":
                issues.append(_issue("HOLDER", "主张或认知的持有人须为人物", obj_id))
            if holder and holder["context_id"] not in visible_contexts:
                issues.append(_issue("CONTEXT_REF", "主张持有人在该语境中不可见", obj_id))
        if predicate:
            if item.get("order_index") is not None and predicate["semantic"] != "containment":
                issues.append(_issue("ORDER_INDEX", "只有组织包含断言可指定阅读顺序", obj_id))
            if subject and predicate["subject_types"] and subject["node_type"] not in predicate["subject_types"]:
                issues.append(_issue("PREDICATE_DOMAIN", "主语类型不符合谓词定义", obj_id))
            if target and predicate["object_types"] and target["node_type"] not in predicate["object_types"]:
                issues.append(_issue("PREDICATE_RANGE", "宾语类型不符合谓词定义", obj_id))
            if predicate["value_type"] == "node" and not item.get("object_id") and predicate["semantic"] != "temporal":
                issues.append(_issue("VALUE_TYPE", "此谓词须引用节点宾语", obj_id))
            if predicate["value_type"] == "scalar" and item.get("object_id"):
                issues.append(_issue("VALUE_TYPE", "此谓词须使用标量值", obj_id))
            if predicate["semantic"] == "participation":
                if not subject or subject["node_type"] != "event" or not target or not item.get("qualifiers", {}).get("role"):
                    issues.append(_issue("PARTICIPATION", "事件参与断言须有事件主语、参与节点和角色", obj_id))
            if predicate["semantic"] == "containment":
                allowed = {"storyline": {"storyline", "scene", "event"}, "scene": {"scene", "event"}, "event": {"event"}}
                if predicate["value_type"] != "node" or not subject or not target or target["node_type"] not in allowed.get(subject["node_type"], set()):
                    issues.append(_issue("CONTAINMENT_DOMAIN", "组织包含须为故事线→故事线/场景/事件、场景→场景/事件或事件→事件", obj_id))
                if subject and target and (subject["context_id"] != target["context_id"] or item["context_id"] != subject["context_id"]):
                    issues.append(_issue("CONTAINMENT_CONTEXT", "组织包含两端与断言须在同一语境", obj_id))
                if item["scope"] != "world" or item["polarity"] != "positive" or item["modality"] != "actual" or item["origin"] not in {"original", "inference"}:
                    issues.append(_issue("CONTAINMENT_STATUS", "组织包含须为原文或有依据推断的正向现实世界断言", obj_id))
                if item["origin"] == "inference" and self._has_retracted_ancestor(item, groups["assertion"]):
                    issues.append(_issue("CONTAINMENT_DEPENDENCY", "推断组织边不可依据已撤回断言", obj_id))
                if item["subject_id"] == item.get("object_id"):
                    issues.append(_issue("CONTAINMENT_SELF", "节点不可包含自身", obj_id))
            if predicate["semantic"] == "temporal":
                if not item.get("temporal_left") or not item.get("temporal_right"):
                    issues.append(_issue("TEMPORAL", "时序断言须有左右时间引用", obj_id))
                elif item["temporal_left"]["timeline_id"] != item["temporal_right"]["timeline_id"]:
                    issues.append(_issue("TEMPORAL_AXIS", "时序约束两端须在同一故事时间轴", obj_id))
                if (predicate["temporal_relation"] in {"before", "after"}
                        and item["polarity"] == "positive" and item["modality"] == "actual"
                        and item.get("subject_id") == item.get("object_id")):
                    if item.get("temporal_left") == item.get("temporal_right"):
                        issues.append(_issue("TIME_CYCLE", "同一事件边界不能严格先于或晚于自身", obj_id))
            elif item.get("temporal_left") or item.get("temporal_right"):
                issues.append(_issue("TEMPORAL", "只有时序谓词可带左右时间引用", obj_id))
        check_time(item.get("temporal_left"), obj_id)
        check_time(item.get("temporal_right"), obj_id)
        check_extent(item.get("validity"), obj_id)
        disclosure = item.get("disclosure")
        if disclosure:
            source = sources.get(disclosure["source_id"])
            if not source or disclosure["start"] > source["length"]:
                issues.append(_issue("DISCLOSURE", "文本披露位置超出来源", obj_id))
        if item.get("supersedes_id"):
            prior = exists("assertion", item["supersedes_id"], obj_id, "supersedes_id")
            if predicate and predicate["semantic"] == "containment" and prior:
                old_predicate = groups["predicate"].get(prior["predicate_id"])
                if (old_predicate is None or old_predicate["semantic"] != "containment"
                        or prior["context_id"] != item["context_id"]
                        or prior.get("object_id") != item.get("object_id")):
                    issues.append(_issue("CONTAINMENT_SUPERSEDES", "组织边只可替换同语境、同子节点的组织边", obj_id))
        for ancestor in item.get("derived_from", []):
            exists("assertion", ancestor, obj_id, "derived_from")
        links = [link for link in groups["evidence_link"].values() if link["assertion_id"] == obj_id]
        if not links:
            issues.append(_issue("EVIDENCE_REQUIRED", "断言至少须关联一份可核原文证据", obj_id))
        if predicate and predicate["semantic"] == "containment" and item["assessment"] != "retracted":
            if not any(link["role"] == "supports" for link in links):
                issues.append(_issue("CONTAINMENT_EVIDENCE", "组织包含须有 supports 原文证据关联", obj_id))

    @staticmethod
    def _has_retracted_ancestor(assertion, assertions):
        pending = list(assertion.get("derived_from", []))
        seen: set[str] = set()
        while pending:
            parent_id = pending.pop()
            if parent_id in seen:
                continue
            seen.add(parent_id)
            parent = assertions.get(parent_id)
            if parent and parent["assessment"] == "retracted":
                return True
            if parent:
                pending.extend(parent.get("derived_from", []))
        return False

    @staticmethod
    def _check_hierarchy(groups, issues, revisions, changed_ids, next_revision):
        predicates = groups["predicate"]
        assertions = groups["assertion"]
        def valid_dependency(a, visiting=None):
            visiting = set() if visiting is None else visiting
            if a["id"] in visiting:
                return False
            visiting.add(a["id"])
            own_revision = next_revision if a["id"] in changed_ids else revisions.get(a["id"], 0)
            return all(parent in assertions
                       and assertions[parent]["assessment"] != "retracted"
                       and (next_revision if parent in changed_ids else revisions.get(parent, 0)) <= own_revision
                       and valid_dependency(assertions[parent], visiting.copy())
                       for parent in a.get("derived_from", []))

        active = [a for a in assertions.values()
                  if predicates.get(a["predicate_id"], {}).get("semantic") == "containment"
                  and a["assessment"] != "retracted" and a["origin"] in {"original", "inference"}
                  and (a["origin"] != "inference" or valid_dependency(a))]
        containment = [a for a in assertions.values()
                       if predicates.get(a["predicate_id"], {}).get("semantic") == "containment"]
        successors = {}
        orders = {(r["context_id"], r["source_id"]): r["ordinal"]
                  for r in groups["source_order"].values()}
        def position(a):
            d = a.get("disclosure")
            if not d:
                return None
            context, seen = a["context_id"], set()
            ordinal = None
            while context and context not in seen:
                seen.add(context)
                ordinal = orders.get((context, d["source_id"]))
                if ordinal is not None:
                    break
                context = groups["context"].get(context, {}).get("parent_id")
            return (ordinal, d["start"]) if ordinal is not None else None
        for a in containment:
            prior_id = a.get("supersedes_id")
            if not prior_id:
                continue
            if prior_id in successors:
                issues.append(_issue("CONTAINMENT_BRANCH", "组织修订不可从同一前驱分叉；请接续最新边", a["id"]))
            successors[prior_id] = a["id"]
            seen, cursor = {a["id"]}, prior_id
            while cursor in assertions:
                if cursor in seen:
                    issues.append(_issue("CONTAINMENT_REVISION_CYCLE", "组织修订链不得成环", a["id"]))
                    break
                seen.add(cursor)
                cursor = assertions[cursor].get("supersedes_id")
            prior = assertions.get(prior_id)
            if prior:
                before, after = position(prior), position(a)
                if after is None and a["id"] in changed_ids:
                    issues.append(_issue("CONTAINMENT_DISCLOSURE", "新组织修订须显式披露并登记来源阅读顺序", a["id"]))
                if before is None or after is None:
                    issues.append(_issue("CONTAINMENT_LEGACY_DISCLOSURE", "此前阅读位置未知，不能还原早期归属，可查审核历史；未猜补披露", a["id"], "warning"))
                elif after < before:
                    issues.append(_issue("CONTAINMENT_DISCLOSURE", "组织修订不可早于前驱披露", a["id"]))
        # Validate every reading boundary, not only the latest organization.
        boundaries = {point for a in containment if (point := position(a)) is not None}
        slices = [containment] + [[a for a in containment if (point := position(a)) is not None and point <= cut]
                             for cut in sorted(boundaries)]
        for visible in slices:
            replaced = {a["supersedes_id"] for a in visible if a.get("supersedes_id")}
            GraphService._check_hierarchy_edges([a for a in visible if a in active and a["id"] not in replaced], issues)

    @staticmethod
    def _check_hierarchy_edges(active, issues):
        parent_of: dict[str, str] = {}
        edge_of: dict[tuple[str, str], str] = {}
        for a in active:
            parent, child = a["subject_id"], a.get("object_id")
            if not child:
                continue
            if (parent, child) in edge_of:
                issues.append(_issue("CONTAINMENT_DUPLICATE", "同一父子组织边不可重复", a["id"]))
            edge_of[parent, child] = a["id"]
            if child in parent_of and parent_of[child] != parent:
                issues.append(_issue("CONTAINMENT_PARENT", "组织节点最多只能有一个父节点", a["id"]))
            parent_of[child] = parent
        for child in parent_of:
            seen: set[str] = set()
            cursor = child
            while cursor in parent_of:
                if cursor in seen:
                    issues.append(_issue("CONTAINMENT_CYCLE", "组织包含不得形成环", edge_of.get((parent_of[cursor], cursor))))
                    break
                seen.add(cursor)
                cursor = parent_of[cursor]

    @staticmethod
    def _check_dependencies(groups, issues):
        graph = {obj["id"]: obj.get("derived_from", []) for obj in groups["assertion"].values()}
        visiting: set[str] = set()
        visited: set[str] = set()

        def walk(node: str) -> None:
            if node in visiting:
                issues.append(_issue("DERIVATION_CYCLE", "推断依赖不能形成循环", node))
                return
            if node in visited:
                return
            visiting.add(node)
            for parent in graph.get(node, []):
                walk(parent)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            walk(node)

    @staticmethod
    def _check_context_cycles(groups, issues):
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

    @staticmethod
    def _check_temporal_cycles(groups, issues, object_revisions=None):
        from .graph_constraints import temporal_conflicts

        issues.extend(temporal_conflicts(groups, object_revisions))

    @staticmethod
    def _known_overlap(a: dict, b: dict) -> bool:
        def bounds(obj):
            extent = TimeExtent.model_validate(obj["validity"])
            if extent.kind == "point":
                point = extent.point
                if point is None or point.form != "number" or point.number is None:
                    return None
                ref = (point.timeline_id, point.number)
                return [ref, ref]
            out = []
            for bound in (extent.start, extent.end):
                if bound.state == "unknown":
                    return None
                if bound.state == "unbounded":
                    out.append(None)
                elif bound.ref and bound.ref.form == "number" and bound.ref.number is not None:
                    out.append((bound.ref.timeline_id, bound.ref.number))
                else:
                    return None
            return out

        first, second = bounds(a), bounds(b)
        if first is None or second is None:
            return False
        values = [v for v in (*first, *second) if v is not None]
        if len({v[0] for v in values}) > 1:
            return False
        a0 = float("-inf") if first[0] is None else float(first[0][1])
        a1 = float("inf") if first[1] is None else float(first[1][1])
        b0 = float("-inf") if second[0] is None else float(second[0][1])
        b1 = float("inf") if second[1] is None else float(second[1][1])
        if a0 == a1:
            return b0 <= a0 < b1 if b0 != b1 else a0 == b0
        if b0 == b1:
            return a0 <= b0 < a1
        return a0 < b1 and b0 < a1

    def _check_functional_conflicts(self, groups, normalized, issues):
        changed = {item["id"] for item in normalized}
        assertions = list(groups["assertion"].values())
        for i, left in enumerate(assertions):
            predicate = groups["predicate"].get(left["predicate_id"])
            if not predicate or not predicate["functional"]:
                continue
            for right in assertions[i + 1:]:
                if not ({left["id"], right["id"]} & changed):
                    continue
                if any(left.get(key) != right.get(key) for key in ("subject_id", "predicate_id", "context_id", "scope", "holder_id")):
                    continue
                if left.get("assessment") == "retracted" or right.get("assessment") == "retracted":
                    continue
                if (left.get("object_id"), left.get("value")) == (right.get("object_id"), right.get("value")):
                    continue
                if self._known_overlap(left, right):
                    issues.append(_issue("FUNCTIONAL_CONFLICT", "同语境互斥谓词在已知重叠区间取值不同，须人工复核", left["id"], "warning"))

    def propose(self, objects, expected_revision, *, source, note, run_id, parent_candidate_id):
        normalized, format_issues = self._normalized(objects)
        if format_issues:
            raise ValueError("；".join(issue["message"] for issue in format_issues))
        diagnostics = self.precheck(normalized, expected_revision)
        errors = [item["message"] for item in diagnostics if item["severity"] == "error"]
        if errors:
            raise ValueError("；".join(errors))
        if source not in {"manual", "model", "correction"}:
            raise ValueError("候选来源无效")
        candidate_id = uuid4().hex
        change = {
            "id": candidate_id, "objects": normalized, "source": source, "note": note,
            "run_id": run_id, "parent_candidate_id": parent_candidate_id,
            "expected_revision": expected_revision, "diagnostics": diagnostics,
        }
        with self.store.connect(write=True) as db:
            db.execute(
                "INSERT INTO candidates(id,data,status,version,expected_revision) VALUES (?,?,?,?,?)",
                (candidate_id, json.dumps(change, ensure_ascii=False), "pending", 1, expected_revision),
            )
        return candidate_id

    def candidates(self, status: str | None = None) -> list[dict]:
        if status is not None and status not in {"pending", "accepted", "rejected"}:
            raise ValueError("候选状态无效")
        sql = "SELECT * FROM candidates" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC,rowid DESC"
        with self.store.connect() as db:
            rows = db.execute(sql, (status,) if status else ()).fetchall()
        return [self._candidate_row(row) for row in rows]

    @staticmethod
    def _candidate_row(row) -> dict:
        return json.loads(row["data"]) | {
            "status": row["status"], "version": row["version"],
            "expected_revision": row["expected_revision"],
            "result_revision": row["result_revision"], "created_at": row["created_at"],
        }

    def candidate(self, candidate_id: str) -> dict:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if not row:
            raise ValueError("候选不存在")
        return self._candidate_row(row)

    def edit_candidate(self, candidate_id: str, objects: list[dict], version: int) -> dict:
        normalized, format_issues = self._normalized(objects)
        if format_issues:
            raise ValueError("；".join(issue["message"] for issue in format_issues))
        with self.store.connect(write=True) as db:
            row = db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if not row or row["status"] != "pending" or row["version"] != version:
                raise ValueError("候选状态或版本已变化")
            change = json.loads(row["data"])
            diagnostics = self.precheck(normalized, change["expected_revision"])
            errors = [item["message"] for item in diagnostics if item["severity"] == "error"]
            if errors:
                raise ValueError("；".join(errors))
            change["objects"] = normalized
            change["diagnostics"] = diagnostics
            db.execute(
                "UPDATE candidates SET data=?,version=version+1 WHERE id=?",
                (json.dumps(change, ensure_ascii=False), candidate_id),
            )
        return self.candidate(candidate_id)

    def select_operations(self, candidate_id: str, keep_ids: list[str], version: int) -> dict:
        candidate = self.candidate(candidate_id)
        if len(keep_ids) != len(set(keep_ids)):
            raise ValueError("保留对象 ID 不能重复")
        available = {item["id"] for item in candidate["objects"]}
        if not keep_ids or not set(keep_ids) <= available:
            raise ValueError("须至少保留一条该候选内的操作")
        return self.edit_candidate(
            candidate_id,
            [item for item in candidate["objects"] if item["id"] in keep_ids],
            version,
        )

    def accept(self, candidate_id: str, version: int) -> int:
        with self.store.connect(write=True) as db:
            row = db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if not row:
                raise ValueError("候选不存在")
            if row["status"] != "pending" or row["version"] != version:
                raise ValueError("候选状态或版本已变化")
            change = json.loads(row["data"])
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            if change["expected_revision"] != revision:
                raise ValueError("正式资料版本已变化，请重新建立候选")
            errors = [d["message"] for d in self.precheck(change["objects"], revision) if d["severity"] == "error"]
            if errors:
                raise ValueError("；".join(errors))
            audit = []
            for obj in change["objects"]:
                if obj["kind"] == "evidence_link":
                    before = db.execute("SELECT * FROM evidence_links WHERE id=?", (obj["id"],)).fetchone()
                else:
                    before = db.execute("SELECT kind,data FROM objects WHERE id=?", (obj["id"],)).fetchone()
                if before and change["source"] != "correction":
                    raise ValueError("已有对象只能由显式修订候选更新")
                audit.append({"id": obj["id"], "before": dict(before) if before else None, "after": obj})
            next_revision = revision + 1
            for obj in change["objects"]:
                if obj["kind"] == "evidence_link":
                    db.execute(
                        "INSERT INTO evidence_links VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET assertion_id=excluded.assertion_id,evidence_id=excluded.evidence_id,role=excluded.role,accepted_revision=excluded.accepted_revision",
                        (obj["id"], obj["assertion_id"], obj["evidence_id"], obj["role"], next_revision),
                    )
                else:
                    db.execute(
                        "INSERT INTO objects VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,data=excluded.data,accepted_revision=excluded.accepted_revision",
                        (obj["id"], obj["kind"], json.dumps(obj, ensure_ascii=False), next_revision),
                    )
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (str(next_revision),))
            db.execute("UPDATE candidates SET status='accepted',result_revision=? WHERE id=?", (next_revision, candidate_id))
            db.execute(
                "INSERT INTO revisions(revision,candidate_id,data) VALUES (?,?,?)",
                (next_revision, candidate_id, json.dumps({"operations": change["objects"], "audit": audit}, ensure_ascii=False)),
            )
        return next_revision

    def reject(self, candidate_id: str, version: int) -> None:
        with self.store.connect(write=True) as db:
            row = db.execute("SELECT status,version FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if not row or row["status"] != "pending" or row["version"] != version:
                raise ValueError("候选状态或版本已变化")
            db.execute("UPDATE candidates SET status='rejected' WHERE id=?", (candidate_id,))
