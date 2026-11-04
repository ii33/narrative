"""Pure inclusive-anchor span resolution and versioned actual-input fingerprints."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .adaptation_views import _View

FINGERPRINT_VERSION = 2
_TIME_TYPES = {"event", "action"}
_CONTAINER_TYPES = {"storyline", "big_event", "scene"}
_EXCLUDED = {"sort_id", "revision", "created_at", "updated_at", "accepted_revision"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items() if key not in _EXCLUDED}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def span_endpoints(adaptation, selection_ids=None, start_node_id=None, end_node_id=None):
    nodes = {n["id"]: n for n in adaptation["nodes"]}
    axis = sorted([n for n in nodes.values() if n["node_type"] in _TIME_TYPES], key=lambda n: n["sort_id"])
    rank = {n["id"]: i for i, n in enumerate(axis)}
    view = _View(adaptation)
    if selection_ids is not None:
        if start_node_id is not None or end_node_id is not None:
            raise ValueError("选区与显式起止锚点不能混用")
        if not isinstance(selection_ids, list) or not selection_ids or any(not isinstance(x, str) for x in selection_ids):
            raise ValueError("请选择至少一个改编时间点")
        expanded = set()
        def expand(identifier, seen, explicit=False):
            node = nodes.get(identifier)
            if not node:
                raise ValueError("选中的改编节点不存在")
            if identifier in seen:
                raise ValueError("改编包含关系存在循环")
            if not view.active(identifier) and not (explicit and node["node_type"] in _TIME_TYPES):
                return
            if node["node_type"] in _TIME_TYPES:
                expanded.add(identifier)
            if node["node_type"] in _TIME_TYPES | _CONTAINER_TYPES:
                for child in node.get("events", []):
                    expand(child, seen | {identifier})
            else:
                raise ValueError("时间段只能选择五级叙事时间点")
        for identifier in selection_ids:
            expand(identifier, set(), explicit=True)
        if not expanded:
            raise ValueError("选区没有可解析的事件或动作")
        ordered = sorted(expanded, key=rank.__getitem__)
        return ordered[0], ordered[-1], ordered
    if not isinstance(start_node_id, str) or not isinstance(end_node_id, str):
        raise ValueError("须提供选区或起止锚点")
    if start_node_id not in rank or end_node_id not in rank:
        raise ValueError("时间段边界须引用现有改编事件或动作")
    return start_node_id, end_node_id, None


class _GenerationView(_View):
    """State time survives narrative suppression; hidden narrative never does."""
    def __init__(self, snapshot):
        super().__init__(snapshot)
        for row in self.records:
            anchor = self.boundary(row.get("event_id"))
            owner = self.nodes.get(row.get("node_id"))
            if not anchor or not owner or not self.active(owner["id"]):
                continue
            if anchor.get("context_id", "canon") != owner.get("context_id", "canon"):
                continue
            for identifier in [owner["id"], row.get("holder_id"), *self.targets(row)]:
                if identifier in self.nodes:
                    self.introductions[identifier] = min(self.introductions[identifier], anchor["sort_id"])
        for node in self.ordered:
            if self.boundary(node.get("point_id")) is not None:
                self.introductions[node["id"]] = self.rank(node["point_id"])

    def visible(self, identifier, cutoff=None, context=None):
        node = self.nodes.get(identifier)
        return bool(node and self.active(identifier)
                    and (context is None or node.get("context_id", "canon") == context)
                    and (not node.get("point_id") or self.boundary(node["point_id"]) is not None)
                    and (cutoff is None or self.introduction(node) <= cutoff))


def _parent_event(view, identifier):
    node = view.nodes.get(identifier)
    if not node or not view.active(identifier):
        return None
    if node["node_type"] == "event":
        return identifier
    if node["node_type"] != "action":
        return None
    queue, seen = list(view.parents.get(identifier, [])), set()
    while queue:
        parent = queue.pop(0)
        if parent in seen:
            continue
        seen.add(parent)
        candidate = view.nodes.get(parent)
        if candidate and candidate["node_type"] == "event" and view.active(parent):
            return parent
        queue.extend(view.parents.get(parent, []))
    return None


def generation_event_ids(adaptation, effective_node_ids):
    """Stable source-event coverage IDs; existing actions fold into their event."""
    view = _GenerationView(adaptation)
    return list(dict.fromkeys(event for key in effective_node_ids if (event := _parent_event(view, key))))


def _state_visible(view, row, identifier):
    target = view.nodes.get(identifier)
    if not target or not view.active(identifier):
        return False
    cutoff, context = target["sort_id"], target.get("context_id", "canon")
    if not view.visible(row.get("node_id"), cutoff, context):
        return False
    if row.get("holder_id") and not view.visible(row["holder_id"], cutoff, context):
        return False
    for field in ("event_id", "end_event_id"):
        anchor_id = row.get(field)
        if not anchor_id:
            continue
        anchor = view.boundary(anchor_id)
        if not anchor or anchor.get("context_id", "canon") != context:
            return False
        # A suppressed endpoint still locates a legitimate property's interval.
        if field == "event_id" and anchor["sort_id"] > cutoff:
            return False
        if field == "end_event_id" and anchor["sort_id"] <= cutoff:
            return False
    if row.get("applies_to") and row["applies_to"] not in {identifier, _parent_event(view, identifier)}:
        return False
    definition = view.definitions.get(row.get("property_id"), {})
    if definition.get("value_type") in {"node", "either"}:
        value = row.get("value")
        values = value if isinstance(value, list) else [value]
        for ref in values:
            if isinstance(ref, str) and ref in view.nodes and not view.visible(ref, cutoff, context):
                return False
            if definition.get("value_type") == "node" and (not isinstance(ref, str) or ref not in view.nodes):
                return False
    return True


def generation_state_visible(adaptation, row, event_id):
    """Whether a stored fact applies at an effective point, without its prose."""
    return _state_visible(_GenerationView(adaptation), row, event_id)


def _safe_node(view, node, effective):
    identifier = node["id"]
    if not view.active(identifier) or (node["node_type"] in _TIME_TYPES and identifier not in effective):
        return None
    if node["node_type"] in _CONTAINER_TYPES:
        return {"id": identifier, "node_type": node["node_type"], "context_id": node.get("context_id", "canon"), "reference_only": True}
    result = {key: copy.deepcopy(node[key]) for key in ("id", "node_type", "name", "aliases", "subtype", "context_id") if key in node}
    if node["node_type"] in _TIME_TYPES:
        cutoff, context = node["sort_id"], node.get("context_id", "canon")
        result["participants"] = [key for key in node.get("participants", [])
                                  if view.visible(key, cutoff, context) and view.nodes[key]["node_type"] not in MOMENT_TYPES]
        for field in ("events", "predecessors", "successors"):
            result[field] = [key for key in node.get(field, []) if key in effective and view.active(key)]
        result["source_event_id"] = _parent_event(view, identifier)
        occurrence = node.get("occurrence")
        if isinstance(occurrence, dict):
            def ref(value):
                return {key: copy.deepcopy(value[key]) for key in ("timeline_id", "form", "number", "datetime", "event_id", "boundary") if key in value} if isinstance(value, dict) else None
            result["occurrence"] = {"kind": occurrence.get("kind", "interval")}
            if occurrence.get("point"):
                result["occurrence"]["point"] = ref(occurrence["point"])
            for side in ("start", "end"):
                bound = occurrence.get(side)
                if isinstance(bound, dict):
                    result["occurrence"][side] = {"state": bound.get("state", "unknown"), "ref": ref(bound.get("ref"))}
    return result


MOMENT_TYPES = _TIME_TYPES | _CONTAINER_TYPES


def generation_node(adaptation, node_or_id, effective_node_ids=None):
    """Generation-only node identity/content projection; never return provenance."""
    view = _GenerationView(adaptation)
    identifier = node_or_id.get("id") if isinstance(node_or_id, dict) else node_or_id
    node = view.nodes.get(identifier)
    if not node:
        return None
    effective = set(effective_node_ids) if effective_node_ids is not None else {
        key for key, row in view.nodes.items() if row["node_type"] in _TIME_TYPES and view.active(key)}
    return _safe_node(view, node, effective)


def _state_projection(view, row, effective, event_id):
    cutoff = view.rank(event_id)
    context = view.nodes[event_id].get("context_id", "canon")
    result = {key: copy.deepcopy(row[key]) for key in ("id", "node_id", "property_id", "value", "scope", "holder_id", "category") if key in row}
    result["related_nodes"] = [key for key in row.get("related_nodes", [])
                               if view.visible(key, cutoff, context)
                               and (view.nodes[key]["node_type"] not in MOMENT_TYPES or key in effective)]
    if row.get("applies_to") in effective:
        result["applies_to"] = row["applies_to"]
    return result


def _location_record(view, row, event_id):
    definition = view.definitions.get(row.get("property_id"), {})
    return (row.get("node_id") == event_id and (row.get("scope") or "world") == "world" and not row.get("holder_id")
            and (row.get("property_id") in {"地点", "发生地点", "location_of_event", "event_location"}
                 or definition.get("semantic") in {"location_of_event", "event_location"}))


def _input(adaptation, members, effective):
    view = _GenerationView(adaptation)
    nodes, rows, definitions = view.nodes, view.records, view.definitions
    targets = set(effective)
    source_event_ids = list(dict.fromkeys(event for key in effective if (event := _parent_event(view, key))))
    folded = {key: _parent_event(view, key) for key in effective}
    related = set()
    for identifier in effective:
        node_input = _safe_node(view, nodes[identifier], targets)
        if node_input:
            related.update(node_input["participants"])
    # Parent event place facts may ground a selected action, without importing
    # that parent's prose or its unselected sibling actions.
    for identifier in effective:
        for row in rows:
            if _location_record(view, row, folded[identifier]) and _state_visible(view, row, identifier):
                related.update(key for key in view.targets(row) if view.nodes[key]["node_type"] not in MOMENT_TYPES)
    selected_values, state_rows = {}, {}
    while True:
        before = len(related), len(selected_values)
        for identifier in effective:
            values = []
            for row in rows:
                eligible = row.get("node_id") == identifier or row.get("node_id") in related or _location_record(view, row, folded[identifier])
                if not eligible or not _state_visible(view, row, identifier):
                    continue
                selected_values[row["id"]] = row
                values.append(row)
                for target in [row.get("holder_id"), *view.targets(row)]:
                    if view.visible(target, view.rank(identifier), nodes[identifier].get("context_id", "canon")) and nodes[target]["node_type"] not in MOMENT_TYPES:
                        related.add(target)
            state_rows[identifier] = values
        if before == (len(related), len(selected_values)):
            break
    definition_ids = {row["property_id"] for row in selected_values.values()}
    grounding = []
    for event_id in source_event_ids:
        points = [key for key in effective if folded[key] == event_id]
        participants, locations, location_recorded = set(), set(), False
        for point in points:
            node_input = _safe_node(view, nodes[point], targets)
            if node_input:
                participants.update(key for key in node_input["participants"] if nodes[key]["node_type"] in {"person", "group"})
            for row in state_rows[point]:
                if _location_record(view, row, event_id):
                    location_recorded = True
                    locations.update(key for key in view.targets(row) if nodes[key]["node_type"] == "location" and view.active(key))
        grounding.append({"event_id": event_id, "participant_ids": sorted(participants), "location_ids": sorted(locations),
                          "location_status": "known" if locations else "unknown" if location_recorded else "unrecorded"})
    return {"fingerprint_version": FINGERPRINT_VERSION, "timeline_id": adaptation["timeline_id"],
            "source_event_ids": source_event_ids,
            "nodes": [_safe_node(view, nodes[key], targets) for key in effective],
            "entities": [_safe_node(view, nodes[key], targets) for key in sorted(related) if key not in targets],
            "states": [{"event_id": key, "values": [_state_projection(view, row, targets, key) for row in state_rows[key]]} for key in effective],
            "definitions": [{key: copy.deepcopy(definitions[name][key]) for key in
                             ("id", "value_type", "cardinality", "target_node_types", "semantic", "symmetric") if key in definitions[name]}
                            for name in sorted(definition_ids) if name in definitions],
            "event_grounding": grounding}


def resolve_span(adaptation, span):
    nodes = {n["id"]: n for n in adaptation["nodes"]}
    axis = sorted([n for n in nodes.values() if n["node_type"] in _TIME_TYPES], key=lambda n: n["sort_id"])
    rank = {n["id"]: i for i, n in enumerate(axis)}
    diagnostics = []
    start, end = span["start_node_id"], span["end_node_id"]
    if span.get("timeline_id") != adaptation.get("timeline_id"):
        diagnostics.append({"severity": "error", "code": "SPAN_TIMELINE", "message": "时间段不属于当前改编时间线"})
    if start not in rank or end not in rank:
        diagnostics.append({"severity": "error", "code": "SPAN_ANCHOR_MISSING", "message": "时间段边界已删除或不再是事件／动作"})
    elif rank[start] > rank[end]:
        diagnostics.append({"severity": "error", "code": "SPAN_REVERSED", "message": "时间段边界顺序倒置"})
    result = {"span": span, "status": "invalid", "member_node_ids": [], "effective_node_ids": [],
              "suppressed_count": 0, "diagnostics": diagnostics, "input": None,
              "span_fingerprint": None, "fingerprint": None}
    if diagnostics:
        return result
    members = [n["id"] for n in axis[rank[start]:rank[end] + 1]]
    effective = [key for key in members if not nodes[key].get("suppressed") and not nodes[key].get("retracted")]
    projection = _input(adaptation, members, effective)
    value = fingerprint(projection)
    result.update(status="valid" if effective else "empty", member_node_ids=members,
                  effective_node_ids=effective, suppressed_count=len(members) - len(effective),
                  input=projection, span_fingerprint=value, fingerprint=value)
    if not effective:
        diagnostics.append({"severity": "warning", "code": "SPAN_EMPTY", "message": "时间段全部内容被屏蔽，保留旧正文但不能生成"})
    return result
