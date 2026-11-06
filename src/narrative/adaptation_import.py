"""Pure selection and dependency closure for importing extracted time points.

Neighbor links and container membership are projected to imported nodes; they do
not pull unrelated neighboring events into an adaptation.
"""
from __future__ import annotations

import copy
import json

MOMENTS = {"storyline", "big_event", "scene", "event", "action"}
_TYPE_ORDER = {"storyline": 0, "big_event": 1, "scene": 2, "event": 3, "action": 4}


def source_data(db):
    revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
    nodes = [json.loads(row[0]) for row in db.execute("SELECT data FROM objects WHERE kind='node'")]
    records = [json.loads(row[0]) for row in db.execute("SELECT data FROM property_records ORDER BY rowid")]
    orders = {obj["source_id"]: obj["ordinal"] for obj in
              (json.loads(row[0]) for row in db.execute("SELECT data FROM objects WHERE kind='source_order'"))}
    return nodes, records, orders, revision


def _position(boundary, orders):
    if not isinstance(boundary, dict) or not isinstance(boundary.get("start"), int):
        return None
    return orders.get(boundary.get("source_id"), 10**9), boundary["start"]


def _reading_order(nodes, orders):
    return sorted(nodes, key=lambda n: (_position(n.get("disclosure"), orders) or (10**9, 10**9),
                                       _TYPE_ORDER.get(n["node_type"], 5)))


def _parents(nodes):
    parents = {}
    for node in nodes:
        for child in node.get("events", []):
            parents.setdefault(child, []).append(node["id"])
    return parents


def candidates(nodes, orders, revision):
    active = {n["id"]: n for n in nodes if n["node_type"] in MOMENTS and not n.get("retracted")}
    parents = _parents(active.values())
    from .storyline_ranges import reading_starts
    starts = reading_starts({"nodes": list(active.values()), "source_orders": [
        {"source_id": source, "ordinal": ordinal, "context_id": context}
        for context in {n.get("context_id", "canon") for n in active.values()}
        for source, ordinal in orders.items()]})
    def ordered_members(members):
        return sorted(members, key=lambda node: (
            _position(starts.get(node["id"], node.get("disclosure")), orders) or (10**9, 10**9),
            _TYPE_ORDER.get(node["node_type"], 5)))
    result, visited = [], set()
    def visit(identifier, parent_id, depth):
        if identifier in visited:
            return
        visited.add(identifier)
        node = active[identifier]
        children = ordered_members([active[key] for key in node.get("events", []) if key in active])
        result.append({"id": identifier, "name": node["name"], "node_type": node["node_type"],
                       "parent_id": parent_id, "depth": depth, "children": [n["id"] for n in children],
                       "disclosure": copy.deepcopy(node.get("disclosure")), "source_revision": revision})
        for child in children:
            visit(child["id"], identifier, depth + 1)
    ordered = ordered_members(active.values())
    for node in ordered:
        if not parents.get(node["id"]):
            visit(node["id"], None, 0)
    for node in ordered:
        visit(node["id"], None, 0)
    return result


def select(nodes, records, orders, moment_ids, state):
    by_id = {n["id"]: n for n in nodes}
    selected = set(moment_ids)
    for identifier in selected:
        node = by_id.get(identifier)
        if not node or node["node_type"] not in MOMENTS or node.get("retracted"):
            raise ValueError(f"提取时间点不存在或不可导入：{identifier}")
    primary = set()
    def descendants(identifier):
        if identifier in primary:
            return
        primary.add(identifier)
        for child in by_id[identifier].get("events", []):
            if child in by_id and not by_id[child].get("retracted"):
                descendants(child)
    for identifier in selected:
        descendants(identifier)
    parents = _parents(nodes)
    included = set(primary)
    wanted_records = set()
    boundary_ids = set()
    definitions = {r["id"]: r for r in records if r.get("kind") == "property_definition"}
    values = [r for r in records if r.get("kind") == "property_change"]
    cuts = [_position(by_id[key].get("disclosure"), orders) for key in primary]
    cuts = [cut for cut in cuts if cut is not None]

    def row_position(row):
        return _position(by_id.get(row.get("event_id"), {}).get("disclosure"), orders)

    def relevant(row):
        position = row_position(row)
        return position is not None and bool(cuts) and position <= max(cuts)

    def add(identifier):
        if isinstance(identifier, str) and identifier in by_id:
            if by_id[identifier]["node_type"] in MOMENTS and identifier not in primary:
                boundary_ids.add(identifier)
            else:
                included.add(identifier)

    def include_value(row):
        wanted_records.add(row["id"])
        for field in ("node_id", "event_id", "holder_id", "applies_to"):
            add(row.get(field))
        for identifier in row.get("related_nodes", []):
            add(identifier)
        definition = definitions.get(row.get("property_id"), {})
        if definition.get("value_type") in {"node", "either"}:
            value = row.get("value")
            for target in value if isinstance(value, list) else [value]:
                add(target)

    # A finite closure: only entity state at selected cuts and explicit references
    # can add data. Merely neighboring an event never expands the selection.
    while True:
        previous = len(included), len(wanted_records)
        for identifier in list(included):
            node = by_id[identifier]
            if node["node_type"] in MOMENTS:
                for parent in parents.get(identifier, []):
                    included.add(parent)
            if identifier in primary:
                for participant in node.get("participants", []):
                    add(participant)
            occurrence = node.get("occurrence") or {}
            for ref in (occurrence.get("point"), (occurrence.get("start") or {}).get("ref"),
                        (occurrence.get("end") or {}).get("ref")):
                if isinstance(ref, dict):
                    add(ref.get("event_id"))
        for row in values:
            owner = row.get("node_id")
            owner_node = by_id.get(owner, {})
            if (row.get("event_id") in primary or
                (owner in included and (owner in primary or owner_node.get("node_type") not in MOMENTS)
                 and relevant(row))):
                include_value(row)
        if previous == (len(included), len(wanted_records)):
            break

    existing = {n["id"] for n in state["nodes"]}
    visible_ids = included | existing
    incoming_nodes = []
    for original in _reading_order([by_id[key] for key in included], orders):
        node = copy.deepcopy(original)
        # Containment and event-neighbor links are restricted to this workspace.
        for field in ("events", "predecessors", "successors"):
            node[field] = [key for key in node.get(field, []) if key in visible_ids]
        incoming_nodes.append(node)
    incoming_values = [copy.deepcopy(r) for r in values if r["id"] in wanted_records]
    definition_ids = {r["property_id"] for r in incoming_values}
    incoming_records = [copy.deepcopy(r) for r in records
                        if r.get("kind") == "property_definition" and r["id"] in definition_ids] + incoming_values
    moment_order = [n["id"] for n in incoming_nodes if n["node_type"] in MOMENTS]
    from .state_anchors import selection_anchors
    geometry = {"nodes": [{**node, "sort_id": index * 1024} for index, node in enumerate(_reading_order(nodes, orders), 1)]}
    anchors = selection_anchors(geometry, included | existing, boundary_ids)
    summary = {"state_anchors": anchors, "selected_moment_ids": list(dict.fromkeys(moment_ids)),
               "imported_moment_ids": [key for key in moment_order if key in primary],
               "dependency_moment_ids": [key for key in moment_order if key not in primary],
               "node_ids": [n["id"] for n in incoming_nodes], "property_ids": [r["id"] for r in incoming_records]}
    return incoming_nodes, incoming_records, summary
