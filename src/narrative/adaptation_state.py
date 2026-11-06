"""Replay property changes on an independent, locally ordered timeline."""
from __future__ import annotations

import copy

from .state_anchors import anchor_nodes


def event_order(snapshot):
    return {key: node["sort_id"] for key, node in {
        **anchor_nodes(snapshot), **{node["id"]: node for node in snapshot["nodes"]}
    }.items() if node.get("node_type") in {"event", "action"} and not node.get("suppressed") and not node.get("retracted")}


def group_key(row):
    return (row["node_id"], row["property_id"], row.get("context_id", "canon"),
            row.get("scope", "world"), row.get("holder_id"))


def replay(snapshot, cutoff=None):
    from .property_core import reduce_changes

    nodes = {node["id"]: node for node in snapshot["nodes"]}
    definitions = {row["id"]: row for row in snapshot["properties"] if row.get("kind") == "property_definition"}
    order = event_order(snapshot)
    if cutoff is None:
        cutoff = max((node["sort_id"] for node in snapshot["nodes"] if node["id"] in order), default=float("-inf"))
    groups = {}
    for row in snapshot["properties"]:
        if row.get("kind") != "property_change" or row.get("event_id") not in order:
            continue
        if row.get("node_id") not in nodes or nodes[row["node_id"]].get("suppressed"):
            continue
        groups.setdefault(group_key(row), []).append(row)
    result = {}
    for key, rows in groups.items():
        if key[1] not in definitions:
            continue
        cut = cutoff
        if isinstance(cutoff, (int, float)):
            candidates = [identifier for identifier, rank in order.items() if rank <= cutoff]
            if not candidates:
                continue
            cut = max(candidates, key=order.__getitem__)
        result[key] = reduce_changes(rows, definitions[key[1]], nodes, event_order=order, cut=cut)
    return result


def current_rows(snapshot, cutoff=None):
    """Derived current facts with unchanged change provenance, never persisted."""
    from .property_core import relation_target

    rows = []
    raw = {row["id"]: row for row in snapshot["properties"]}
    nodes = {node["id"]: node for node in snapshot["nodes"]}
    definitions = {row["id"]: row for row in snapshot["properties"] if row.get("kind") == "property_definition"}
    for key, result in replay(snapshot, cutoff).items():
        state, active = result["state"], result.get("active_change_ids", [])
        history = result["history_changes"]
        if not history:
            continue
        contributors = [raw[identifier] for identifier in active if identifier in raw]
        row = copy.deepcopy((contributors or history)[-1])
        row.pop("value", None)
        row.update(state_kind=state["kind"], active_change_ids=active, derived=True,
                   provenance=copy.deepcopy(contributors), history_change_ids=[change["id"] for change in history])
        if state["kind"] == "known":
            row["op"] = "set"
            value = state["value"]
            relation = definitions[key[1]].get("cardinality", "one") == "one" and any(relation_target(change, nodes, definitions) for change in history)
            if relation:
                for target in value:
                    source = next((change for change in reversed(contributors) if change.get("value") == target), row)
                    rows.append(row | {"id": source["id"], "event_id": source["event_id"], "sequence": source["sequence"],
                                       "value": target, "active_change_ids": [source["id"]]})
                continue
            row["value"] = copy.deepcopy(value)
        if state["kind"] == "unknown":
            row["op"] = "unknown"
        elif state["kind"] == "absent":
            row["op"] = "clear"
        rows.append(row)
    return rows
