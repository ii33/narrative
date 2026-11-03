"""Independent, explicitly synchronized adaptation workspace.

The current workspace is a project meta document, never extraction graph data.
Its own revision protects edits; source baselines are solely for three-way sync.
"""
from __future__ import annotations

import copy
import json
from uuid import uuid4

from .graph_models import Node
from .graph_store import GraphStore
from .property_core import _valid_value
from .readable_ids import allocate_id

_KEY = "adaptation_workspace_v1"
_EXTRA = {"sort_id", "suppressed", "source_node_id", "point_id"}


def _empty():
    return {"revision": 0, "source_revision": None, "nodes": [], "properties": [],
            "conflicts": [], "sync_diagnostics": [], "baseline_nodes": {}, "baseline_properties": {}}


def _read(db):
    row = db.execute("SELECT value FROM meta WHERE key=?", (_KEY,)).fetchone()
    return json.loads(row[0]) if row else _empty()


def _public(state):
    return {key: copy.deepcopy(value) for key, value in state.items()
            if not key.startswith("baseline_")}


def _find(state, node_id):
    for node in state["nodes"]:
        if node["id"] == node_id:
            return node
    raise ValueError("改编节点不存在")


def _node_id(db, state, node_type):
    prefix = "adapt-" + node_type.replace("_", "-") + "-"
    return allocate_id(db, "adaptation", prefix, (node["id"] for node in state["nodes"]))


def _value_id(db, state):
    return allocate_id(db, "adaptation", "adapt-value-", (row["id"] for row in state["properties"]))


def _order(state, node_id, after_id):
    nodes = sorted(state["nodes"], key=lambda n: n["sort_id"])
    node = next(n for n in nodes if n["id"] == node_id)
    nodes.remove(node)
    if after_id == node_id:
        raise ValueError("不能移动到自身之后")
    index = 0 if after_id is None else next(
        (i + 1 for i, n in enumerate(nodes) if n["id"] == after_id), -1)
    if index < 0:
        raise ValueError("排序参照节点不存在")
    nodes.insert(index, node)
    for index, item in enumerate(nodes, 1):
        item["sort_id"] = index * 1024
    state["nodes"] = nodes


def _parent(state, node_id, parent_id):
    parent = _find(state, parent_id)
    for node in state["nodes"]:
        if node_id in node.get("events", []):
            node["events"].remove(node_id)
    parent.setdefault("events", []).append(node_id)


def _normalize_properties(records, nodes):
    """Translate source-coordinate intervals to the adaptation ordering axis."""
    by_id = {n["id"]: n for n in nodes}
    moments = [n for n in nodes if n["node_type"] in {"event", "action"} and not n.get("retracted")]
    diagnostics = []
    result = copy.deepcopy(records)
    for row in result:
        if row.get("kind") != "property_value":
            continue
        for source_field, target_field in (("valid_from", "event_id"), ("valid_to", "end_event_id")):
            boundary = row.pop(source_field, None)
            if boundary is None:
                continue
            row["source_" + source_field] = copy.deepcopy(boundary)
            explicit = boundary.get("event_id")
            if explicit and explicit in by_id and by_id[explicit]["node_type"] in {"event", "action"}:
                row[target_field] = explicit
                continue
            sid, offset = boundary.get("source_id"), boundary.get("start")
            exact = [n for n in moments if (n.get("disclosure") or {}).get("source_id") == sid
                     and (n.get("disclosure") or {}).get("start") == offset]
            candidates = exact
            if not candidates and isinstance(offset, int):
                candidates = [n for n in moments if any(span.get("source_id") == sid
                    and span.get("start", -1) <= offset < span.get("end", -1)
                    for span in n.get("evidence_spans", []))]
            if len(candidates) > 1:
                preferred = row.get("event_id") if source_field == "valid_from" else None
                candidates = [n for n in candidates if n["id"] == preferred] or candidates
            if len(candidates) == 1:
                row[target_field] = candidates[0]["id"]
                diagnostics.append({"severity": "warning", "code": "SOURCE_BOUNDARY_MAPPED", "object_id": row["id"],
                                    "field": source_field, "boundary": boundary, "event_id": row[target_field],
                                    "message": "原文区间边界无有效事件锚点，已由精确原文坐标定位改编时点"})
            else:
                row[target_field] = None
                row["unresolved_" + target_field] = copy.deepcopy(boundary)
                diagnostics.append({"severity": "error", "code": "SOURCE_BOUNDARY_UNRESOLVED", "object_id": row["id"],
                                    "field": source_field, "boundary": boundary,
                                    "message": "原文区间边界无法唯一定位改编时点；同步未入库，请先纠正原文锚点"})
    return result, diagnostics


def _issues(state):
    issues = [copy.deepcopy(d) for d in state.get("sync_diagnostics", []) if d.get("severity") == "error"]
    nodes = {n["id"]: n for n in state["nodes"]}
    def issue(code, text, node_id=None):
        issues.append({"severity": "error", "code": code, "message": text, "object_id": node_id})
    if len(nodes) != len(state["nodes"]):
        issue("DUPLICATE_ID", "改编节点标识重复")
    orders = [n.get("sort_id") for n in nodes.values()]
    if len(set(orders)) != len(orders) or any(not isinstance(x, int) for x in orders):
        issue("SORT_ID", "改编排序标识必须为唯一整数")
    active = {key: n for key, n in nodes.items() if not n.get("suppressed") and not n.get("retracted")}
    parents = {}
    for node in nodes.values():
        try:
            Node.model_validate({k: v for k, v in node.items() if k not in _EXTRA})
        except ValueError as exc:
            issue("NODE_SHAPE", str(exc), node["id"])
        for field in ("events", "participants", "predecessors", "successors"):
            for target in node.get(field, []):
                if target not in nodes:
                    issue("NODE_REF", f"{field}引用不存在的改编节点：{target}", node["id"])
                elif field == "events" and node["id"] in active and target in active:
                    parents.setdefault(target, []).append(node["id"])
        for child_id in node.get("events", []):
            child = nodes.get(child_id)
            if child:
                allowed = {"storyline": {"big_event"}, "big_event": {"scene"},
                           "scene": {"event"}, "event": {"event", "action"}}
                if child["node_type"] not in allowed.get(node["node_type"], set()):
                    issue("CONTAINMENT_TYPE", "改编包含层级无效", node["id"])
                if child.get("context_id", "canon") != node.get("context_id", "canon"):
                    issue("CONTEXT_REF", "改编父子节点须在同一语境", node["id"])
        if node.get("point_id"):
            point = nodes.get(node["point_id"])
            if not point or point["node_type"] not in {"storyline", "big_event", "scene", "event", "action"}:
                issue("POINT_REF", "改编时点须引用存在的叙事节点", node["id"])
    def reaches_scene(identifier, seen):
        if identifier in seen:
            return False
        return any(active[p]["node_type"] == "scene" or reaches_scene(p, seen | {identifier})
                   for p in parents.get(identifier, []))
    for identifier, node in active.items():
        if node["node_type"] == "event" and not reaches_scene(identifier, set()):
            issue("EVENT_SCENE", "活动改编事件须归属场景", identifier)
        if node["node_type"] == "action":
            ps = parents.get(identifier, [])
            if len(ps) != 1 or active[ps[0]]["node_type"] != "event" or node.get("events"):
                issue("ACTION_PARENT", "动作须直属恰好一个事件且不含子项", identifier)
        def visit(current, path):
            if current in path:
                return True
            return any(visit(child, path | {current}) for child in active[current].get("events", []) if child in active)
        if visit(identifier, set()):
            issue("CONTAINMENT_CYCLE", "改编包含关系存在循环", identifier)
    definitions = {p["id"]: p for p in state["properties"] if p.get("kind") == "property_definition"}
    for definition in definitions.values():
        if definition.get("value_type") not in {"any", "string", "number", "boolean", "node", "either"} or definition.get("cardinality", "one") not in {"one", "many"}:
            issue("PROPERTY_DEFINITION", "改编属性定义类型或基数无效", definition["id"])
    for row in state["properties"]:
        if row.get("kind") != "property_value":
            continue
        for field in ("node_id", "event_id", "end_event_id"):
            if row.get(field) and row[field] not in nodes:
                issue("PROPERTY_REF", f"属性{field}引用不存在", row.get("id"))
        if row.get("property_id") not in definitions:
            issue("PROPERTY_DEFINITION", "属性定义不存在", row.get("id"))
        definition = definitions.get(row.get("property_id"))
        if definition and not _valid_value(row.get("value"), definition, nodes):
            issue("PROPERTY_VALUE", "改编属性值与定义类型或节点引用不匹配", row.get("id"))
        if row.get("holder_id") and row["holder_id"] not in nodes:
            issue("PROPERTY_HOLDER", "改编认知持有人引用不存在", row.get("id"))
        for field in ("event_id", "end_event_id"):
            point = nodes.get(row.get(field))
            if point and point["node_type"] not in {"storyline", "big_event", "scene", "event", "action"}:
                issue("PROPERTY_POINT", "属性有效时点必须引用叙事节点", row.get("id"))
        start, end = nodes.get(row.get("event_id")), nodes.get(row.get("end_event_id"))
        if start and end and start["sort_id"] >= end["sort_id"]:
            issue("PROPERTY_INTERVAL", "属性失效时点必须晚于生效时点", row.get("id"))
    holder_names = {p["id"] for p in state["properties"] if p.get("kind") == "property_definition"
                    and (p.get("semantic") == "item_holder" or p["id"] == "持有者")}
    for identifier, node in active.items():
        if node["node_type"] != "item":
            continue
        values = [p for p in state["properties"] if p.get("kind") == "property_value"
                  and p.get("node_id") == identifier and p.get("property_id") in holder_names
                  and p.get("scope", "world") == "world" and not p.get("holder_id")
                  and not p.get("valid_to") and not p.get("end_event_id")]
        values.sort(key=lambda p: nodes.get(p.get("event_id"), {}).get("sort_id", 0))
        value = values[-1].get("value") if values else None
        if not isinstance(value, str) or (value not in {"无", "未知"} and
                                         (value not in active or value == identifier)):
            issue("ITEM_HOLDER_REQUIRED", "活动改编物品须有明确world持有者：节点、无或未知", identifier)
    return issues


def _merge_source(state, source_nodes, records, source_revision, *, partial=False, preserve_suppression=False):
    prior_conflicts = state["conflicts"]
    state["conflicts"] = []
    for collection, baseline_key, incoming in (("nodes", "baseline_nodes", source_nodes),
                                                ("properties", "baseline_properties", records)):
        local = {n["id"]: n for n in state[collection]}
        baseline = state[baseline_key]
        fresh = {n["id"]: n for n in incoming}
        for conflict in prior_conflicts:
            identifier, field = conflict["id"], conflict["field"]
            if identifier not in local:
                continue
            if partial and identifier not in fresh:
                state["conflicts"].append(copy.deepcopy(conflict))
                continue
            if field == "deleted":
                if identifier not in fresh:
                    state["conflicts"].append({**conflict, "local": copy.deepcopy(local[identifier]), "source": None})
            elif identifier in fresh and local[identifier].get(field) != fresh[identifier].get(field):
                state["conflicts"].append({**conflict, "local": copy.deepcopy(local[identifier].get(field)),
                                           "source": copy.deepcopy(fresh[identifier].get(field))})
        added = []
        for identifier, source in fresh.items():
            if identifier not in local:
                obj = copy.deepcopy(source)
                if collection == "nodes":
                    obj.update(sort_id=(max([n["sort_id"] for n in local.values()] or [0]) + 1024),
                               suppressed=bool(source.get("suppressed")) if preserve_suppression else False, source_node_id=identifier)
                local[identifier] = obj
                added.append(identifier)
            elif identifier in baseline:
                for key in set(source) | set(baseline[identifier]):
                    old = baseline[identifier].get(key)
                    if local[identifier].get(key) == old:
                        if key in source:
                            local[identifier][key] = copy.deepcopy(source[key])
                        else:
                            local[identifier].pop(key, None)
                    elif (key in {"events", "participants", "predecessors", "successors"}
                          and isinstance(old, list) and isinstance(source.get(key), list)
                          and isinstance(local[identifier].get(key), list)):
                        removed = set(old) - set(source[key])
                        merged = [value for value in local[identifier][key] if value not in removed]
                        merged.extend(value for value in source[key] if value not in old and value not in merged)
                        local[identifier][key] = merged
                    elif source.get(key) != old and local[identifier].get(key) != source.get(key):
                        state["conflicts"].append({"id": identifier, "field": key, "local": copy.deepcopy(local[identifier].get(key)), "source": copy.deepcopy(source.get(key)), "message": "提取与改编均已修改，保留改编值"})
        for identifier in (set() if partial else set(baseline) - set(fresh)):
            if identifier in local:
                state["conflicts"].append({"id": identifier, "field": "deleted", "local": copy.deepcopy(local[identifier]), "source": None, "message": "提取节点或属性已删除，保留改编副本"})
        state[collection] = list(local.values())
        state[baseline_key] = {**baseline, **fresh} if partial else fresh
        if collection == "nodes":
            sequence = list(fresh)
            for identifier in added:
                index = sequence.index(identifier)
                previous = next((key for key in reversed(sequence[:index]) if key in local), None)
                _order(state, identifier, previous)
    unique = {(c["id"], c["field"]): c for c in state["conflicts"]}
    state["conflicts"] = list(unique.values())
    state["source_revision"] = source_revision


class AdaptationOperationsMixin:
    _store: GraphStore

    def adaptation_snapshot(self) -> dict:
        with self._store.connect() as db:
            return _public(_read(db))

    def _adaptation_write(self, mutate, expected_revision, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("改编操作须提供原因")
        with self._store.connect(write=True) as db:
            state = _read(db)
            if expected_revision is not None and (not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision != state["revision"]):
                raise ValueError("改编修订已变化，请刷新后重试")
            mutate(state, db)
            issues = _issues(state)
            blockers = issues
            if blockers:
                raise ValueError(blockers[0]["message"])
            state["revision"] += 1
            db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (_KEY, json.dumps(state, ensure_ascii=False)))
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            db.execute("INSERT INTO actions(id,revision,action,reason,summary) VALUES (?,?,?,?,?)",
                       (uuid4().hex, revision, "adaptation", reason,
                        json.dumps({"adaptation_revision": state["revision"], "issues": len(issues)}, ensure_ascii=False)))
            return {"status": "accepted", **_public(state)}

    def sync_adaptation(self, expected_revision=None, reason="同步提取") -> dict:
        def mutate(state, db):
            source_revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            source_nodes = [json.loads(r[0]) for r in db.execute("SELECT data FROM objects WHERE kind='node'")]
            orders = {json.loads(r[0])["source_id"]: json.loads(r[0])["ordinal"]
                      for r in db.execute("SELECT data FROM objects WHERE kind='source_order'")}
            source_nodes.sort(key=lambda n: (orders.get((n.get("disclosure") or {}).get("source_id"), 10**9),
                                             (n.get("disclosure") or {}).get("start", 10**9)))
            records = [json.loads(r[0]) for r in db.execute("SELECT data FROM property_records ORDER BY rowid")]
            records, diagnostics = _normalize_properties(records, source_nodes)
            state["sync_diagnostics"] = diagnostics
            _merge_source(state, source_nodes, records, source_revision)
        return self._adaptation_write(mutate, expected_revision, reason)

    def import_adaptation_candidates(self) -> list[dict]:
        from .adaptation_import import candidates, source_data
        with self._store.connect() as db:
            nodes, _records, orders, revision = source_data(db)
        return candidates(nodes, orders, revision)

    def import_adaptation(self, moment_ids: list[str], expected_revision=None, reason="从提取导入") -> dict:
        from .adaptation_import import select, source_data
        if not isinstance(moment_ids, list) or not moment_ids or any(not isinstance(x, str) or not x for x in moment_ids):
            raise ValueError("请选择至少一个有效的提取时间点")
        def mutate(state, db):
            nodes, records, orders, revision = source_data(db)
            normalized, diagnostics = _normalize_properties(records, nodes)
            incoming_nodes, incoming_records, summary = select(nodes, normalized, orders, moment_ids, state)
            relevant = {p["id"] for p in incoming_records}
            state["sync_diagnostics"] = [d for d in diagnostics if d.get("object_id") in relevant]
            _merge_source(state, incoming_nodes, incoming_records, revision, partial=True)
            state["import_summary"] = summary
        return self._adaptation_write(mutate, expected_revision, reason)

    def edit_adaptation_node(self, node_id=None, changes=None, after_id=None, parent_id=None,
                             expected_revision=None, reason="编辑改编") -> dict:
        changes = copy.deepcopy(changes or {})
        if set(changes) & {"id", "kind", "sort_id", "source_node_id"}:
            raise ValueError("节点标识与排序由程序分配")
        def mutate(state, db):
            if node_id is None:
                raw = {k: v for k, v in changes.items() if k not in _EXTRA and k != "initial_holder"}
                node = Node.model_validate({"id": "pending", **raw}).model_dump(mode="json")
                node["id"] = _node_id(db, state, node["node_type"])
                node.update(sort_id=max([n["sort_id"] for n in state["nodes"]] or [0]) + 1024,
                            suppressed=bool(changes.get("suppressed", False)), source_node_id=None)
                if changes.get("point_id"):
                    node["point_id"] = changes["point_id"]
                state["nodes"].append(node)
                if node["node_type"] == "item" and "initial_holder" in changes:
                    holder_name = next((p["id"] for p in state["properties"] if p.get("semantic") == "item_holder"), "持有者")
                    if not any(p["id"] == holder_name for p in state["properties"]):
                        state["properties"].append({"id": holder_name, "kind": "property_definition", "value_type": "either",
                                                    "cardinality": "one", "semantic": "item_holder"})
                    state["properties"].append({"id": _value_id(db, state), "kind": "property_value", "node_id": node["id"],
                                                "property_id": holder_name, "value": changes["initial_holder"],
                                                "event_id": changes.get("point_id"), "scope": "world"})
                _order(state, node["id"], after_id)
            else:
                node = _find(state, node_id)
                node.update(changes)
            if parent_id is not None:
                _parent(state, node["id"], parent_id)
        return self._adaptation_write(mutate, expected_revision, reason)

    def copy_adaptation_node(self, node_id, after_id=None, parent_id=None,
                             expected_revision=None, reason="复制改编节点") -> dict:
        def mutate(state, db):
            original = _find(state, node_id)
            # Copy the complete containment subtree and remap internal references.
            originals = {}
            def collect(identifier):
                if identifier in originals:
                    return
                originals[identifier] = _find(state, identifier)
                for child in originals[identifier].get("events", []):
                    collect(child)
            collect(node_id)
            ids = {identifier: _node_id(db, state, node["node_type"]) for identifier, node in originals.items()}
            rank = max([n["sort_id"] for n in state["nodes"]] or [0])
            for index, (identifier, source) in enumerate(originals.items(), 1):
                node = copy.deepcopy(source)
                node.update(id=ids[identifier], source_node_id=None, sort_id=rank + index * 1024)
                for field in ("events", "participants", "predecessors", "successors"):
                    node[field] = [ids.get(value, value) for value in node.get(field, [])]
                if node.get("point_id") in ids:
                    node["point_id"] = ids[node["point_id"]]
                state["nodes"].append(node)
            previous = after_id or original["id"]
            for identifier in originals:
                _order(state, ids[identifier], previous)
                previous = ids[identifier]
            if parent_id is not None:
                _parent(state, ids[node_id], parent_id)
            else:
                for node in state["nodes"]:
                    if node_id in node.get("events", []):
                        node["events"].append(ids[node_id])
            values = [p for p in state["properties"] if p.get("node_id") in ids]
            for value in values:
                row = copy.deepcopy(value)
                row.update(id=_value_id(db, state), node_id=ids[row["node_id"]])
                for field in ("event_id", "end_event_id", "holder_id", "applies_to"):
                    if row.get(field) in ids:
                        row[field] = ids[row[field]]
                row["related_nodes"] = [ids.get(value, value) for value in row.get("related_nodes", [])]
                definition = next((p for p in state["properties"] if p["id"] == row.get("property_id")), {})
                if definition.get("value_type") in {"node", "either"} and isinstance(row.get("value"), str):
                    row["value"] = ids.get(row["value"], row["value"])
                state["properties"].append(row)
        return self._adaptation_write(mutate, expected_revision, reason)

    def move_adaptation_node(self, node_id, after_id=None, parent_id=None,
                             expected_revision=None, reason="移动改编节点") -> dict:
        def mutate(state, _db):
            descendants = []
            def collect(identifier):
                descendants.append(identifier)
                for child in _find(state, identifier).get("events", []):
                    collect(child)
            collect(node_id)
            if after_id in descendants:
                raise ValueError("不能移动到自身或子节点之后")
            previous = after_id
            for identifier in descendants:
                _order(state, identifier, previous)
                previous = identifier
            if parent_id is not None:
                _parent(state, node_id, parent_id)
        return self._adaptation_write(mutate, expected_revision, reason)

    def suppress_adaptation_node(self, node_id, suppressed=True, expected_revision=None,
                                 reason="屏蔽改编节点") -> dict:
        def mutate(state, _db):
            def apply(identifier):
                node = _find(state, identifier)
                node["suppressed"] = bool(suppressed)
                for child in node.get("events", []):
                    apply(child)
            apply(node_id)
        return self._adaptation_write(mutate, expected_revision, reason)

    def delete_adaptation_node(self, node_id, expected_revision, reason) -> dict:
        report = {}
        def mutate(state, db):
            from .screenplay_spans import resolve_span
            from .screenplay_store import load_state, timeline_id
            deleted = set()
            def collect(identifier):
                if identifier in deleted:
                    return
                node = _find(state, identifier)
                deleted.add(identifier)
                for child in node.get("events", []):
                    collect(child)
            collect(node_id)
            script = load_state(db)
            domain_id = state.get("_scope_timeline_id") or timeline_id(db)
            scoped_scripts = {s["id"] for s in script["screenplays"].values() if s["timeline_id"] == domain_id}
            scoped_segments = {s["id"] for s in script["script_segments"].values() if s["screenplay_id"] in scoped_scripts}
            scoped_units = {u["id"] for u in script["script_units"].values() if u["segment_id"] in scoped_segments}
            # A screenplay may retain an invalid range after an anchor disappears,
            # but authored entity references must be corrected explicitly first.
            # Otherwise a physical deletion would strand plans and dialogue and
            # make every subsequent screenplay transaction fail validation.
            referenced = []
            for unit in script["script_units"].values():
                if unit["id"] not in scoped_units:
                    continue
                plan = unit["plan"]
                targets = [plan.get("location_id"), *plan.get("cast_ids", [])]
                targets += [row.get("entity_id") for row in plan.get("continuity_requirements", [])]
                if any(target in deleted for target in targets):
                    referenced.append("场表:" + unit["id"])
            for block in script["script_blocks"].values():
                if block["unit_id"] not in scoped_units:
                    continue
                payload = block["payload"]
                if payload.get("speaker_id") in deleted or payload.get("entity_id") in deleted:
                    referenced.append("正文块:" + block["id"])
            for usage in script["script_entity_usages"].values():
                if usage["unit_id"] in scoped_units and usage["entity_id"] in deleted:
                    referenced.append("实体使用:" + usage["id"])
            if referenced:
                raise ValueError("待删除节点仍被剧本引用，请先显式修正引用；正文未改动：" + "、".join(referenced))
            adaptation = {**state, "timeline_id": domain_id}
            affected = [span for span in script["adaptation_spans"].values()
                        if span["timeline_id"] == domain_id and
                        (deleted & set(resolve_span(adaptation, span)["member_node_ids"])
                         or span["start_node_id"] in deleted or span["end_node_id"] in deleted)]
            state["nodes"] = [n for n in state["nodes"] if n["id"] not in deleted]
            for node in state["nodes"]:
                for field in ("events", "participants", "predecessors", "successors"):
                    node[field] = [value for value in node.get(field, []) if value not in deleted]
                if node.get("point_id") in deleted:
                    node.pop("point_id")
                occurrence = node.get("occurrence") or {}
                refs = [occurrence.get("point"), (occurrence.get("start") or {}).get("ref"),
                        (occurrence.get("end") or {}).get("ref")]
                if any(isinstance(ref, dict) and ref.get("event_id") in deleted for ref in refs):
                    node["occurrence"] = None
            definitions = {p["id"]: p for p in state["properties"] if p.get("kind") == "property_definition"}
            retained = []
            for row in state["properties"]:
                if row.get("kind") == "property_value":
                    if any(row.get(field) in deleted for field in ("node_id", "event_id", "end_event_id", "holder_id", "applies_to")):
                        continue
                    definition = definitions.get(row.get("property_id"), {})
                    if definition.get("value_type") in {"node", "either"} and isinstance(row.get("value"), str) and row["value"] in deleted:
                        continue
                    row["related_nodes"] = [key for key in row.get("related_nodes", []) if key not in deleted]
                retained.append(row)
            state["properties"] = retained
            current_ids = {row["id"] for row in state["nodes"] + retained}
            state["conflicts"] = [c for c in state["conflicts"] if c["id"] in current_ids]
            report["deleted_node_ids"] = sorted(deleted)
            report["affected_spans"] = [{"span_id": span["id"], **resolve_span({**state, "timeline_id": domain_id}, span)}
                                        for span in affected]
        result = self._adaptation_write(mutate, expected_revision, reason)
        return result | report

    def set_adaptation_property(self, node_id, property_id, value=None, event_id=None,
                                record_id=None, expire=False, expected_revision=None,
                                reason="编辑改编属性") -> dict:
        def mutate(state, db):
            _find(state, node_id)
            if event_id is not None:
                event = _find(state, event_id)
                if event["node_type"] not in {"event", "action", "scene", "big_event", "storyline"}:
                    raise ValueError("属性时点须为叙事节点")
            row = next((p for p in state["properties"] if p["id"] == record_id), None) if record_id else None
            if record_id and (not row or row.get("node_id") != node_id or row.get("property_id") != property_id):
                raise ValueError("改编属性记录不存在或不属于当前节点属性")
            if expire:
                if row is None or event_id is None:
                    raise ValueError("失效属性须指定记录与时点")
                row["end_event_id"] = event_id
                return
            if not any(p["id"] == property_id and p.get("kind") == "property_definition" for p in state["properties"]):
                if not property_id or not isinstance(property_id, str):
                    raise ValueError("属性名称不能为空")
                state["properties"].append({"id": property_id, "kind": "property_definition", "value_type": "any", "cardinality": "one"})
            if row is not None:
                if event_id is None or event_id == row.get("event_id"):
                    row["value"] = value
                    return
                row["end_event_id"] = event_id
            state["properties"].append({"id": _value_id(db, state), "kind": "property_value", "node_id": node_id,
                                        "property_id": property_id, "value": copy.deepcopy(value), "event_id": event_id,
                                        "end_event_id": None, "scope": "world"})
        return self._adaptation_write(mutate, expected_revision, reason)

    def check_adaptation(self) -> dict:
        with self._store.connect() as db:
            state = _read(db)
        issues = _issues(state)
        return {"ok": not issues, "revision": state["revision"], "issues": issues,
                "source_revision": state["source_revision"], "conflicts": state["conflicts"],
                "sync_diagnostics": state.get("sync_diagnostics", [])}
