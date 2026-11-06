"""Per-screenplay independent timeline copies and explicit adaptation imports."""
from __future__ import annotations

import copy

from .adaptation_operations import AdaptationOperationsMixin, _issues, _merge_source, _public
from .content_errors import ContentGuardError, content_write
from .screenplay_models import ScriptTimeline
from .screenplay_store import expect_revision, new_id, now, read_context, write_transaction
from .state_anchors import anchor_nodes, merge_anchors, selection_anchors

MOMENTS = {"storyline", "big_event", "scene", "event", "action"}


def new_script_timeline(screenplay_id, source):
    stamp = now()
    return ScriptTimeline(id=new_id("script_timeline"), screenplay_id=screenplay_id,
                          source_timeline_id=source["timeline_id"], created_at=stamp, updated_at=stamp).model_dump(mode="json")


def _timeline_row(state, screenplay_id):
    screenplay = state["screenplays"].get(screenplay_id)
    if not screenplay:
        raise ContentGuardError("剧本不存在")
    row = state["script_timelines"].get(screenplay["timeline_id"])
    if not row or row["screenplay_id"] != screenplay_id:
        raise ContentGuardError("剧本独立时间线不存在或归属错误")
    return row


def screenplay_timeline(state, source_adaptation, screenplay_id):
    # source_adaptation is deliberately not a fallback: imports are explicit.
    row = _timeline_row(state, screenplay_id)
    snapshot = ScriptTimeline.model_validate(row).model_dump(mode="json")
    return _public(snapshot) | {"timeline_id": row["id"]}


def timeline_owner(state, identifier):
    row = state["script_timelines"].get(identifier)
    if not row:
        raise ContentGuardError("剧本时间线不存在")
    return row["screenplay_id"]


def _candidates(source):
    nodes = {n["id"]: n for n in source["nodes"] if n["node_type"] in MOMENTS and not n.get("retracted")}
    parents = {}
    for node in nodes.values():
        for child in node.get("events", []):
            if child in nodes:
                parents.setdefault(child, []).append(node["id"])
    placed, result = set(), []
    def emit(identifier, parent_id, depth):
        if identifier in placed:
            return
        placed.add(identifier)
        node = nodes[identifier]
        children = sorted([nodes[key] for key in node.get("events", []) if key in nodes], key=lambda n: n["sort_id"])
        result.append({"id": identifier, "name": node["name"], "node_type": node["node_type"],
                       "parent_id": parent_id, "depth": depth, "children": [n["id"] for n in children],
                       "sort_id": node["sort_id"], "suppressed": bool(node.get("suppressed")),
                       "source_timeline_id": source["timeline_id"], "source_revision": source["revision"]})
        for child in children:
            emit(child["id"], identifier, depth + 1)
    ordered = sorted(nodes.values(), key=lambda n: n["sort_id"])
    for node in ordered:
        if not parents.get(node["id"]):
            emit(node["id"], None, 0)
    for node in ordered:
        emit(node["id"], None, 0)
    return sorted(result, key=lambda row: row["sort_id"])


def _selection(source, moment_ids, local):
    nodes = {n["id"]: n for n in source["nodes"]}
    primary = set()
    def descend(identifier):
        if identifier in primary:
            return
        node = nodes.get(identifier)
        if not node or node["node_type"] not in MOMENTS or node.get("retracted"):
            raise ValueError("请选择有效的改编时间点")
        primary.add(identifier)
        for child in node.get("events", []):
            if child in nodes and not nodes[child].get("retracted"):
                descend(child)
    for identifier in moment_ids:
        descend(identifier)
    parents = {}
    for node in nodes.values():
        for child in node.get("events", []):
            parents.setdefault(child, []).append(node["id"])
    included = set(primary)
    boundary_ids = set()
    source_points = {**anchor_nodes(source), **nodes}
    cuts = [nodes[key]["sort_id"] for key in primary]
    values = [r for r in source["properties"] if r.get("kind") == "property_change"]
    definitions = {r["id"]: r for r in source["properties"] if r.get("kind") == "property_definition"}
    selected_values = set()
    def add(identifier):
        if isinstance(identifier, str) and identifier in source_points:
            if identifier not in nodes or (nodes[identifier]["node_type"] in MOMENTS and identifier not in primary):
                boundary_ids.add(identifier)
            else:
                included.add(identifier)
    def relevant(row):
        point = source_points.get(row.get("event_id"))
        return point is not None and bool(cuts) and point["sort_id"] <= max(cuts)
    def include_value(row):
        selected_values.add(row["id"])
        for field in ("node_id", "event_id", "holder_id", "applies_to"):
            add(row.get(field))
        for identifier in row.get("related_nodes", []):
            add(identifier)
        if definitions.get(row.get("property_id"), {}).get("value_type") in {"node", "either"}:
            value = row.get("value")
            for target in value if isinstance(value, list) else [value]:
                add(target)
    while True:
        before = len(included), len(selected_values)
        for identifier in list(included):
            node = nodes[identifier]
            if node["node_type"] in MOMENTS:
                for parent in parents.get(identifier, []):
                    included.add(parent)
            if identifier in primary:
                for participant in node.get("participants", []):
                    add(participant)
            add(node.get("point_id"))
            occurrence = node.get("occurrence") or {}
            for ref in (occurrence.get("point"), (occurrence.get("start") or {}).get("ref"),
                        (occurrence.get("end") or {}).get("ref")):
                if isinstance(ref, dict):
                    add(ref.get("event_id"))
        for row in values:
            owner = row.get("node_id")
            if row.get("event_id") in primary or (owner in included and
                (owner in primary or nodes[owner]["node_type"] not in MOMENTS) and relevant(row)):
                include_value(row)
        if before == (len(included), len(selected_values)):
            break
    existing = {n["id"] for n in local["nodes"]}
    visible = included | existing
    incoming = []
    for node in sorted([nodes[key] for key in included], key=lambda n: n["sort_id"]):
        row = copy.deepcopy(node)
        # Local order and source provenance are program-owned, not merged fields.
        row.pop("sort_id")
        row.pop("source_node_id", None)
        for field in ("events", "predecessors", "successors"):
            row[field] = [key for key in row.get(field, []) if key in visible]
            previous = next((item for item in local["nodes"] if item["id"] == row["id"]), {})
            row[field].extend(key for key in previous.get(field, [])
                              if key not in nodes and key in existing and key not in row[field])
        incoming.append(row)
    records = [copy.deepcopy(r) for r in values if r["id"] in selected_values]
    definition_ids = {r["property_id"] for r in records}
    records = [copy.deepcopy(definitions[key]) for key in definitions if key in definition_ids] + records
    summary = {"state_anchors": selection_anchors(source, included | existing, boundary_ids),
               "selected_moment_ids": list(dict.fromkeys(moment_ids)),
               "imported_moment_ids": [n["id"] for n in incoming if n["id"] in primary],
               "dependency_moment_ids": [n["id"] for n in incoming if n["node_type"] in MOMENTS and n["id"] not in primary],
               "node_ids": [n["id"] for n in incoming], "property_ids": [r["id"] for r in records]}
    return incoming, records, summary


def _append_selection(local, incoming_nodes, incoming_records, summary, source_revision):
    """Append new identities at the tail; existing authored objects stay intact."""
    existing_nodes = {node["id"]: node for node in local["nodes"]}
    existing_properties = {row["id"]: row for row in local["properties"]}
    added_nodes = [row["id"] for row in incoming_nodes if row["id"] not in existing_nodes]
    added_properties = [row["id"] for row in incoming_records if row["id"] not in existing_properties]
    new_ids = set(added_nodes)
    rank = max((node["sort_id"] for node in local["nodes"]), default=0)
    structural_updates = []
    for source in incoming_nodes:
        identifier = source["id"]
        if identifier in existing_nodes:
            parent = existing_nodes[identifier]
            # Only newly appended descendants may extend an existing container.
            # Never reparent a previously imported or locally moved node.
            children = [child for child in source.get("events", []) if child in new_ids and child not in parent.get("events", [])]
            if children:
                parent.setdefault("events", []).extend(children)
                baseline = local["baseline_nodes"].get(identifier)
                if baseline is not None:
                    baseline.setdefault("events", []).extend(child for child in children if child not in baseline.get("events", []))
                structural_updates.append({"node_id": identifier, "added_child_ids": children})
            continue
        row = copy.deepcopy(source)
        rank += 1024
        row.update(sort_id=rank, source_node_id=identifier)
        row["events"] = [child for child in row.get("events", []) if child in new_ids]
        local["nodes"].append(row)
        local["baseline_nodes"][identifier] = copy.deepcopy(source)
    incoming_definitions = {row["id"]: row for row in incoming_records if row.get("kind") == "property_definition"}
    for source in incoming_records:
        if source["id"] not in existing_properties:
            if source.get("kind") == "property_change" and source.get("property_id") in existing_properties:
                previous_definition = existing_properties[source["property_id"]]
                source_definition = incoming_definitions.get(source["property_id"], {})
                if any(previous_definition.get(key) != source_definition.get(key)
                       for key in ("value_type", "cardinality", "target_node_types", "semantic", "symmetric")):
                    raise ValueError("追加属性定义与本地定义不一致，未更新本地定义；请先显式处理：" + source["property_id"])
            local["properties"].append(copy.deepcopy(source))
            local["baseline_properties"][source["id"]] = copy.deepcopy(source)
    merge_anchors(local, summary.pop("state_anchors", []))
    local["source_revision"] = source_revision
    local["import_summary"] = {**summary, "mode": "append", "added_node_ids": added_nodes,
                               "added_property_ids": added_properties, "structural_updates": structural_updates,
                               "diagnostics": [],
                               "skipped_existing_node_ids": [row["id"] for row in incoming_nodes if row["id"] in existing_nodes],
                               "skipped_existing_property_ids": [row["id"] for row in incoming_records if row["id"] in existing_properties]}


class _TimelineEditor(AdaptationOperationsMixin):
    def __init__(self, ops, screenplay_id):
        self.ops, self.screenplay_id, self._store = ops, screenplay_id, ops._store

    def _adaptation_write(self, mutate, expected_revision, reason):
        def scoped(state, source, db):
            local = _timeline_row(state, self.screenplay_id)
            expect_revision(local, expected_revision)
            local["_scope_timeline_id"] = local["id"]
            try:
                mutate(local, db)
            finally:
                local.pop("_scope_timeline_id", None)
            issues = _issues(local)
            if issues:
                raise ValueError(issues[0]["message"])
            local["revision"] += 1
            local["updated_at"] = now()
            return screenplay_timeline(state, source, self.screenplay_id) | {"status": "accepted"}
        return write_transaction(self.ops, reason, scoped)


class ScreenplayTimelineOperationsMixin:
    def read_screenplay_timeline(self, screenplay_id):
        state, source = read_context(self)
        return screenplay_timeline(state, source, screenplay_id)

    def import_screenplay_timeline_candidates(self, screenplay_id):
        state, source = read_context(self)
        local = _timeline_row(state, screenplay_id)
        imported = {node["id"] for node in local["nodes"]}
        return [{**row, "imported": row["id"] in imported} for row in _candidates(source)]

    def import_screenplay_timeline(self, screenplay_id, moment_ids, expected_timeline_revision,
                                   expected_adaptation_revision, reason):
        if not isinstance(moment_ids, list) or not moment_ids or any(not isinstance(x, str) or not x for x in moment_ids):
            raise ValueError("请选择至少一个改编时间点")
        def mutate(state, source, _db):
            local = _timeline_row(state, screenplay_id)
            expect_revision(local, expected_timeline_revision)
            expect_revision(source, expected_adaptation_revision)
            nodes, records, summary = _selection(source, moment_ids, local)
            summary["diagnostics"] = []
            source_ids = {n["id"] for n in source["nodes"]} | {r["id"] for r in source["properties"]}
            current = {n["id"]: n for n in local["nodes"] + local["properties"]}
            for identifier in set(local["baseline_nodes"]) | set(local["baseline_properties"]):
                if identifier not in source_ids and identifier in current:
                    local["conflicts"].append({"id": identifier, "field": "deleted", "local": copy.deepcopy(current[identifier]),
                                                "source": None, "message": "改编来源已删除，保留剧本独立副本"})
            _merge_source(local, nodes, records, source["revision"], partial=True, preserve_suppression=True)
            merge_anchors(local, summary.pop("state_anchors", []))
            local["import_summary"] = summary
            local["revision"] += 1
            local["updated_at"] = now()
            return screenplay_timeline(state, source, screenplay_id) | {"status": "accepted"}
        return write_transaction(self, reason, mutate)

    def append_screenplay_timeline(self, screenplay_id, moment_ids, expected_timeline_revision,
                                   expected_adaptation_revision, reason):
        if not isinstance(moment_ids, list) or not moment_ids or any(not isinstance(x, str) or not x for x in moment_ids):
            raise ValueError("请选择至少一个改编时间点")
        def mutate(state, source, _db):
            local = _timeline_row(state, screenplay_id)
            expect_revision(local, expected_timeline_revision)
            expect_revision(source, expected_adaptation_revision)
            nodes, records, summary = _selection(source, moment_ids, local)
            _append_selection(local, nodes, records, summary, source["revision"])
            local["revision"] += 1
            local["updated_at"] = now()
            return screenplay_timeline(state, source, screenplay_id) | {"status": "accepted"}
        return write_transaction(self, reason, mutate)

    @content_write
    def edit_screenplay_timeline_node(self, screenplay_id, node_id=None, changes=None, after_id=None,
                                      parent_id=None, expected_timeline_revision=None, reason=""):
        return _TimelineEditor(self, screenplay_id).edit_adaptation_node(node_id, changes, after_id, parent_id,
                                                                       expected_timeline_revision, reason)

    def move_screenplay_timeline_node(self, screenplay_id, node_id, after_id=None, parent_id=None,
                                      expected_timeline_revision=None, reason=""):
        return _TimelineEditor(self, screenplay_id).move_adaptation_node(node_id, after_id, parent_id,
                                                                       expected_timeline_revision, reason)

    def suppress_screenplay_timeline_node(self, screenplay_id, node_id, suppressed=True,
                                          expected_timeline_revision=None, reason=""):
        return _TimelineEditor(self, screenplay_id).suppress_adaptation_node(node_id, suppressed,
                                                                           expected_timeline_revision, reason)

    @content_write
    def set_screenplay_timeline_property(self, screenplay_id, node_id, property_id, event_id, op, value=None,
                                         record_id=None, expected_timeline_revision=None, reason="",
                                         scope=None, holder_id=None, context_id=None):
        return _TimelineEditor(self, screenplay_id).set_adaptation_property(
            node_id, property_id, event_id, op, value=value, record_id=record_id,
            expected_revision=expected_timeline_revision, reason=reason,
            scope=scope, holder_id=holder_id, context_id=context_id)

    def copy_screenplay_timeline_node(self, screenplay_id, node_id, after_id=None, parent_id=None,
                                      expected_timeline_revision=None, reason=""):
        return _TimelineEditor(self, screenplay_id).copy_adaptation_node(node_id, after_id, parent_id,
                                                                       expected_timeline_revision, reason)

    def delete_screenplay_timeline_node(self, screenplay_id, node_id, expected_timeline_revision, reason):
        return _TimelineEditor(self, screenplay_id).delete_adaptation_node(node_id, expected_timeline_revision, reason)
