"""Deterministic audit of property records.

Extraction is loose on purpose — one record is one disclosure, and a property may
carry several. That looseness is right for capture but leaves four kinds of
noise: overlapping records, contradicting records, records that a later
disclosure supersedes, and properties that were never declared. This module
finds those without asking a model, so the same input always yields the same
findings.

It never edits anything. Each finding names the records involved and suggests an
action; applying it stays a human decision through the normal candidate flow.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .order_ids import order_key

ERROR = "error"
WARNING = "warning"
INFO = "info"

_SCOPE_LABELS = {"world": "世界", "cognition": "认知", "claim": "声称"}


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalized_name(property_id: str) -> str:
    """`height`, `Height`, `heights`, `height_cm` collapse to one key."""
    name = property_id.strip().lower().replace("-", "_")
    for suffix in ("_cm", "_kg", "_value", "_desc"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if len(name) > 3 and name.endswith("s"):
        name = name[:-1]
    return name.replace("_", "")


def _position(record: dict) -> tuple:
    anchor = record.get("event_id")
    key = order_key(anchor) if isinstance(anchor, str) else None
    disclosure = record.get("disclosure")
    offset = disclosure.get("start") if isinstance(disclosure, dict) else None
    return (key or (999,), offset if isinstance(offset, int) else 0)


def _issue(code, severity, message, *, node=None, property_id=None, records=None, suggestion=""):
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "suggestion": suggestion,
        "node_id": (node or {}).get("id"),
        "node_name": (node or {}).get("name"),
        "property_id": property_id,
        "record_ids": [record.get("id") for record in (records or [])],
    }


def audit_properties(
    records: list[dict],
    nodes: list[dict],
    *,
    node_ids: set[str] | None = None,
) -> dict:
    """Audit property records, optionally limited to the given nodes.

    Returns findings grouped by node plus a flat list, so a caller can show
    either "this node has problems" or "the whole extraction has problems".
    """
    names = {node["id"]: node for node in nodes}
    values = [
        record for record in records
        if record.get("kind") == "property_value"
        and (node_ids is None or record.get("node_id") in node_ids)
    ]
    definitions = {
        record["id"]: record for record in records
        if record.get("kind") == "property_definition"
    }

    issues: list[dict] = []
    groups: dict[tuple, list[dict]] = defaultdict(list)
    per_node_names: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    for record in values:
        node_id = record.get("node_id")
        property_id = record.get("property_id")
        if not isinstance(node_id, str) or not isinstance(property_id, str):
            issues.append(_issue(
                "record_incomplete", ERROR,
                "记录缺少节点或属性名", records=[record],
            ))
            continue
        node = names.get(node_id, {"id": node_id, "name": node_id})
        scope = record.get("scope", "world")
        groups[(node_id, property_id, scope, record.get("holder_id"))].append(record)
        per_node_names[node_id][_normalized_name(property_id)].append(property_id)

        if property_id not in definitions:
            issues.append(_issue(
                "definition_missing", ERROR,
                f"属性「{property_id}」没有定义，值无法校验类型与基数",
                node=node, property_id=property_id, records=[record],
                suggestion="补一条 property_definition，或改用一个已定义的属性名",
            ))
        if not record.get("evidence"):
            issues.append(_issue(
                "evidence_missing", WARNING,
                f"属性「{property_id}」的记录没有原文引文，无法回查",
                node=node, property_id=property_id, records=[record],
                suggestion="补上逐字引文",
            ))
        if not isinstance(record.get("event_id"), str) or not record.get("event_id"):
            issues.append(_issue(
                "anchor_missing", ERROR,
                f"属性「{property_id}」的记录没有披露锚点事件",
                node=node, property_id=property_id, records=[record],
                suggestion="指定读者读到哪个事件时知道这件事",
            ))
        if scope != "world" and not record.get("holder_id"):
            issues.append(_issue(
                "holder_missing", ERROR,
                f"属性「{property_id}」标为{_SCOPE_LABELS.get(scope, scope)}，但没有持有人",
                node=node, property_id=property_id, records=[record],
                suggestion="补 holder，或把范围改回 world",
            ))

    for (node_id, property_id, scope, holder_id), rows in groups.items():
        node = names.get(node_id, {"id": node_id, "name": node_id})
        label = _SCOPE_LABELS.get(scope, scope)
        ordered = sorted(rows, key=_position)
        seen: dict[str, list[dict]] = defaultdict(list)
        for row in ordered:
            seen[_text(row.get("value"))].append(row)

        for value_text, same in seen.items():
            if len(same) > 1:
                issues.append(_issue(
                    "duplicate_value", ERROR,
                    f"属性「{property_id}」（{label}）有 {len(same)} 条记录值相同：{value_text or '（空）'}",
                    node=node, property_id=property_id, records=same,
                    suggestion="同一披露点重复记录，保留一条，其余撤回",
                ))

        if len(seen) > 1:
            latest = _text(ordered[-1].get("value"))
            earlier = [value for value in seen if value != latest]
            issues.append(_issue(
                "conflicting_values", WARNING,
                f"属性「{property_id}」（{label}）前后出现 {len(seen)} 个不同值，"
                f"最近一次是「{latest or '（空）'}」",
                node=node, property_id=property_id, records=ordered,
                suggestion="确认这些是不同时点的披露还是互相矛盾；"
                           f"若只是过时说法，当前有效值是「{latest or '（空）'}」"
                           f"，早先的 {len(earlier)} 条保留为历史",
            ))

        if holder_id and not names.get(holder_id):
            issues.append(_issue(
                "holder_unknown", ERROR,
                f"属性「{property_id}」的持有人 {holder_id} 不在节点表中",
                node=node, property_id=property_id, records=ordered,
                suggestion="修正 holder，或先补上该节点",
            ))

    for node_id, variants in per_node_names.items():
        node = names.get(node_id, {"id": node_id, "name": node_id})
        for property_ids in variants.values():
            distinct = sorted(set(property_ids))
            if len(distinct) > 1:
                issues.append(_issue(
                    "name_variant", WARNING,
                    f"同一节点上出现多个近义属性名：{'、'.join(distinct)}",
                    node=node, property_id=distinct[0],
                    suggestion=f"合并成一个属性名（建议用「{distinct[0]}」），否则同一个方面会被拆成两条线",
                ))

    used = {record.get("property_id") for record in values}
    for property_id in definitions:
        if property_id not in used:
            issues.append(_issue(
                "definition_unused", INFO,
                f"属性定义「{property_id}」没有任何取值记录",
                property_id=property_id,
                suggestion="若无用就撤回定义，否则说明有属性被漏提",
            ))

    issues.sort(key=lambda item: (
        {"error": 0, "warning": 1, "info": 2}.get(item["severity"], 3),
        str(item.get("node_id") or ""),
        str(item.get("property_id") or ""),
    ))
    by_node: dict[str, list[dict]] = defaultdict(list)
    for item in issues:
        if item.get("node_id"):
            by_node[item["node_id"]].append(item)
    return {
        "issues": issues,
        "by_node": dict(by_node),
        "counts": {
            "error": sum(1 for item in issues if item["severity"] == ERROR),
            "warning": sum(1 for item in issues if item["severity"] == WARNING),
            "info": sum(1 for item in issues if item["severity"] == INFO),
        },
        "checked_records": len(values),
    }


def audit_candidate(ops: Any, built: dict) -> dict:
    """Audit an extraction that is not accepted yet.

    The candidate's own records are checked together with what the project
    already holds, limited to the nodes this extraction touches — that is the
    window where overlaps and contradictions actually appear.
    """
    snapshot = ops.graph_snapshot()
    nodes = list(snapshot.get("nodes", []))
    nodes += [
        obj for obj in built.get("objects", [])
        if obj.get("kind") == "node"
    ]
    records = list(ops.property_records()) + list(built.get("property_records", []))
    node_ids = {
        obj["id"] for obj in built.get("objects", [])
        if obj.get("kind") == "node"
    }
    return audit_properties(records, nodes, node_ids=node_ids)
