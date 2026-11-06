"""Read-only people/location membership derived from moment containment."""
from __future__ import annotations

from .graph_query import revealed
from .property_core import canonical_definitions
from .temporal_state import boundary_at, point, position, projected_changes, source_order

MOMENTS = {"storyline", "big_event", "scene", "event", "action"}


def involvement(snapshot: dict, records: list[dict], node_id: str, cut=None) -> dict:
    nodes = {row["id"]: row for row in snapshot["nodes"]}
    owner = nodes.get(node_id)
    result: dict[str, list[dict]] = {"people": [], "locations": []}
    if not owner or owner.get("node_type") not in MOMENTS or owner.get("retracted"):
        return result
    context = owner.get("context_id", "canon")
    boundary = boundary_at(snapshot, cut) if cut is not None else None
    orders = source_order(snapshot)
    def visible(row):
        return (not row.get("retracted") and row.get("context_id", "canon") == context
                and (boundary is None or revealed(row, boundary, orders)))
    members, visited, queue = {}, set(), [node_id]
    while queue:
        identifier = queue.pop()
        if identifier in visited:
            continue
        visited.add(identifier)
        row = nodes.get(identifier)
        if not row or not visible(row) or row.get("node_type") not in MOMENTS:
            continue
        if row["node_type"] in {"event", "action"}:
            members[identifier] = row
        queue.extend(row.get("events") or [])
    collected: dict[str, dict] = {}
    def add(identifier, event_id, record_id=None):
        target = nodes.get(identifier)
        if not target or target.get("node_type") not in {"person", "location"} or not visible(target):
            return
        row = collected.setdefault(identifier, {"id": identifier, "name": target.get("name") or identifier,
                                   "node_type": target["node_type"], "event_ids": [], "record_ids": []})
        if event_id not in row["event_ids"]:
            row["event_ids"].append(event_id)
        if record_id and record_id not in row["record_ids"]:
            row["record_ids"].append(record_id)
    for event_id, member in members.items():
        for identifier in member.get("participants") or []:
            add(identifier, event_id)
    definitions = canonical_definitions(records)
    for record in projected_changes(snapshot, records, boundary):
        if record.get("state_kind") != "known" or record.get("node_id") not in members:
            continue
        if record.get("scope", "world") != "world":
            continue
        definition = definitions.get(str(record.get("property_id") or ""), {})
        if definition.get("value_type") not in {"node", "either"}:
            continue
        value = record.get("value")
        for target in value if isinstance(value, list) else [value]:
            if isinstance(target, str):
                add(target, record["node_id"], record.get("id"))
    def reading_key(item):
        return min(position(point(members[event_id]), snapshot) or (float("inf"), 0) for event_id in item["event_ids"]), item["id"]
    for row in sorted(collected.values(), key=reading_key):
        row["event_ids"].sort(key=lambda identifier: (position(point(members[identifier]), snapshot) or (float("inf"), 0), identifier))
        result["people" if row["node_type"] == "person" else "locations"].append(row)
    return result
