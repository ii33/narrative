"""Explicit current world possession for items, shared by writes and read projections."""
from __future__ import annotations

SENTINELS = {"无", "未知"}


def possession_candidates(records: list[dict], cut: tuple | None = None, *, position_key=None) -> tuple[str, dict[str, list[dict]], dict]:
    from .property_core import canonical_definitions
    from .property_store import invalid_at, record_position

    key_of = position_key or record_position
    invalid = invalid_at(records, cut) if position_key is None else {}
    active = [row for row in records if row.get("id") not in invalid]
    definitions = canonical_definitions(active)
    name = next((key for key, definition in definitions.items()
                 if definition.get("semantic") == "item_holder"), "持有者")
    latest: dict[str, list[dict]] = {}
    for row in sorted(active, key=key_of):
        if row.get("kind") != "property_value" or (row.get("scope") or "world") != "world" or row.get("holder_id"):
            continue
        raw = str(row.get("property_id") or "")
        if raw != name:
            continue
        if cut is not None and key_of(row) > cut:
            continue
        key = str(row.get("node_id") or "")
        current = latest.get(key) or []
        if current and key_of(current[-1]) == key_of(row):
            current.append(row)
        else:
            latest[key] = [row]
    return name, latest, definitions


def possession_rows(records: list[dict], cut: tuple | None = None, *, position_key=None) -> tuple[str, dict[str, dict], dict]:
    name, candidates, definitions = possession_candidates(records, cut, position_key=position_key)
    return name, {key: rows[-1] for key, rows in candidates.items()}, definitions


def item_possession_issues(objects: list[dict], records: list[dict]) -> list[dict]:
    nodes = {obj["id"]: obj for obj in objects if obj.get("kind") == "node"}
    property_id, latest, definitions = possession_candidates(records)
    issues = []
    for node in nodes.values():
        if node.get("node_type") != "item" or node.get("retracted"):
            continue
        current = latest.get(node["id"]) or []
        if not current:
            issues.append({"severity": "error", "code": "ITEM_HOLDER_REQUIRED", "object_id": node["id"],
                           "property_id": property_id,
                           "message": f"物品 {node['id']}（{node['name']}）必须有当前有效的world持有者记录；请明确节点、无或未知，认知holder_id不能代替"})
            continue
        if len({str(row.get("value")) for row in current}) > 1:
            issues.append({"severity": "error", "code": "ITEM_HOLDER_CONFLICT", "object_id": node["id"],
                           "record_ids": [row["id"] for row in current], "property_id": property_id,
                           "message": f"物品 {node['id']}（{node['name']}）在同一披露位置有不同持有者状态，不能按插入顺序挑选当前值"})
        for row in current:
            value = row.get("value")
            value_type = (definitions.get(property_id) or {}).get("value_type")
            explicit = isinstance(value, str) and value in SENTINELS and value_type in {"either", "string"}
            target = nodes.get(value) if isinstance(value, str) else None
            reference = (value_type in {"node", "either"} and target is not None
                         and not target.get("retracted") and target["id"] != node["id"])
            if not explicit and not reference:
                issues.append({"severity": "error", "code": "ITEM_HOLDER_VALUE", "object_id": node["id"],
                               "record_id": row["id"], "property_id": property_id,
                               "message": f"物品 {node['id']}（{node['name']}）的当前world持有者须为合法节点或显式无/未知"})
    return issues
