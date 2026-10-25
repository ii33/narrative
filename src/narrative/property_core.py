"""Pure validation and projection for append-only node-property records.

This module intentionally has no storage, UI, or model-service dependencies.
It consumes JSON-shaped dictionaries and the reviewed graph snapshot.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import dependency_core

_KINDS = {
    "property_definition",
    "property_value",
    "property_retraction",
}
# "any" means the shape is not fixed: one property may hold a number, a
# description and a relative phrase over time.
_VALUE_TYPES = {"string", "number", "boolean", "node", "any"}
_CARDINALITIES = {"one", "many"}
_PERSISTENCE = {"until_changed", "instant"}
_CELL_KINDS = {"known", "unknown", "absent"}
_EFFECT_OPS = {"set", "add", "remove", "unknown", "absent"}
_SCOPES = {"world", "cognition", "claim"}
_COMMON = {"id", "kind", "evidence"}
_SCOPED_FIELDS = {"scope", "holder_id", "disclosure"}
_DEPENDENCY_FIELDS = {"derived_from", "support_groups"}
_FIELDS = {
    "property_definition": _COMMON
    | {"value_type", "cardinality", "persistence", "owner_node_types", "target_node_types"},
    "property_value": _COMMON | _SCOPED_FIELDS | _DEPENDENCY_FIELDS
    | {"node_id", "property_id", "value", "event_id", "anchor_basis", "applies_to",
       "source", "related_nodes"},
    "property_retraction": _COMMON | {"target_id", "reason"},
}


def _record_scope(record: dict) -> str:
    scope = record.get("scope", "world")
    return scope if isinstance(scope, str) else "world"


def _scope_matches(record: dict, scope: str, holder_id: str | None) -> bool:
    if record.get("kind") in {"property_definition", "property_retraction"}:
        return True
    if _record_scope(record) != scope:
        return False
    if scope == "world":
        return True
    return record.get("holder_id") == holder_id


def _issue(code: str, message: str, object_id: str | None = None, severity: str = "error") -> dict:
    return {"severity": severity, "code": code, "message": message, "object_id": object_id}


def _all_objects(snapshot: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for key in ("objects", "nodes", "predicates", "contexts", "timelines"):
        for item in snapshot.get(key, []) or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                result[item["id"]] = item
    return result


def _active(records: list[dict]) -> tuple[list[dict], set[str]]:
    retracted: set[str] = set()
    for record in records:
        if isinstance(record, dict) and record.get("kind") == "property_retraction":
            target = record.get("target_id")
            if isinstance(target, str):
                retracted.add(target)
    # A retraction record cannot itself erase another record, but malformed
    # cyclic chains are harmless because only target IDs are removed here.
    return [r for r in records if isinstance(r, dict) and r.get("id") not in retracted], retracted


def _definition_map(records: list[dict]) -> dict[str, dict]:
    return {
        r["id"]: r
        for r in records
        if r.get("kind") == "property_definition" and isinstance(r.get("id"), str)
    }


def _valid_value(value: Any, definition: dict, graph_nodes: dict[str, dict]) -> bool:
    value_type = definition.get("value_type")
    if value_type == "any":
        return True
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value == value
            and abs(value) != float("inf")
        )
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "node":
        target = graph_nodes.get(value) if isinstance(value, str) else None
        if not target or target.get("kind") not in {None, "node"}:
            return False
        allowed = definition.get("target_node_types")
        return not allowed or target.get("node_type") in allowed
    return False


def _validate_cell(cell: Any, definition: dict, nodes: dict[str, dict]) -> str | None:
    if not isinstance(cell, dict) or cell.get("kind") not in _CELL_KINDS:
        return "state must be a known, unknown, or absent cell"
    if cell.get("kind") != "known":
        if set(cell) != {"kind"}:
            return "unknown and absent cells cannot contain a value"
        return None
    if set(cell) != {"kind", "value"}:
        return "known cell must contain exactly kind and value"
    value = cell["value"]
    if definition.get("cardinality") == "many":
        if not isinstance(value, list):
            return "many-valued properties require a list"
        if len(value) != len(set(_freeze(v) for v in value)):
            return "many-valued property values must be unique"
        if not all(_valid_value(item, definition, nodes) for item in value):
            return "known cell contains a value of the wrong type or an invalid node reference"
    elif not _valid_value(value, definition, nodes):
        return "known cell contains a value of the wrong type or an invalid node reference"
    return None


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _valid_ref(ref: Any, nodes: dict[str, dict]) -> bool:
    if not isinstance(ref, dict) or not isinstance(ref.get("timeline_id", "story"), str):
        return False
    form = ref.get("form")
    if form == "number":
        v = ref.get("number")
        return (
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and v == v
            and abs(v) != float("inf")
        )
    if form == "datetime":
        raw = ref.get("datetime")
        if not isinstance(raw, str):
            return False
        try:
            datetime.fromisoformat(raw)
        except ValueError:
            return False
        return True
    if form == "event_boundary":
        event_id = ref.get("event_id")
        event = nodes.get(event_id) if isinstance(event_id, str) else None
        return bool(
            event and event.get("node_type") == "event" and ref.get("boundary") in {"start", "end"}
        )
    return False


def _evidence_error(record: dict, sources: dict[str, dict]) -> str | None:
    evidence = record.get("evidence")
    if evidence is None:
        return None
    if not isinstance(evidence, dict) or set(evidence) != {"source_id", "quote"}:
        return "evidence must contain source_id and exact quote"
    if not isinstance(evidence.get("source_id"), str) or not evidence["source_id"]:
        return "evidence source_id must be a non-empty string"
    if not isinstance(evidence.get("quote"), str) or not evidence["quote"]:
        return "evidence quote must be a non-empty exact string"
    source = sources.get(evidence["source_id"])
    if source is None:
        return "evidence source_id does not exist in the graph snapshot"
    text = source.get("text")
    if not isinstance(text, str):
        return "evidence source text is unavailable"
    quote = evidence["quote"]
    first = text.find(quote)
    if first < 0 or text.find(quote, first + 1) >= 0:
        return "evidence quote must match exactly one source location"
    return None


def validate_records(
    records: list[dict], graph_snapshot: dict, accepted_records: list[dict]
) -> list[dict]:
    """Validate candidate property records without mutating either input."""
    issues: list[dict] = []
    if not isinstance(records, list) or not isinstance(accepted_records, list):
        return [_issue("FORMAT", "records and accepted_records must be lists")]
    nodes = _all_objects(graph_snapshot if isinstance(graph_snapshot, dict) else {})
    sources = {
        s["id"]: s
        for s in (graph_snapshot.get("sources", []) if isinstance(graph_snapshot, dict) else [])
        if isinstance(s, dict) and isinstance(s.get("id"), str)
    }
    combined = [r for r in accepted_records if isinstance(r, dict)] + [
        r for r in records if isinstance(r, dict)
    ]
    active, retracted = _active(combined)
    defs = _definition_map(active)
    existing_ids = {r.get("id") for r in accepted_records if isinstance(r, dict)}
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            issues.append(_issue("FORMAT", "each property record must be an object"))
            continue
        rid = record.get("id") if isinstance(record.get("id"), str) else None
        kind = record.get("kind")
        if kind not in _KINDS:
            issues.append(_issue("KIND", "unknown property record kind", rid))
            continue
        if not rid:
            issues.append(_issue("ID", "property record requires a stable non-empty id", rid))
        elif rid in seen or rid in existing_ids:
            issues.append(_issue("DUPLICATE_ID", "property record id must be new and unique", rid))
        elif len(rid) > 200:
            issues.append(_issue("ID", "property record id is too long", rid))
        if rid:
            seen.add(rid)
        extra = set(record) - _FIELDS[kind]
        missing = {
            "property_definition": {"value_type", "cardinality", "persistence"},
            "property_value": {"node_id", "property_id", "value", "event_id"},
            "property_retraction": {"target_id", "reason"},
        }[kind] - set(record)
        if extra:
            issues.append(_issue("FORMAT", f"unexpected fields: {', '.join(sorted(extra))}", rid))
        if missing:
            issues.append(_issue("FORMAT", f"missing fields: {', '.join(sorted(missing))}", rid))
        ev_error = _evidence_error(record, sources)
        if ev_error:
            issues.append(_issue("EVIDENCE", ev_error, rid))
        if kind == "property_value":
            scope = record.get("scope", "world")
            holder_id = record.get("holder_id")
            if scope not in _SCOPES:
                issues.append(_issue("SCOPE", "scope must be world, belief, claim, or knowledge", rid))
            elif scope == "world":
                if holder_id is not None:
                    issues.append(_issue("SCOPE", "world records must not have holder_id", rid))
            else:
                holder = nodes.get(holder_id) if isinstance(holder_id, str) else None
                if holder is None or holder.get("node_type") != "person":
                    issues.append(
                        _issue("HOLDER", "belief/claim/knowledge records require a person holder", rid)
                    )
            disclosure = record.get("disclosure")
            if disclosure is not None:
                if not isinstance(disclosure, dict) or set(disclosure) != {"source_id", "start"}:
                    issues.append(
                        _issue("DISCLOSURE", "disclosure must contain source_id and start", rid)
                    )
                else:
                    source_id = disclosure.get("source_id")
                    start = disclosure.get("start")
                    source = sources.get(source_id) if isinstance(source_id, str) else None
                    if source is None:
                        issues.append(
                            _issue("DISCLOSURE", "disclosure source_id must be an accepted source", rid)
                        )
                    elif (
                        not isinstance(start, int)
                        or isinstance(start, bool)
                        or start < 0
                        or (isinstance(source.get("text"), str) and start > len(source["text"]))
                    ):
                        issues.append(
                            _issue("DISCLOSURE", "disclosure start must be a valid source offset", rid)
                        )
        if kind == "property_definition":
            if record.get("value_type") not in _VALUE_TYPES:
                issues.append(_issue("DEFINITION", "invalid property value_type", rid))
            if record.get("cardinality") not in _CARDINALITIES:
                issues.append(_issue("DEFINITION", "invalid property cardinality", rid))
            if record.get("persistence") not in _PERSISTENCE:
                issues.append(_issue("DEFINITION", "invalid property persistence", rid))
            for field in ("owner_node_types", "target_node_types"):
                values = record.get(field, [])
                if not isinstance(values, list) or any(
                    not isinstance(v, str) or not v for v in values
                ):
                    issues.append(
                        _issue("DEFINITION", f"{field} must be a list of node types", rid)
                    )
            if record.get("value_type") != "node" and record.get("target_node_types"):
                issues.append(
                    _issue("DEFINITION", "target_node_types only applies to node properties", rid)
                )
            continue
        if kind == "property_retraction":
            target_id = record.get("target_id")
            target = next((item for item in combined if item.get("id") == target_id), None)
            if target is None or target.get("kind") not in _KINDS - {"property_retraction"}:
                issues.append(
                    _issue("RETRACTION_TARGET", "retraction must target a property record", rid)
                )
            elif target_id in retracted and not any(
                r.get("id") == rid and r.get("target_id") == target_id
                for r in records
                if isinstance(r, dict)
            ):
                issues.append(_issue("RETRACTION_TARGET", "target is already retracted", rid))
            if not isinstance(record.get("reason"), str) or not record.get("reason", "").strip():
                issues.append(
                    _issue("RETRACTION_REASON", "retraction requires a non-empty reason", rid)
                )
            continue
        node_id = (
            record.get("node_id") if kind in {"property_initial", "property_observation"} else None
        )
        property_id = (
            record.get("property_id")
            if kind in {"property_initial", "property_observation"}
            else None
        )
        if kind == "property_value":
            node_id = record.get("node_id")
            property_id = record.get("property_id")
            _validate_target(node_id, property_id, rid, nodes, defs, issues)
            definition = defs.get(property_id) if isinstance(property_id, str) else None
            if definition is not None:
                # Only a node reference is checked: it must point at something that
                # exists. Scalar shape is not policed — 180 and "变高了" are both
                # legitimate values of the same property.
                if definition.get("value_type") == "node" and not _valid_value(
                    record.get("value"), definition, nodes
                ):
                    issues.append(
                        _issue("VALUE", "node value must reference an existing node", rid)
                    )
                if definition.get("cardinality") == "many" and not isinstance(record.get("value"), list):
                    issues.append(_issue("VALUE", "many-valued properties require a list", rid))
            event_id = record.get("event_id")
            if (
                not isinstance(event_id, str)
                or not nodes.get(event_id)
                or nodes[event_id].get("node_type") != "event"
            ):
                issues.append(_issue("EVENT_REF", "property_value must reference an event node", rid))
            # How the information was expressed (narration, speech, thought,
            # document...). Free text: the ways a story conveys something do not
            # fit a closed list.
            source = record.get("source")
            if source is not None and not isinstance(source, str):
                issues.append(_issue("SOURCE", "source must be a string", rid))
            # Who else the sentence explicitly involves: observer, speaker,
            # blamed party, carrier document. Not the holder — that is scope.
            related = record.get("related_nodes")
            if related is not None:
                if not isinstance(related, list) or any(
                    not isinstance(item, str) for item in related
                ):
                    issues.append(_issue("RELATED", "related_nodes must be a list of node ids", rid))
                else:
                    for target in related:
                        if not nodes.get(target):
                            issues.append(
                                _issue("RELATED", "related_nodes references a missing node", rid)
                            )
            # The logical applicability is a separate link and may be absent: a
            # disclosure anchor does not say the value holds at that story event.
            applies_to = record.get("applies_to")
            if applies_to is not None and (
                not isinstance(applies_to, str)
                or not nodes.get(applies_to)
                or nodes[applies_to].get("node_type") != "event"
            ):
                issues.append(_issue("EVENT_REF", "applies_to must reference an event node", rid))
        if kind == "event_change":
            event_id = record.get("event_id")
            if (
                not isinstance(event_id, str)
                or not nodes.get(event_id)
                or nodes[event_id].get("node_type") != "event"
            ):
                issues.append(_issue("EVENT_REF", "event_change must reference an event node", rid))
            if record.get("boundary") not in {"start", "end"}:
                issues.append(_issue("BOUNDARY", "event_change boundary must be start or end", rid))
            effects = record.get("effects")
            if not isinstance(effects, list) or not effects:
                issues.append(
                    _issue("EFFECTS", "event_change requires a non-empty effects list", rid)
                )
                continue
            pairs: set[tuple] = set()
            for effect in effects:
                if not isinstance(effect, dict):
                    issues.append(_issue("EFFECT", "each effect must be an object", rid))
                    continue
                allowed = {"node_id", "property_id", "op", "value", "expected"}
                if set(effect) - allowed or not {"node_id", "property_id", "op"} <= set(effect):
                    issues.append(_issue("EFFECT", "effect fields are invalid or incomplete", rid))
                    continue
                node_id, property_id = effect["node_id"], effect["property_id"]
                pair = (node_id, property_id)
                if pair in pairs:
                    issues.append(
                        _issue("EFFECT", "one event batch may affect a property only once", rid)
                    )
                pairs.add(pair)
                _validate_target(node_id, property_id, rid, nodes, defs, issues)
                definition = defs.get(property_id) if isinstance(property_id, str) else None
                _validate_effect(effect, definition, nodes, rid, issues)
            continue
        if kind == "property_value":
            continue
    issues.extend(dependency_core.validate_support(graph_snapshot, combined))
    return issues


def _validate_target(
    node_id: Any,
    property_id: Any,
    rid: str | None,
    nodes: dict[str, dict],
    definitions: dict[str, dict],
    issues: list[dict],
) -> None:
    node = nodes.get(node_id) if isinstance(node_id, str) else None
    if not node or node.get("kind") not in {None, "node"}:
        issues.append(_issue("NODE_REF", "property target must reference a graph node", rid))
    definition = definitions.get(property_id) if isinstance(property_id, str) else None
    if not definition:
        issues.append(_issue("PROPERTY_REF", "property definition does not exist", rid))
        return
    if (
        node
        and definition.get("owner_node_types")
        and node.get("node_type") not in definition["owner_node_types"]
    ):
        issues.append(_issue("OWNER_TYPE", "property is not defined for this node type", rid))


def _validate_effect(
    effect: dict,
    definition: dict | None,
    nodes: dict[str, dict],
    rid: str | None,
    issues: list[dict],
) -> None:
    op = effect.get("op")
    if op not in _EFFECT_OPS:
        issues.append(_issue("EFFECT_OP", "invalid event effect operation", rid))
        return
    if definition is None:
        return
    if op in {"add", "remove"} and definition.get("cardinality") != "many":
        issues.append(_issue("EFFECT_OP", "add/remove require a many-valued property", rid))
    if op in {"set", "add", "remove"}:
        if "value" not in effect:
            issues.append(_issue("EFFECT_VALUE", "set/add/remove require value", rid))
        else:
            value = effect["value"]
            if op == "set" and definition.get("cardinality") == "many":
                err = _validate_cell({"kind": "known", "value": value}, definition, nodes)
            elif not _valid_value(value, definition, nodes):
                err = "effect value has the wrong type or invalid node reference"
            else:
                err = None
            if err:
                issues.append(_issue("EFFECT_VALUE", err, rid))
    elif "value" in effect:
        issues.append(_issue("EFFECT_VALUE", "unknown/absent effects cannot contain value", rid))
    if "expected" in effect:
        err = _validate_cell(effect["expected"], definition, nodes)
        if err:
            issues.append(_issue("EXPECTED", err, rid))


def history(
    accepted_records: list[dict],
    node_id: str,
    property_id: str,
    scope: str = "world",
    holder_id: str | None = None,
) -> list[dict]:
    """Return the append-only records relevant to one node/property pair."""
    records = [
        r for r in accepted_records
        if isinstance(r, dict) and _scope_matches(r, scope, holder_id)
    ]
    by_id = {r.get("id"): r for r in records if isinstance(r.get("id"), str)}

    def is_relevant(record: dict) -> bool:
        kind = record.get("kind")
        if kind == "property_definition":
            return record.get("id") == property_id
        if kind == "property_value":
            return record.get("node_id") == node_id and record.get("property_id") == property_id
        if kind == "property_retraction":
            target = by_id.get(record.get("target_id"))
            return bool(target and is_relevant(target))
        return False

    # Recursion guard is important for damaged imported history.
    def relevant(record: dict, seen: set[str]) -> bool:
        rid = record.get("id")
        if not isinstance(rid, str) or rid in seen:
            return False
        seen.add(rid)
        if record.get("kind") == "property_retraction":
            target = by_id.get(record.get("target_id"))
            return bool(target and relevant(target, seen))
        return is_relevant(record)

    return [r for r in records if relevant(r, set())]
