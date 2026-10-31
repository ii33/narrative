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

# 关系名说的是「对方是我的什么人」，所以一个名字只能站一边。成对的叫法（父子、
# 夫妻、师徒、上下级）是**一个对子**，不是某一方的身份——写上去就读不出谁是谁的
# 父亲。单边称呼（父亲、儿子）写两侧各一次是对的，但两次都写同一个词就是反了。
PAIR_RELATIONS = {
    "父子", "父女", "母子", "母女", "夫妻", "师徒", "上下级", "主仆",
    "兄弟", "姐妹", "叔侄", "翁婿", "妯娌",
}
ROLE_RELATIONS = {
    "父亲", "母亲", "儿子", "女儿", "丈夫", "妻子", "师父", "师傅", "徒弟",
    "老师", "学生", "上司", "下属", "主人", "仆人", "叔父", "伯父", "侄",
    "侄儿", "兄", "弟", "姐", "妹", "祖父", "祖母", "孙", "岳父", "岳母",
}


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


_INVERSE = {
    "父亲": "儿子", "母亲": "女儿", "儿子": "父亲", "女儿": "母亲",
    "丈夫": "妻子", "妻子": "丈夫", "师父": "徒弟", "师傅": "徒弟",
    "徒弟": "师父", "老师": "学生", "学生": "老师", "上司": "下属",
    "下属": "上司", "主人": "仆人", "仆人": "主人", "叔父": "侄",
    "侄": "叔父", "兄": "弟", "弟": "兄", "姐": "妹", "妹": "姐",
}


def _inverse_hint(kind: str) -> str:
    return _INVERSE.get(kind, "相反的称呼")


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
    # 找撤回目标要索引**全部**取值：撤回可能指向别的批次（上一章）的记录，
    # 按本次节点筛过就找不到目标，会误报「撤不掉任何东西」。分组与统计仍只看本次节点。
    indexed_values = [
        record for record in records if record.get("kind") == "property_value"
    ]
    values_all = [
        record for record in indexed_values
        if node_ids is None or record.get("node_id") in node_ids
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
    values_by_id = {record["id"]: record for record in indexed_values}
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
        scope = (record.get("scope") or "world")
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
            (target.get("scope") or "world"), target.get("holder_id"),
        )].append(record)

    # Groups come from every value, retracted or not: a group whose values were
    # all taken back still has to be judged, and it would otherwise vanish from
    # the report exactly when it became empty.
    for record in values_all:
        node_id, property_id = record.get("node_id"), record.get("property_id")
        if isinstance(node_id, str) and isinstance(property_id, str):
            groups[(
                node_id, property_id, (record.get("scope") or "world"),
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

    # 属性属于**东西**和**场景**，不属于一次发生。事件只讲发生了什么；给它挂
    # 一条状态，等于把「这一刻的样子」记在了时间点上，下一次披露就没地方放。
    for record in values:
        node = names.get(record.get("node_id") or "") or {}
        if node.get("node_type") == "event":
            issues.append(_issue(
                "property_on_event", INFO,
                f"事件「{node.get('name')}」上挂了属性「{record.get('property_id')}」，"
                "事件不该带属性",
                node=node,
                property_id=record.get("property_id"),
                records=[record],
                suggestion="状态记在人物/物品/地点上，或者记在它所属的场景上；"
                           "事件名本身已经说了发生了什么",
            ))

    # 大事件不写属性：它的样子由场景那句 `storyline_progress` 攒出来。模型偶尔
    # 还是把总结直接写在它身上，那样读者看到的就是一句静态结论，不随阅读位置变。
    for record in values:
        node = names.get(record.get("node_id") or "") or {}
        if node.get("node_type") != "storyline":
            continue
        if str(record.get("property_id") or "") != property_labels.STORYLINE_PROGRESS:
            continue
        issues.append(_issue(
            "property_on_storyline", INFO,
            f"大事件「{node.get('name')}」自己写了一条进展，"
            "这句话应该属于某个场景",
            node=node,
            property_id=record.get("property_id"),
            records=[record],
            suggestion="把这条改挂到它对应的场景上（场景的 `storyline_progress`），"
                       "大事件会自己汇总",
        ))

    # 关系名要指清是哪一边。成对的叫法（父子）读不出谁是谁的父亲；单边称呼
    # （父亲）写两侧各一次是对的，但两侧都写同一个词，方向就没了。
    people = {
        node_id_: node for node_id_, node in names.items()
        if node.get("node_type") == "person"
    }
    relations: dict[tuple[str, str], list[dict]] = {}
    for record in values:
        left, right = str(record.get("node_id") or ""), str(record.get("value") or "")
        if left not in people or right not in people or left == right:
            continue
        kind = str(record.get("property_id") or "")
        relations.setdefault((left, right), []).append(record)
        if kind in PAIR_RELATIONS:
            issues.append(_issue(
                "relation_pair_name", INFO,
                f"「{people[left].get('name')} —{kind}→ {people[right].get('name')}」"
                f"用的是成对的叫法，读不出谁是谁的哪一边",
                node=people[left],
                property_id=kind,
                records=[record],
                suggestion="写成两边各自的身份：父亲 / 儿子、丈夫 / 妻子、上司 / 下属",
            ))
    for (left, right), rows in relations.items():
        for record in rows:
            kind = str(record.get("property_id") or "")
            if kind not in ROLE_RELATIONS:
                continue
            twin = [
                other for other in relations.get((right, left), [])
                if str(other.get("property_id") or "") == kind
            ]
            if not twin:
                continue
            issues.append(_issue(
                "relation_same_both_ways", INFO,
                f"「{people[left].get('name')}」和「{people[right].get('name')}」"
                f"两侧都写成了「{kind}」，方向没了",
                node=people[left],
                property_id=kind,
                records=[record] + twin[:1],
                suggestion=f"改成 A —{kind}→ B 和 B —{_inverse_hint(kind)}→ A 两条",
            ))

    # 场景必须归到一个大事件下面。没有归属的场景在界面里只能进「未归入大事件」
    # 那个桶——它不是错，但它说明这一章还没读出大事件的边界。
    parents: dict[str, list[dict]] = {}
    for node in nodes:
        for child in node.get("events") or []:
            parents.setdefault(child, []).append(node)
    for node in nodes:
        if node.get("node_type") != "scene":
            continue
        owners = [item for item in parents.get(node["id"], [])
                  if item.get("node_type") == "storyline"]
        if owners:
            continue
        issues.append(_issue(
            "scene_without_storyline", INFO,
            f"场景「{node.get('name')}」没有归入任何大事件",
            node=node,
            suggestion="在场景的 `storyline` 里写它属于哪个大事件；跨章还没走完的用暂称",
        ))

    # 大事件在每个场景里往前走一步：场景没给出那句话，读者就永远看不到进展。
    for node in nodes:
        if node.get("node_type") != "storyline":
            continue
        scenes = {
            child for child in node.get("events") or []
            if (names.get(child) or {}).get("node_type") == "scene"
        }
        spoken = {
            record.get("node_id") for record in values
            if str(record.get("property_id") or "") == property_labels.STORYLINE_PROGRESS
        }
        silent = sorted(scenes - spoken)
        if scenes and silent:
            issues.append(_issue(
                "scene_without_progress", INFO,
                f"大事件「{node.get('name')}」有 {len(silent)} 个场景没写进展，"
                "读者读到这里看不到它走到哪了",
                node=node,
                suggestion="每个场景给一条 `storyline_progress`，一句话概括到这个场景为止的部分",
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

    # 同一个节点上，两个**名字不同**的方面拿着同一个值：多半是同一个方面被叫了两个名字
    # （中文同义词程序认不出来），一条线被拆成了两条。只报不改，合并是人的决定。
    by_node_value: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in values:
        node_id = record.get("node_id")
        name = record.get("property_id")
        value = record.get("value")
        if isinstance(node_id, str) and isinstance(name, str) and isinstance(value, str):
            by_node_value[(node_id, value)].add(name)
    for (node_id, value), aspects in by_node_value.items():
        if len(aspects) < 2:
            continue
        issues.append(_issue(
            "aspect_overlap", WARNING,
            f"同一节点上「{'」「'.join(sorted(aspects))}」记着同一个值「{value[:20]}」"
            f"，可能是同一个方面用了两个名字",
            node=names.get(node_id),
            records=[
                record for record in values
                if record.get("node_id") == node_id and record.get("value") == value
            ],
            suggestion="确认是不是同一个方面；是的话用「合并」把两个名字并成一条线",
        ))

    # 关系得指向人：值既不是节点编号也不是任何节点的名字，说明写成了句子
    # （「恋人 = 与沈砚是多年的恋人」）。这种在关系网上画不出来。
    by_name = {_text(node.get("name")): node for node in nodes if node.get("name")}
    for record in values:
        if str(record.get("category") or "") != "关系":
            continue
        node = names.get(str(record.get("node_id") or "")) or {}
        value = _text(record.get("value"))
        if not node or not value or value in names or value in by_name:
            continue
        issues.append(_issue(
            "relation_not_a_person", WARNING,
            f"「{node.get('name')}」的「{record.get('property_id')}」值是「{value[:24]}」，"
            f"指向的不是人物节点",
            node=node,
            property_id=str(record.get("property_id") or ""),
            records=[record],
            suggestion="关系要指向具体的人；说的是关系本身，就把值换成对方的名字",
        ))

    # 值就是它自己：「某人的女友 · 关系 = 某人的女友」这种，说的是身份不是关系，
    # 画到关系网上就是一条自己指向自己的边。只报不改——可能是方面名写错了。
    for record in values:
        node = names.get(str(record.get("node_id") or "")) or {}
        if not node:
            continue
        value = _text(record.get("value"))
        if value and value in {str(node.get("id")), _text(node.get("name"))}:
            issues.append(_issue(
                "self_reference", WARNING,
                f"「{node.get('name')}」的「{record.get('property_id')}」值是它自己",
                node=node,
                property_id=str(record.get("property_id") or ""),
                records=[record],
                suggestion="确认这条说的是什么；说的是身份就换个方面名，说的是关系就该指向别人",
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
