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

from . import property_categories, property_core, property_labels
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
    if len(name) > 3 and name.endswith("s") and not name.endswith(("ss", "us", "is")):
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
    categories: dict | None = None,
) -> dict:
    """Audit property records, optionally limited to the given nodes.

    Returns findings grouped by node plus a flat list, so a caller can show
    either "this node has problems" or "the whole extraction has problems".
    """
    names = {node["id"]: node for node in nodes}
    # A retracted value is still a record: it is what a retraction points at, and
    # it is what tells the audit that an aspect lost its last standing value. It
    # is excluded from the value checks, though — a taken-back record must not be
    # reported as an overlap or a contradiction.
    retracted_ids = {
        record.get("target_id") for record in records
        if record.get("kind") == "property_retraction"
    }
    values_all = [
        record for record in records
        if record.get("kind") == "property_value"
        and (node_ids is None or record.get("node_id") in node_ids)
    ]
    values = [record for record in values_all if record.get("id") not in retracted_ids]
    definitions = {
        record["id"]: record for record in records
        if record.get("kind") == "property_definition"
    }
    # A category move is one record pointing at one disclosure; the last accepted
    # one is the grouping a reader sees.
    overrides: dict[str, str] = {}
    for record in records:
        if record.get("kind") != "property_categorization":
            continue
        target, category = record.get("target_id"), record.get("category")
        if isinstance(target, str) and isinstance(category, str) and category.strip():
            overrides[target] = category.strip()

    retracted_nodes = {node["id"] for node in nodes if node.get("retracted")}
    # What the review round would mark: older values a later disclosure replaced.
    # Computed from the same rule the extraction uses, so the audit cannot ask for
    # a different invalidation than the one already written into a candidate.
    values_by_id = {record["id"]: record for record in values_all}
    issues: list[dict] = []
    groups: dict[tuple, list[dict]] = defaultdict(list)
    per_node_names: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    # Records nobody has classified yet, counted per node: one line per node is
    # more useful than one line per record.
    unclassified: dict[str, int] = defaultdict(int)

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
        # Two names for one aspect (`cultivation`, `cultivation_stage`) are the
        # same subject; the reader sees them on one line, so the check has to
        # happen at that level too.
        aspect = _normalized_name(property_categories.aspect_of(property_id))
        per_node_names[node_id][aspect].append(property_id)

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
        if record.get("event_id") in retracted_nodes:
            issues.append(_issue(
                "anchor_retracted", WARNING,
                f"属性「{property_id}」锚在已撤回的事件 {record.get('event_id')} 上，披露位置随之失效",
                node=node, property_id=property_id, records=[record],
                suggestion="改锚到仍然有效的事件，或连同这条记录一起撤回",
            ))
        if scope != "world" and not record.get("holder_id"):
            issues.append(_issue(
                "holder_missing", ERROR,
                f"属性「{property_id}」标为{_SCOPE_LABELS.get(scope, scope)}，但没有持有人",
                node=node, property_id=property_id, records=[record],
                suggestion="补 holder，或把范围改回 world",
            ))

        # Categories are a display grouping: a wrong one is worth fixing but
        # never blocks the record, so the worst case here is a warning.
        node_type = node.get("node_type")
        category, category_source = property_categories.resolve(
            record, node_type, categories, overrides
        )
        if category_source == "fallback":
            unclassified[node_id] += 1
        elif category not in property_categories.vocabulary_for(node_type, categories):
            issues.append(_issue(
                "category_unknown", WARNING,
                f"属性「{property_id}」的分类「{category}」不在该节点类型的分类表里",
                node=node, property_id=property_id, records=[record],
                suggestion="改成一个已有大类，或把它加进分类表；也可以留着，不影响取值",
            ))

    value_ids = {
        record["id"] for record in records
        if record.get("kind") == "property_value" and isinstance(record.get("id"), str)
    }
    for record in records:
        if record.get("kind") != "property_categorization":
            continue
        if record.get("target_id") not in value_ids:
            issues.append(_issue(
                "categorization_target_missing", ERROR,
                f"分类记录指向的 {record.get('target_id') or '（空）'} 不是已有的取值记录",
                property_id=None, records=[record],
                suggestion="分类只能指向一条已接受的取值记录",
            ))

    if unclassified:
        total = sum(unclassified.values())
        issues.append(_issue(
            "category_unassigned", INFO,
            f"有 {total} 条属性暂时归入「其他」，分布在 {len(unclassified)} 个节点",
            records=None,
            suggestion="在浏览页把它们移到合适的大类；拿不准就留在「其他」，不影响取值",
        ))

    retractions = [
        record for record in records
        if record.get("kind") == "property_retraction"
    ]
    retract_groups: dict[tuple, list[dict]] = defaultdict(list)
    for record in retractions:
        # A retraction has no node, aspect or position of its own: it is an
        # editorial act pointing at one disclosure. Everything about it is read
        # from that target, so the two can never disagree.
        target = values_by_id.get(record.get("target_id") or "")
        if target is None:
            issues.append(_issue(
                "retraction_without_target", ERROR,
                f"撤回记录指向的 {record.get('target_id') or '（空）'} 不存在，撤不掉任何东西",
                records=[record],
                suggestion="撤回必须指向一条已接受的取值记录",
            ))
            continue
        retract_groups[(
            target.get("node_id"), target.get("property_id"),
            target.get("scope", "world"), target.get("holder_id"),
        )].append(record)

    # Groups come from every value, retracted or not: a group whose values were
    # all taken back still has to be judged, and it would otherwise vanish from
    # the report exactly when it became empty.
    for record in values_all:
        node_id, property_id = record.get("node_id"), record.get("property_id")
        if isinstance(node_id, str) and isinstance(property_id, str):
            groups[(
                node_id, property_id, record.get("scope", "world"),
                record.get("holder_id"),
            )].append(record)

    for (node_id, property_id, scope, holder_id), rows in groups.items():
        node = names.get(node_id, {"id": node_id, "name": node_id})
        label = _SCOPE_LABELS.get(scope, scope)
        retr = retract_groups.get((node_id, property_id, scope, holder_id)) or []
        # Retracted rows are not evidence of anything: they are only kept so the
        # group exists. Every check below works on what still stands.
        ordered = sorted(
            (row for row in rows if row.get("id") not in retracted_ids), key=_position
        )
        if not ordered:
            if retr:
                issues.append(_issue(
                    "retracted_no_successor", WARNING,
                    f"属性「{property_id}」（{label}）的记录都已撤回，当前没有有效值",
                    node=node, property_id=property_id, records=retr,
                    suggestion="确认这是有意终止；若之后又有新说法，补一条取值记录",
                ))
            continue
        seen: dict[str, list[dict]] = defaultdict(list)
        for row in ordered:
            seen[_text(row.get("value"))].append(row)

        for value_text, same in seen.items():
            # The same value at the same place is a duplicate. The same value at
            # different places is a restatement, which the model allows on
            # purpose — flagging it as an error would punish correct capture.
            at_position: dict[tuple, list[dict]] = defaultdict(list)
            for row in same:
                disclosure = row.get("disclosure") or {}
                at_position[(disclosure.get("source_id"), disclosure.get("start"))].append(row)
            for rows_here in at_position.values():
                if len(rows_here) > 1:
                    issues.append(_issue(
                        "duplicate_value", ERROR,
                        f"属性「{property_id}」（{label}）在同一披露位置重复记录：{value_text or '（空）'}",
                        node=node, property_id=property_id, records=rows_here,
                        suggestion="同一披露点重复输出，保留一条，其余撤回",
                    ))
            if len(same) > 1 and len(at_position) > 1:
                issues.append(_issue(
                    "repeated_value", INFO,
                    f"属性「{property_id}」（{label}）在 {len(at_position)} 个位置重申同一值："
                    f"{value_text or '（空）'}",
                    node=node, property_id=property_id, records=same,
                    suggestion="重申是允许的；只有位置也重复才算重叠",
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
                    f"同一节点上同一个方面用了多个属性名：{'、'.join(distinct)}",
                    node=node, property_id=distinct[0],
                    suggestion=f"确认是同一个方面后合并成一个名字（建议用「{distinct[0]}」），"
                               "否则每换一个名字就多出一条独立的“当前值”",
                ))

    value_nodes = {
        str(record["node_id"]) for record in values if record.get("node_id")
    }
    for node_id in sorted(value_nodes):
        if node_id in retracted_nodes:
            node = names.get(node_id, {"id": node_id, "name": node_id})
            issues.append(_issue(
                "node_retracted_with_values", WARNING,
                f"节点「{node.get('name')}」已撤回，但仍有属性记录，这些值随之失效",
                node=node,
                suggestion="节点撤回时一并撤回它的属性记录，或恢复该节点",
            ))

    # 属性名要在界面上显示中文。英文标识符没有对照时，读者会看到「身份」和
    # `office` 并排——中英混着，一眼就看出来这行不是给人写的。
    for property_id, definition in definitions.items():
        if not property_id.isascii() or property_labels.label_for(property_id) != property_id:
            continue
        issues.append(_issue(
            "label_missing", INFO,
            f"属性名「{property_id}」没有中文对照，界面上会直接显示英文",
            property_id=property_id,
            records=[definition],
            suggestion="在 property_labels.LABELS 里补一条，或用节点编辑器的「改名」换成中文",
        ))

    # A definition whose records were all taken back is not unused — it is in use
    # and currently empty, which `retracted_no_successor` already says.
    used = {record.get("property_id") for record in values_all}
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
    # Retracted rows are included: a retraction in this candidate may point at a
    # record that was taken back earlier, and judging it needs that record.
    records = list(ops.property_records(include_retracted=True)) + list(
        built.get("property_records", [])
    )
    # A name that was renamed or merged away is not a second aspect here either:
    # the candidate is judged under the names the reader will see.
    records = property_core.canonical_records(records)
    node_ids = {
        obj["id"] for obj in built.get("objects", [])
        if obj.get("kind") == "node"
    }
    return audit_properties(
        records, nodes, node_ids=node_ids, categories=ops.property_categories()
    )
