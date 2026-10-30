"""Evidence-bound graph proposals and human review for v5 projects."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from uuid import uuid4

from pydantic import ValidationError

from .graph_models import (
    GRAPH_OBJECTS,
    GraphObject,
    parse_objects,
)
from .graph_store import GraphStore
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
        property_fields = {
            "property_definition": {
                "required": ["id", "value_type", "cardinality"],
                "optional": ["target_node_types", "evidence"],
            },
            "property_value": {
                "required": ["id", "node_id", "property_id", "value", "event_id"],
                "optional": ["anchor_basis", "applies_to", "scope", "holder_id",
                             "disclosure", "evidence"],
            },
            "property_retraction": {
                "required": ["id", "target_id", "reason"],
                "optional": ["evidence"],
            },
        }
        return {
            "version": 5,
            "schema_version": 5,
            "contract_version": "graph-nesting/2",
            "property_contract_version": PROPERTY_CONTRACT_VERSION,
            "property_record_kinds": list(PROPERTY_RECORD_KINDS),
            "property_record_fields": property_fields,
            "property_records": property_fields,
            "property_effect_ops": list(PROPERTY_EFFECT_OPS),
            "property_state_cells": list(PROPERTY_STATE_CELL_KINDS),
            "property_value_types": ["string", "number", "boolean", "node", "either"],
            "property_cardinalities": ["one", "many"],
            "property_candidate_staging": {
                "precheck": "Operations.property_precheck(records, expected_revision)",
                "propose": "Operations.property_propose(records, expected_revision, source, note)",
                "accept": "human-only Operations.property_accept(id, version)",
                "reject": "human-only Operations.property_reject(id, version)",
                "agent_tool_args": {
                    "property_precheck": ["records", "expected_revision", "note"],
                    "property_propose": ["records", "expected_revision", "note"],
                    "source": "可省略；无论传入什么，代理桥接固定记为 model",
                },
                "model_access": "propose_only",
                "model_may_accept": False,
                "model_can_accept": False,
                "model_may_delete": False,
                "tools": ["property_precheck", "property_propose"],
                "records": list(PROPERTY_RECORD_KINDS),
            },
            "property_evidence": {
                "shape": {
                    "source_id": "an accepted source id in this project",
                    "quote": "an exact substring of that source text",
                },
                "exactly": ["source_id", "quote"],
                "quote_rule": "the quote must occur exactly once in the accepted source text; unique exact substring only",
                "staged_graph_evidence_ids": "not valid here; property evidence never references graph evidence objects (for example a staged ev1 handle) staged in the agent workspace",
            },
            "property_two_stage_gate": {
                "order": [
                    "1. graph candidate is proposed from the private graph workspace",
                    "2. a human accepts the graph candidate",
                    "3. property precheck/propose run against the now accepted graph",
                ],
                "graph_first": "Property records may only be proposed after the graph objects they reference are human-accepted; graph and property records are never submitted as one combined candidate.",
                "property_scope": "property_precheck/property_propose validate only against the accepted graph, never against the agent's private graph workspace.",
                "pending_graph_review": "If property records reference graph objects staged in the same private workspace (node_id, event_id, an event-boundary TimeRef, or a node-valued state/effect value), the agent tools return a PENDING_GRAPH_REVIEW diagnostic and next_action instead of referential errors, and no property candidate is created.",
            },
            "object_kinds": ["node", "context", "timeline", "source_order"],
            "node_types": [
                "person", "item", "location", "event", "scene", "storyline", "group", "other"
            ],
            "time_bound_states": ["known", "unknown", "unbounded"],
            "review": "预检无 error 后仅生成待审候选；人工接受才进入正式图",
            "fields": {
                "node": {"required": ["kind", "id", "node_type", "name"], "optional": ["aliases", "subtype", "context_id", "identity_note", "occurrence", "disclosure", "evidence", "retracted", "participants", "predecessors", "successors", "events", "source_marks"]},
                "context": {"required": ["kind", "id", "label", "context_type"], "optional": ["parent_id", "holder_id"]},
                "timeline": {"required": ["kind", "id", "label"], "optional": ["context_id", "calendar"]},
                "source_order": {"required": ["kind", "id", "source_id", "ordinal"], "optional": ["context_id"]},
            },
            "rules": [
                "property_* 记录的 evidence 恰为 {source_id, quote}：source_id 须为已接受来源，quote 须在该来源原文中恰好出现一次；不得引用图证据对象ID或私有句柄。",
                "属性两阶段门槛：先由私有图草稿 graph_propose 并由人工接受图候选，之后属性分析才能对已接受图 precheck/propose；属性记录引用私有图草稿ID时工具返回 PENDING_GRAPH_REVIEW，不创建属性候选，也不与图记录合并成一个候选。",
            ],
            "examples": {
                "node": {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
                "property_definition": {
                    "kind": "property_definition", "id": "holder", "value_type": "node",
                    "cardinality": "one",
                    "target_node_types": ["person"],
                },
                "property_value": {
                    "kind": "property_value", "id": "key-holder-at-e1",
                    "node_id": "key", "property_id": "holder", "value": "p1",
                    "event_id": "e1", "anchor_basis": "model",
                    "disclosure": {"source_id": "s1", "start": 120},
                    "evidence": {"source_id": "s1", "quote": "原文逐字引文"},
                },
                "property_retraction": {
                    "kind": "property_retraction", "id": "retract-holder",
                    "target_id": "key-holder-at-e1", "reason": "登记错误",
                },
                "evidence_link": {
                    "evidence_id": "q1", "role": "supports",
                },
                "evidence": {
                    "kind": "evidence", "id": "q1", "source_id": "source-id",
                    "source_hash": "sha256-of-exact-source", "start": 0, "end": 2,
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

    def precheck(self, objects: list[dict], expected_revision: int, *, allow_cross_family: bool = False) -> list[dict]:
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
        merged = current | {item["id"]: item for item in normalized}
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

        for item in normalized:
            obj_id, kind = item["id"], item["kind"]
            previous = current.get(obj_id)
            if previous and previous["kind"] != kind:
                issues.append(_issue("ID_KIND", "对象 ID 不可跨类型复用", obj_id))
            if previous and kind == "source_order" and previous != item:
                issues.append(_issue("SOURCE_ORDER_IMMUTABLE", "已登记阅读顺序不可改写；请使用新的语境", obj_id))
            if previous and obj_id in {"canon", "story"} and previous != item:
                issues.append(_issue("INFRA_IMMUTABLE", "正篇语境和故事时间轴标识不可修订", obj_id))
            if kind == "node":
                exists("context", item["context_id"], obj_id, "context_id")
                check_extent(item.get("occurrence"), obj_id)
                if item.get("participants") and item["node_type"] != "event":
                    issues.append(_issue("PARTICIPANTS", "只有事件节点记录参与者", obj_id))
                if item.get("events") and item["node_type"] not in {"event", "scene", "storyline"}:
                    issues.append(_issue("COMPOSITE", "只有事件、场景、故事线可以包含子事件", obj_id))
                for child in item.get("events") or []:
                    target = merged.get(child)
                    if not target or target["kind"] != "node":
                        issues.append(_issue("REF", "子事件须引用已有或本批节点", obj_id))
                        continue
                    if target.get("node_type") not in {"event", "scene"}:
                        issues.append(_issue("COMPOSITE", "子事件须是事件或场景", obj_id))
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
                        text = self.store.source(source_id)["text"]
                        if quote not in text:
                            issues.append(_issue("EVIDENCE", "节点引文不是原文子串", obj_id))
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
                before = db.execute("SELECT kind,data FROM objects WHERE id=?", (obj["id"],)).fetchone()
                if before and change["source"] != "correction":
                    raise ValueError("已有对象只能由显式修订候选更新")
                audit.append({"id": obj["id"], "before": dict(before) if before else None, "after": obj})
            next_revision = revision + 1
            for obj in change["objects"]:
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
