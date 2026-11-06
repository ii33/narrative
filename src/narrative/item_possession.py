"""Explicit world possession derived from event changes, never from a time window."""
from __future__ import annotations

SENTINELS = {"无", "未知"}


def possession_candidates(records, cut=None, *, snapshot=None):
    from .property_core import canonical_definitions
    from .temporal_state import projected_changes

    definitions = canonical_definitions(records)
    name = next((key for key, definition in definitions.items() if definition.get("semantic") == "item_holder"), "持有者")
    if snapshot is not None:
        rows = projected_changes(snapshot, records, cut)
    else:
        rows = [row for row in records if row.get("derived") is True]
    found = {}
    for row in rows:
        if row.get("kind") != "property_change" or row.get("property_id") != name:
            continue
        if row.get("scope", "world") != "world" or row.get("holder_id"):
            continue
        found.setdefault(str(row.get("node_id") or ""), []).append(row)
    return name, found, definitions


def possession_rows(records, cut=None, *, snapshot=None):
    name, candidates, definitions = possession_candidates(records, cut, snapshot=snapshot)
    return name, {node: rows[0] for node, rows in candidates.items() if len(rows) == 1}, definitions


def item_possession_issues(objects, records, *, snapshot=None):
    nodes = {obj["id"]: obj for obj in objects if obj.get("kind") == "node"}
    property_id, candidates, definitions = possession_candidates(records, snapshot=snapshot)
    issues = []
    for node in nodes.values():
        if node.get("node_type") != "item" or node.get("retracted"):
            continue
        rows = [row for row in candidates.get(node["id"], []) if row.get("context_id", "canon") == node.get("context_id", "canon")]
        if not rows or all(row.get("state_kind") in {"absent", "unrecorded"} for row in rows):
            issues.append({"severity": "error", "code": "ITEM_HOLDER_REQUIRED", "object_id": node["id"], "property_id": property_id,
                           "message": f"物品{node['id']}（{node['name']}）必须记录world持有状态：节点、无或明确未知；失效/未记录不等于未知"})
            continue
        for row in rows:
            kind = row.get("state_kind")
            if kind == "unknown" and row.get("active_change_ids"):
                continue
            value = row.get("value")
            value_type = definitions.get(property_id, {}).get("value_type")
            if not isinstance(value_type, str):
                # Shape validation reports the malformed definition; precheck
                # must remain diagnostic instead of raising on an unhashable type.
                value_type = None
            explicit = isinstance(value, str) and value in SENTINELS and value_type in {"either", "string"}
            target = nodes.get(value) if isinstance(value, str) else None
            reference = value_type in {"node", "either"} and target is not None and not target.get("retracted") and target["id"] != node["id"]
            if kind == "conflict":
                code, message = "ITEM_HOLDER_CONFLICT", "同一事件位置的持有状态冲突，不能按提交顺序选择"
            elif kind != "known" or not (explicit or reference):
                code, message = "ITEM_HOLDER_VALUE", "world持有状态须为合法节点、无或明确未知"
            else:
                continue
            issues.append({"severity": "error", "code": code, "object_id": node["id"], "property_id": property_id,
                           "record_ids": row.get("active_change_ids", []), "message": f"物品{node['id']}（{node['name']}）{message}"})
    return issues
