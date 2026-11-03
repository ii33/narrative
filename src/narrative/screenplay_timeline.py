"""Per-screenplay independent timeline copies and explicit adaptation imports."""
from __future__ import annotations

import copy

from .adaptation_operations import AdaptationOperationsMixin, _issues, _merge_source, _public
from .screenplay_models import ScriptTimeline
from .screenplay_store import expect_revision, new_id, now, read_context, write_transaction

MOMENTS = {"storyline", "big_event", "scene", "event", "action"}


def new_script_timeline(screenplay_id, source):
    stamp = now()
    return ScriptTimeline(id=new_id("script_timeline"), screenplay_id=screenplay_id,
                          source_timeline_id=source["timeline_id"], created_at=stamp, updated_at=stamp).model_dump(mode="json")


def _timeline_row(state, screenplay_id):
    screenplay = state["screenplays"].get(screenplay_id)
    if not screenplay:
        raise ValueError("剧本不存在")
    row = state["script_timelines"].get(screenplay["timeline_id"])
    if not row or row["screenplay_id"] != screenplay_id:
        raise ValueError("剧本独立时间线不存在或归属错误")
    return row


def screenplay_timeline(state, source_adaptation, screenplay_id):
    # source_adaptation is deliberately not a fallback: imports are explicit.
    row = _timeline_row(state, screenplay_id)
    snapshot = ScriptTimeline.model_validate(row).model_dump(mode="json")
    return _public(snapshot) | {"timeline_id": row["id"]}


def timeline_owner(state, identifier):
    row = state["script_timelines"].get(identifier)
    if not row:
        raise ValueError("剧本时间线不存在")
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
    cuts = [nodes[key]["sort_id"] for key in primary]
    values = [r for r in source["properties"] if r.get("kind") == "property_value"]
    definitions = {r["id"]: r for r in source["properties"] if r.get("kind") == "property_definition"}
    selected_values = set()
    def add(identifier):
        if isinstance(identifier, str) and identifier in nodes:
            included.add(identifier)
    def start(row):
        return nodes.get(row.get("event_id"), {}).get("sort_id", -1)
    def end(row):
        return nodes.get(row.get("end_event_id"), {}).get("sort_id", float("inf"))
    def relevant(row):
        return row.get("event_id") in primary or any(start(row) <= cut < end(row) for cut in cuts)
    def include_value(row):
        selected_values.add(row["id"])
        for field in ("node_id", "event_id", "end_event_id", "holder_id", "applies_to"):
            add(row.get(field))
        for identifier in row.get("related_nodes", []):
            add(identifier)
        if definitions.get(row.get("property_id"), {}).get("value_type") in {"node", "either"}:
            add(row.get("value"))
    while True:
        before = len(included), len(selected_values)
        for identifier in list(included):
            node = nodes[identifier]
            if node["node_type"] in MOMENTS:
                for parent in parents.get(identifier, []):
                    add(parent)
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
        for identifier in list(included):
            if nodes[identifier]["node_type"] != "item" or nodes[identifier].get("retracted") or nodes[identifier].get("suppressed"):
                continue
            holders = [r for r in values if r.get("node_id") == identifier and r.get("scope", "world") == "world"
                       and not r.get("holder_id") and not r.get("end_event_id")
                       and (r.get("property_id") == "持有者" or definitions.get(r.get("property_id"), {}).get("semantic") == "item_holder")]
            if holders:
                include_value(max(holders, key=start))
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
    summary = {"selected_moment_ids": list(dict.fromkeys(moment_ids)),
               "imported_moment_ids": [n["id"] for n in incoming if n["id"] in primary],
               "dependency_moment_ids": [n["id"] for n in incoming if n["node_type"] in MOMENTS and n["id"] not in primary],
               "node_ids": [n["id"] for n in incoming], "property_ids": [r["id"] for r in records]}
    return incoming, records, summary


def _append_interval_check(local, added_property_ids):
    """Reject new single-value interval overlap; never close authored values."""
    nodes = {node["id"]: node for node in local["nodes"]}
    definitions = {row["id"]: row for row in local["properties"] if row.get("kind") == "property_definition"}
    groups = {}
    for row in local["properties"]:
        if row.get("kind") != "property_value" or definitions.get(row.get("property_id"), {}).get("cardinality", "one") != "one":
            continue
        key = row.get("node_id"), row.get("property_id"), row.get("scope") or "world", row.get("holder_id")
        groups.setdefault(key, []).append(row)
    def start(row):
        return nodes.get(row.get("event_id"), {}).get("sort_id", float("-inf"))
    def end(row):
        return nodes.get(row.get("end_event_id"), {}).get("sort_id", float("inf"))
    for rows in groups.values():
        for row in (item for item in rows if item["id"] in added_property_ids):
            for previous in rows:
                if previous["id"] == row["id"] or (previous["id"] in added_property_ids and previous["id"] > row["id"]):
                    continue
                if max(start(row), start(previous)) < min(end(row), end(previous)):
                    raise ValueError("追加属性有效区间与已有单值状态重叠，未关闭或改写本地值；请先显式调整区间："
                                     + previous["id"] + " / " + row["id"])


def _append_selection(local, incoming_nodes, incoming_records, summary, source_revision, source_records):
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
    boundary_updates = []
    for source in source_records:
        if source.get("kind") != "property_value" or source.get("end_event_id") not in new_ids:
            continue
        previous = existing_properties.get(source["id"])
        baseline = local["baseline_properties"].get(source["id"])
        if not previous or previous != baseline or previous.get("end_event_id") is not None:
            continue
        boundary_fields = {"end_event_id", "source_valid_to"}
        if ({key: value for key, value in previous.items() if key not in boundary_fields} !=
            {key: value for key, value in source.items() if key not in boundary_fields}):
            continue
        previous["end_event_id"] = source["end_event_id"]
        baseline["end_event_id"] = source["end_event_id"]
        if "source_valid_to" in source:
            previous["source_valid_to"] = copy.deepcopy(source["source_valid_to"])
            baseline["source_valid_to"] = copy.deepcopy(source["source_valid_to"])
        boundary_updates.append({"record_id": source["id"], "previous_end_event_id": None,
                                 "end_event_id": source["end_event_id"]})
    incoming_definitions = {row["id"]: row for row in incoming_records if row.get("kind") == "property_definition"}
    for source in incoming_records:
        if source["id"] not in existing_properties:
            if source.get("kind") == "property_value" and source.get("property_id") in existing_properties:
                previous_definition = existing_properties[source["property_id"]]
                source_definition = incoming_definitions.get(source["property_id"], {})
                if any(previous_definition.get(key) != source_definition.get(key)
                       for key in ("value_type", "cardinality", "target_node_types", "semantic", "symmetric")):
                    raise ValueError("追加属性定义与本地定义不一致，未更新本地定义；请先显式处理：" + source["property_id"])
            local["properties"].append(copy.deepcopy(source))
            local["baseline_properties"][source["id"]] = copy.deepcopy(source)
    _append_interval_check(local, set(added_properties))
    local["source_revision"] = source_revision
    local["import_summary"] = {**summary, "mode": "append", "added_node_ids": added_nodes,
                               "added_property_ids": added_properties, "structural_updates": structural_updates,
                               "boundary_updates": boundary_updates,
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
            source_ids = {n["id"] for n in source["nodes"]} | {r["id"] for r in source["properties"]}
            current = {n["id"]: n for n in local["nodes"] + local["properties"]}
            for identifier in set(local["baseline_nodes"]) | set(local["baseline_properties"]):
                if identifier not in source_ids and identifier in current:
                    local["conflicts"].append({"id": identifier, "field": "deleted", "local": copy.deepcopy(current[identifier]),
                                                "source": None, "message": "改编来源已删除，保留剧本独立副本"})
            _merge_source(local, nodes, records, source["revision"], partial=True, preserve_suppression=True)
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
            _append_selection(local, nodes, records, summary, source["revision"], source["properties"])
            local["revision"] += 1
            local["updated_at"] = now()
            return screenplay_timeline(state, source, screenplay_id) | {"status": "accepted"}
        return write_transaction(self, reason, mutate)

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

    def set_screenplay_timeline_property(self, screenplay_id, node_id, property_id, value=None, event_id=None,
                                         record_id=None, expire=False, expected_timeline_revision=None, reason=""):
        return _TimelineEditor(self, screenplay_id).set_adaptation_property(node_id, property_id, value, event_id,
                                                                          record_id, expire, expected_timeline_revision, reason)

    def copy_screenplay_timeline_node(self, screenplay_id, node_id, after_id=None, parent_id=None,
                                      expected_timeline_revision=None, reason=""):
        return _TimelineEditor(self, screenplay_id).copy_adaptation_node(node_id, after_id, parent_id,
                                                                       expected_timeline_revision, reason)

    def delete_screenplay_timeline_node(self, screenplay_id, node_id, expected_timeline_revision, reason):
        return _TimelineEditor(self, screenplay_id).delete_adaptation_node(node_id, expected_timeline_revision, reason)
