"""Validate conversation-produced facts and build current graph records."""

from __future__ import annotations

import json
from typing import cast

from .order_ids import allocate, prefix_for
from .property_labels import BIG_EVENT_PROGRESS, STORYLINE_PROGRESS
from .source_location import ExactQuoteLookup

NODE_TYPES = ("person", "group", "item", "location", "scene", "event", "action", "big_event", "storyline")











VALUE_TYPES = ("string", "number", "boolean", "node", "any", "either")
ALLOWED_PARTICIPANTS = ("person", "group", "item", "location")


def _parse_properties(rows, text: str, names: set[str], rejected: list[dict], locations: dict, lookup=None) -> list[dict]:
    """Verify property observations the same way as nodes: exact quote, known owner."""
    if not isinstance(rows, list):
        return []
    properties: list[dict] = []
    for draft_index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        node_name = str(row.get("node") or "").strip()
        property_id = str(row.get("property") or "").strip()
        value_type = str(row.get("value_type") or "string").strip()
        if not node_name or not property_id:
            continue
        if any(row.get(key) is not None and not isinstance(row[key], str) for key in ('node', 'holder', 'at_event')):
            rejected.append({"name": property_id, "reason": "节点引用必须为字符串"})
            continue
        if (not isinstance(row.get('related_nodes', []), list)
                or any(not isinstance(value, str) for value in row.get('related_nodes', []))):
            rejected.append({"name": property_id, "reason": "related_nodes须为节点引用字符串数组"})
            continue
        if value_type not in VALUE_TYPES:
            rejected.append({"name": f"{node_name}.{property_id}", "reason": f"值类型无效：{value_type}"})
            continue
        if "cardinality" in row and row["cardinality"] not in ("one", "many"):
            rejected.append({"name": f"{node_name}.{property_id}", "reason": "cardinality必须为one或many"})
            continue
        if "symmetric" in row and type(row["symmetric"]) is not bool:
            rejected.append({"name": f"{node_name}.{property_id}", "reason": "symmetric必须是布尔值"})
            continue
        quotes = [str(item) for item in (row.get("quotes") or []) if str(item).strip()]
        good = []
        for quote in quotes:
            index, found = _locate(text, quote, locations, lookup)
            if index < 0:
                rejected.append({"name": f"{node_name}.{property_id}", "reason": "引文不在原文中",
                                 "quote": quote[:60]})
                continue
            good.append({"quote": found, "start": index})
        if not good:
            rejected.append({"name": f"{node_name}.{property_id}", "reason": "没有可用的原文引文"})
            continue
        good.sort(key=lambda item: item["start"])
        op = row.get('op')
        if not isinstance(op, str) or op not in {'set', 'add', 'remove', 'clear', 'unknown'}:
            rejected.append({'name': property_id, 'reason': '必须明确事件变更op'})
            continue
        if ('value' in row) != (op in {'set', 'add', 'remove'}):
            rejected.append({'name': property_id, 'reason': 'set/add/remove须有value，clear/unknown禁止value'})
            continue
        value = row.get('value')
        if op in {'set', 'add', 'remove'} and value_type == 'boolean' and not isinstance(value, list) and type(value) is not bool:
            rejected.append({'name': property_id, 'reason': '布尔属性必须为true或false'})
            continue
        properties.append({
            "_draft_index": draft_index,
            "node": node_name,
            "property": property_id,
            **({"value": value} if op in {"set", "add", "remove"} else {}), "op": op,
            "value_type": value_type,
            **({"cardinality": row["cardinality"]} if "cardinality" in row else {}),
            **({"symmetric": row["symmetric"]} if "symmetric" in row else {}),
            "start": good[0]["start"],
            "quotes": [item["quote"] for item in good],
            "scope": str(row.get("scope") or "world").strip(),
            "holder": str(row.get("holder") or "").strip(),
            "at_event": str(row.get("at_event") or "").strip(),
            "source": str(row.get("source") or "").strip(),
            # A display grouping, not a fact. Empty is allowed and means 其他.
            "category": str(row.get("category") or "").strip(),
            "related_nodes": [
                str(item).strip()
                for item in (row.get("related_nodes") or [])
                if str(item).strip()
            ],
        })
    properties.sort(key=lambda item: item["start"])
    return properties


WRAPPING_QUOTES = "\"'\u2018\u2019\u201c\u201d\u300c\u300d\u300e\u300f"


def _locate(text: str, quote: str, locations: dict, lookup=None) -> tuple[int, str]:
    if quote not in locations:
        locations[quote] = _find_quote(text, quote, lookup)
    return locations[quote]


def _find_quote(text: str, quote: str, lookup=None) -> tuple[int, str]:
    """找到引文在原文里的位置，返回 (下标, 实际用的引文)。

    先按原样找；找不到就只去掉**首尾的引号**再试一次——模型经常把整句再套一层
    引号（`“他说‘火纹石改过外形’”` 多套了一层），多出来的是排版不是内容。只剥首尾、
    不动中间，所以「逐字对上」这条并没有松。
    """
    lookup = lookup if lookup is not None else ExactQuoteLookup(text)
    found = lookup.positions(quote, 1)
    if found:
        return found[0], quote
    trimmed = quote.strip().strip(WRAPPING_QUOTES).strip()
    if trimmed and trimmed != quote:
        found = lookup.positions(trimmed, 1)
        if found:
            return found[0], trimmed
    return -1, quote


def parse_nodes(payload: dict, text: str) -> dict:
    """Verify every quote and pin each node to its first appearance.

    A node survives only if at least one quote is a literal substring of the
    source; rejected quotes are reported with the reason instead of dropped
    silently.
    """
    rows = payload.get("nodes")
    if not isinstance(rows, list):
        raise ValueError("模型回复缺少 nodes 数组")

    # 模型偶尔把属性行写进 `nodes` 里（长章节更容易）：一行有 node/property 没有 type，
    # 那就是属性，不是节点。按**形状**认，不按数组位置认——否则这些行会被当成
    # 「类型无效」丢掉，整章的属性、关系、大事件进展一起消失，还不报错。
    strays: list[dict] = []
    kept_rows: list = []
    for original_index, row in enumerate(rows, 1):
        if (
            isinstance(row, dict)
            and not str(row.get("type") or "").strip()
            and str(row.get("node") or "").strip()
            and str(row.get("property") or "").strip()
        ):
            strays.append(row)
        else:
            kept_rows.append((original_index, row))
    rows = kept_rows

    nodes: list[dict] = []
    locations: dict[str, tuple[int, str]] = {}
    lookup = ExactQuoteLookup(text)
    rejected: list[dict] = []
    for original_index, row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        node_type = str(row.get("type") or "").strip()
        if node_type not in NODE_TYPES:
            rejected.append({"name": name, "reason": f"类型无效：{node_type}"})
            continue
        if (any(row.get(field) is not None and not isinstance(row[field], str) for field in ('scene', 'event', 'big_event', 'storyline'))
                or not isinstance(row.get('participants', []), list)
                or any(not isinstance(value, str) for value in row.get('participants', []))
                or not isinstance(row.get('children', []), list)
                or any(not isinstance(value, str) for value in row.get('children', []))):
            rejected.append({"name": name, "reason": "父级与参与者须为正式ID或@索引字符串"})
            continue
        closing_span = None
        if node_type == "storyline":
            closing = row.get("closing_quote")
            closing_positions = lookup.positions(closing, 2) if isinstance(closing, str) and closing else []
            if not isinstance(closing, str) or not closing_positions:
                rejected.append({"name": name, "reason": "主要故事仅在完整结束后总结，须给出原文closing_quote"})
                continue
            closing_start = closing_positions[0]
            if len(closing_positions) > 1:
                rejected.append({"name": name, "reason": "closing_quote出现多次，须用完整上下文唯一定位"})
                continue
            closing_span = {"start": closing_start, "end": closing_start+len(closing), "quote": closing}
        quotes = [str(item) for item in (row.get("quotes") or []) if str(item).strip()]
        if node_type == "storyline" and not quotes:
            quotes = [closing_span["quote"]] if closing_span else []
        good: list[dict] = []
        for quote in quotes:
            index, found = _locate(text, quote, locations, lookup)
            if index < 0:
                rejected.append({"name": name, "reason": "引文不在原文中", "quote": quote[:60]})
                continue
            positions = lookup.positions(found, 2) if node_type == "event" else []
            if len(positions) > 1:
                rejected.append({"name": name, "quote": found,
                                 "positions_sample": positions,
                                 "position_basis": "submission_relative",
                                 "reason": "事件引文在提交范围出现多处，不能任取首处作为精确身份依据；请缩小提交范围或提供更具体的逐字引文"})
            good.append({"quote": found, "start": index})
        if not name or not good:
            if name:
                rejected.append({"name": name, "reason": "没有可用的原文引文"})
            continue
        if row.get("existing_id") is not None and not isinstance(row["existing_id"], str):
            rejected.append({"name": name, "reason": "existing_id须为节点ID字符串"})
            continue
        if row.get("new_occurrence") is not None and type(row["new_occurrence"]) is not bool:
            rejected.append({"name": name, "reason": "new_occurrence须为布尔值"})
            continue
        good.sort(key=lambda item: item["start"])
        participants = [
            str(item).strip() for item in (row.get("participants") or []) if str(item).strip()
        ]
        nodes.append({
            "name": name,
            "reference": f"@{original_index}",
            "type": node_type,
            "start": good[0]["start"],
            "quotes": [item["quote"] for item in good],
            "evidence_spans": [{"start": item["start"], "end": item["start"] + len(item["quote"]),
                                "quote": item["quote"]} for item in good],
            "existing_id": str(row.get("existing_id") or "").strip(),
            "new_occurrence": row.get("new_occurrence") is True,
            "context_id": str(row.get("context_id") or "canon"),
            "aliases": [
                str(item).strip() for item in (row.get("aliases") or []) if str(item).strip()
            ],
            "participants": participants,
            "children": list(row.get("children") or []),
            "participants_not_in_text": [item for item in participants if item not in text],
            "scene": str(row.get("scene") or "").strip(),
            "event": str(row.get("event") or "").strip(),
            # 场景归属的大事件（可选）：大事件跨场景、甚至跨章。
            "storyline": str(row.get("storyline") or "").strip(),
            "big_event": str(row.get("big_event") or "").strip(),
            "closing_span": closing_span,
        })

    # Reading order inside each type, then grouped for display.
    nodes.sort(key=lambda item: (item["start"], item["name"]))

    grouped: dict[str, list[dict]] = {node_type: [] for node_type in NODE_TYPES}
    for node in nodes:
        grouped[node["type"]].append(node)
    declared = payload.get("properties")
    declared = declared if isinstance(declared, list) else []
    if strays:
        declared = declared + strays
    properties = _parse_properties(declared, text, {item["name"] for item in nodes}, rejected, locations, lookup)
    scenes = {item["name"] for item in grouped["scene"]}
    for event in grouped["event"]:
        if event["scene"] and event["scene"] not in scenes:
            event["scene_not_found"] = True
    containers = {kind: {item["name"] for item in grouped[kind]} for kind in ("storyline", "big_event")}
    for child_type, parent_type in (("scene", "big_event"), ("big_event", "storyline")):
        for child in grouped[child_type]:
            if child[parent_type] and child[parent_type] not in containers[parent_type]:
                child[parent_type + "_not_found"] = True

    return {
        "nodes": nodes,
        "properties": properties,
        "grouped": grouped,
        "counts": {node_type: len(items) for node_type, items in grouped.items()},
        "rejected": rejected,
    }












def _checkpoint_at(start: int, checkpoints: list[tuple[int, int, str]]) -> str | None:
    """The event whose reading segment contains this position.

    Prefer the event whose quote covers the position; otherwise the nearest one
    *before* it, because the information appears in that event's segment; before
    the chapter's first event it belongs to the opening segment. Text after the
    last event hangs on that last event rather than being dropped.

    The result is a reading anchor. It says nothing about when the value holds in
    the story — a value learned in a flashback is disclosed here, not there.
    """
    if not checkpoints:
        return None
    for begin, end, event_id in checkpoints:
        if begin <= start <= end:
            return event_id
    preceding = [item for item in checkpoints if item[0] <= start]
    if preceding:
        return max(preceding, key=lambda item: item[0])[2]
    return min(checkpoints, key=lambda item: item[0])[2]


def _anchor_for(reference: str, event_by_name: dict[str, str]) -> tuple[str | None, str]:
    anchor = event_by_name.get(reference)
    return anchor, "explicit" if anchor else ""



def _node_spans(item: dict, source_id: str) -> list[dict]:
    return [{"source_id": source_id, **span} for span in item.get("evidence_spans") or []]


def _span_key(span: dict) -> tuple:
    return (span.get("source_id"), span.get("start"), span.get("end"), span.get("quote"))


def _participants(item: dict, by_name: dict, ids: dict, existing: dict) -> list[str]:
    resolved = []
    for name in item.get("participants") or []:
        target = None
        candidate = by_name.get(name)
        if candidate and candidate["type"] in ALLOWED_PARTICIPANTS:
            target = ids.get((candidate["type"], name))
        if target is None:
            target = next((existing[(kind, name)] for kind in ALLOWED_PARTICIPANTS
                           if (kind, name) in existing), None)
        if target and target not in resolved:
            resolved.append(target)
    return resolved


def _event_reuse_candidates(item: dict, snapshot: dict, source_id: str,
                            by_name: dict, ids: dict, existing: dict) -> tuple[list[dict], list[dict]]:
    named = [node for node in snapshot["nodes"] if node["node_type"] == item["type"]
             and node.get("context_id", "canon") == item.get("context_id", "canon")
             and (item.get("display_name", item["name"]) == node["name"]
                  or item.get("display_name", item["name"]) in (node.get("aliases") or []))]
    parent_field = "event" if item["type"] == "action" else "scene"
    scene_name = item.get(parent_field)
    scene_id = item.get("resolved_" + parent_field + "_id")
    scene = next((node for node in snapshot["nodes"] if node["id"] == scene_id), {})
    if item["type"] == "action":
        named = [node for node in named if node["id"] in (scene.get("events") or [])]
    participants = set(_participants(item, by_name, ids, existing))
    spans = {_span_key(span) for span in _node_spans(item, source_id)}
    proven = []
    for candidate in named:
        if scene_name and candidate["id"] not in (scene.get("events") or []):
            continue
        if item.get("participants") and set(candidate.get("participants") or []) != participants:
            continue
        if (candidate.get("disclosure") or {}).get("source_id") != source_id:
            continue
        recorded = list(candidate.get("evidence_spans") or [])
        # Preserve exact legacy primary evidence matching. Do not read history
        # or invent other evidence that the node did not retain.
        primary = candidate.get("evidence") or {}
        start = (candidate.get("disclosure") or {}).get("start")
        if isinstance(start, int) and isinstance(primary.get("quote"), str):
            recorded.append({"source_id": primary.get("source_id"), "start": start,
                             "end": start + len(primary["quote"]), "quote": primary["quote"]})
        exact_primary = bool(
            primary.get("quote") == item["quotes"][0]
            and primary.get("source_id") == source_id and start == item["start"]
        )
        # The previous strict primary-quote identity remains valid when a
        # correction supplies no scene/participants; omitted structure is not a
        # claim that the event had no scene or participants. Additional evidence
        # may prove identity only with the full scene/participant checks.
        complete_structure = bool(scene_id and item.get("participants") is not None
                                  and set(candidate.get("participants") or []) == participants)
        if exact_primary or (complete_structure and spans.intersection(_span_key(span) for span in recorded)):
            proven.append(candidate)
    return named, proven


def observation_key(row: dict) -> str:
    """Full fact/provenance identity; readable row IDs do not encode semantics."""
    boundary = row.get("disclosure") or {}
    evidence = row.get("evidence") or {}
    fields = {
        "node": row.get("node_id"), "property": row.get("property_id"), "value": row.get("value"),
        "scope": row.get("scope") or "world", "holder": row.get("holder_id"),
        "event": row.get("event_id"), "op": row.get("op"), "sequence": row.get("sequence"),
        "source_id": boundary.get("source_id"), "start": boundary.get("start"),
        "quote": evidence.get("quote"), "evidence_source": evidence.get("source_id"),
    }
    return json.dumps(fields, sort_keys=True, ensure_ascii=False, allow_nan=False)


def build_extraction(ops, data: dict) -> dict:
    """Turn one extraction result into graph objects + property records.

    Two orders are kept apart. The disclosure chain is derived from source
    positions and never stored. ``predecessors``/``successors`` hold only the
    relations the source states, so a missing edge means "not established yet",
    not "comes next in the text". Property values are anchored to the disclosure
    checkpoint where the reader learns them.
    """
    snapshot = ops.graph_snapshot()
    skipped: list[dict] = []
    snapshot_nodes = {node["id"]: node for node in snapshot["nodes"]}
    # Ids are disclosure positions, so a new mention needs to know where it sits
    # relative to the nodes that already exist.
    orders = {
        row["source_id"]: row["ordinal"] for row in snapshot.get("source_orders", []) or []
    }
    current_ordinal = orders.get(data["source_id"])
    if current_ordinal is None:
        current_ordinal = max((int(value) for value in orders.values()), default=-1) + 1
    # 每种节点各有自己的编号序列：人从 P1 开始，物品从 I1 开始，互不相关。
    # 所以位置表和占用表都按类型分开——两个类型共用一本流水号就不是两套编号了。
    positions: dict[str, list[tuple[tuple[int, int], str]]] = {}
    taken: dict[str, set[str]] = {}
    for node in snapshot["nodes"]:
        node_type = str(node.get("node_type") or "")
        taken.setdefault(node_type, set()).add(node["id"])
        disclosure = node.get("disclosure")
        if isinstance(disclosure, dict) and isinstance(disclosure.get("start"), int):
            positions.setdefault(node_type, []).append((
                (int(orders.get(disclosure.get("source_id"), 99)), disclosure["start"]),
                node["id"],
            ))

    existing: dict[tuple[str, str], str] = {}
    name_counts: dict[tuple[str, str], int] = {}
    for node in snapshot["nodes"]:
        key = (node["node_type"], node["name"])
        existing.setdefault(key, node["id"])
        name_counts[key] = name_counts.get(key, 0) + 1

    existing_by_name = dict(existing)
    for node in snapshot['nodes']:
        existing[(node['node_type'], node['id'])] = node['id']
    # A shared name is not an identity. Entries collapse only when the type, the
    # name *and* the sentence all match — that is the model repeating itself,
    # not two different deeds that happen to be called the same thing.
    unique: dict[tuple[str, str, str], dict] = {}
    for item in data["nodes"]:
        quote = item["quotes"][0] if item["quotes"] else ""
        key = (item["type"], item["name"], quote)
        if key in unique:
            merged = unique[key]
            identity_fields = ("existing_id", "new_occurrence", "context_id", "scene", "event")
            if (any(item.get(field) != merged.get(field) for field in identity_fields)
                    or set(item.get("participants") or []) != set(merged.get("participants") or [])):
                skipped.append({"name": item["name"], "reason": "同一首引文的节点声明了不同身份或参与结构，请先消除歧义"})
            for extra in item["quotes"]:
                if extra not in merged["quotes"]:
                    merged["quotes"].append(extra)
            span_keys = {_span_key(span) for span in merged.get("evidence_spans") or []}
            for span in item.get("evidence_spans") or []:
                if _span_key(span) not in span_keys:
                    merged.setdefault("evidence_spans", []).append(span)
                    span_keys.add(_span_key(span))
            continue
        unique[key] = dict(item)
    nodes = []
    for item in unique.values():
        explicit = item.get("existing_id")
        target = snapshot_nodes.get(explicit) if explicit else None
        if explicit and (target is None
                         or target.get("node_type") != item["type"]
                         or target.get("context_id", "canon") != item.get("context_id", "canon")
                         or item.get("new_occurrence")):
            skipped.append({"name": item["name"], "existing_id": explicit,
                            "reason": "existing_id须指向本工程存在且类型、语境一致的节点，不能同时声明new_occurrence"})
            continue
        nodes.append(item)
    by_name: dict[str, dict] = {}
    for item in nodes:
        by_name.setdefault(item["name"], item)
    ids: dict[tuple[str, str], str] = {}
    # Two mentions can share a name; the sentence is what keeps them apart, so
    # each mention gets its own id while references still resolve by name.
    mention_ids: dict[tuple[str, str, str], str] = {}
    objects: list[dict] = []

    event_items = [item for item in nodes if item["type"] == "event"]
    event_items.sort(key=lambda item: item["start"])
    action_items = sorted((item for item in nodes if item["type"] == "action"), key=lambda item: item["start"])

    # Resolve every id first: an event may reference a node that comes later.
    # Entities keep their identity across chapters; events do not, because two
    # scenes may legitimately narrate two different deeds under one name.
    # One extraction record is identified by what it says *and* where it says it:
    # the same sentence can legitimately appear twice in a book.
    known_by_quote = {
        (
            item["node_type"], item["name"],
            (item.get("evidence") or {}).get("quote"),
            (item.get("disclosure") or {}).get("source_id"),
            (item.get("disclosure") or {}).get("start"), item.get("context_id", "canon"),
        ): item["id"]
        for item in snapshot["nodes"]
    }
    allocation_order = [item for item in nodes if item["type"] not in {"event", "action"}] + event_items + action_items
    for item in allocation_order:
        key = (item["type"], item["name"])
        quote = item["quotes"][0] if item["quotes"] else ""
        mention = (item["type"], item["name"], quote)
        record_key = (item["type"], item.get("display_name", item["name"]), quote, data["source_id"], item["start"], item.get("context_id", "canon"))
        if item.get("existing_id"):
            mention_ids[mention] = item["existing_id"]
            ids.setdefault(key, mention_ids[mention])
            continue
        if item["type"] in {"event", "action"}:
            from .containment_refs import resolve_parent

            parent_kind = "event" if item["type"] == "action" else "scene"
            item["resolved_" + parent_kind + "_id"] = resolve_parent(
                item.get(parent_kind) or "", parent_kind, item.get("context_id", "canon"),
                snapshot, nodes, mention_ids, skipped, item["name"],
            )
            named, proven = _event_reuse_candidates(item, snapshot, data["source_id"], by_name, ids, existing)
            if proven and item.get("new_occurrence"):
                skipped.append({"name": item["name"], "existing_ids": [node["id"] for node in proven],
                                "reason": "new_occurrence与同一位置的事件证据冲突；请查证后引用existing_id或提供另一次发生的证据"})
            elif len(proven) == 1:
                mention_ids[mention] = proven[0]["id"]
                ids.setdefault(key, mention_ids[mention])
                continue
            if len(proven) > 1 or (named and not item.get("new_occurrence")):
                skipped.append({"name": item["name"], "existing_ids": [node["id"] for node in named],
                                "reason": "同名事件身份不能唯一确认；查证后用existing_id引用已有事件，或用new_occurrence声明原文中的另一次发生"})
        if item["type"] not in {"event", "action"} and record_key in known_by_quote:
            mention_ids[mention] = known_by_quote[record_key]
            ids.setdefault(key, mention_ids[mention])
            continue
        # Reuse an entity only when the project holds exactly one node of that
        # name and type. Once there are several, the name proves nothing, so the
        # new mention stays separate until someone confirms the identity.
        reused = (
            existing_by_name.get((item["type"], item.get("display_name", item["name"])))
            if item["type"] not in {"event", "action"}
            and name_counts.get((item["type"], item.get("display_name", item["name"])), 0) == 1
            else None
        )
        if reused:
            mention_ids[mention] = reused
            ids.setdefault(key, reused)
            continue
        position = (current_ordinal, item["start"])
        node_type = str(item["type"])
        node_id = allocate(
            positions.setdefault(node_type, []),
            position,
            taken.setdefault(node_type, set()),
            prefix_for(node_type),
        )
        taken[node_type].add(node_id)
        positions[node_type].append((position, node_id))
        mention_ids[mention] = node_id
        ids.setdefault(key, node_id)

    from .containment_refs import resolve_parent

    # Typed parent IDs are the common identity for old and new containers.
    scene_children: dict[str, list[str]] = {}
    for event in event_items:
        if "resolved_scene_id" not in event:
            event["resolved_scene_id"] = resolve_parent(
                event.get("scene") or "", "scene", event.get("context_id", "canon"),
                snapshot, nodes, mention_ids, skipped, event["name"],
            )
        parent = event.get("resolved_scene_id")
        if parent:
            quote = event["quotes"][0]
            scene_children.setdefault(parent, []).append(mention_ids[("event", event["name"], quote)])
    action_children: dict[str, list[str]] = {}
    for action in action_items:
        parent = action.get("resolved_event_id")
        if not parent:
            parent = resolve_parent(action.get("event") or "", "event", action.get("context_id", "canon"),
                                    snapshot, nodes, mention_ids, skipped, action["name"])
        if parent:
            action_id = mention_ids[("action", action["name"], action["quotes"][0])]
            action_children.setdefault(parent, []).append(action_id)
    big_event_children: dict[str, list[str]] = {}
    storyline_children: dict[str, list[str]] = {}
    for child_type, parent_type, children in (("scene", "big_event", big_event_children),
                                               ("big_event", "storyline", storyline_children)):
        for child in nodes:
            if child["type"] != child_type:
                continue
            parent = resolve_parent(child.get(parent_type) or "", parent_type, child.get("context_id", "canon"),
                                    snapshot, nodes, mention_ids, skipped, child["name"])
            if parent:
                children.setdefault(parent, []).append(mention_ids[(child_type, child["name"], child["quotes"][0])])
    children_by_type = {"scene": scene_children, "big_event": big_event_children,
                        "storyline": storyline_children, "event": action_children}
    direct_kind = {"storyline":"big_event", "big_event":"scene", "scene":"event", "event":"action"}
    for item in nodes:
        parent_kind = item["type"]
        for reference in item.get("children") or []:
            if parent_kind not in direct_kind:
                skipped.append({"name": item["name"], "reason": "只有五层容器节点可声明children"})
                continue
            child_kind = direct_kind[parent_kind]
            if parent_kind == "scene" and snapshot_nodes.get(reference, {}).get("node_type") == "scene":
                child_kind = "scene"
            if parent_kind == "scene" and any(row.get("name") == reference and row.get("type") == "scene" for row in nodes):
                child_kind = "scene"
            child_id = resolve_parent(reference, child_kind, item.get("context_id", "canon"),
                                      snapshot, nodes, mention_ids, skipped, item["name"])
            if child_id:
                parent_id = mention_ids[(parent_kind,item["name"],item["quotes"][0])]
                children_by_type[parent_kind].setdefault(parent_id, []).append(child_id)
    for children_map in children_by_type.values():
        for parent_id, children in children_map.items():
            children = list(dict.fromkeys(children))
            children_map[parent_id] = children
            parent = snapshot_nodes.get(parent_id)
            if parent is None:
                continue
            merged_children = list(dict.fromkeys([*(parent.get("events") or []), *children]))
            if merged_children != list(parent.get("events") or []):
                objects.append(dict(parent) | {"events": merged_children})
            children_map[parent_id] = cast(list[str], merged_children)

    existing_ids = {item["id"] for item in snapshot["nodes"]}
    for item in nodes:
        key = (item["type"], item["name"])
        quote = item["quotes"][0] if item["quotes"] else ""
        node_id = mention_ids[(item["type"], item["name"], quote)]
        if node_id in existing_ids:
            old = snapshot_nodes[node_id]
            staged_index = next((index for index, obj in enumerate(objects) if obj["id"] == node_id), None)
            current = objects[staged_index] if staged_index is not None else old
            updated = dict(current)
            spans = list(current.get("evidence_spans") or [])
            keys = {_span_key(span) for span in spans}
            for span in _node_spans(item, data["source_id"]):
                if _span_key(span) not in keys:
                    spans.append(span)
                    keys.add(_span_key(span))
            if spans:
                updated["evidence_spans"] = spans
            children = children_by_type.get(item["type"], {}).get(node_id, [])
            if children:
                updated["events"] = list(dict.fromkeys([*(current.get("events") or []), *children]))
            if updated != current:
                if staged_index is None:
                    objects.append(updated)
                else:
                    objects[staged_index] = updated
            continue
        payload = {
            "kind": "node",
            "id": node_id,
            "node_type": item["type"],
            "name": item.get("display_name", item["name"]),
            "aliases": item.get("aliases") or [],
            "context_id": item.get("context_id", "canon"),
            "identity_note": "原文提取",
            "disclosure": {"source_id": data["source_id"], "start": item["start"]},
            "evidence": {"source_id": data["source_id"], "quote": item["quotes"][0]},
            "evidence_spans": _node_spans(item, data["source_id"]),
        }
        if item["type"] == "storyline" and item.get("closing_span"):
            closing = item["closing_span"]
            payload["completion"] = {"source_id": data["source_id"], "start": closing["end"]}
            payload["disclosure"] = {"source_id": data["source_id"], "start": closing["start"]}
            payload["evidence"] = {"source_id": data["source_id"], "quote": closing["quote"]}
            close_evidence = {"source_id": data["source_id"], **closing}
            if close_evidence not in payload["evidence_spans"]:
                payload["evidence_spans"].append(close_evidence)
        if item["type"] in children_by_type:
            payload["events"] = children_by_type[item["type"]].get(node_id, [])
        if item["type"] in {"event", "action"}:
            payload["participants"] = _participants(item, by_name, ids, existing)
        if item["type"] == "event" and action_children.get(node_id):
            payload["events"] = action_children[node_id]
        objects.append(payload)

    # A source needs a reading position before disclosure queries can order it.
    registered_sources = {
        item["source_id"] for item in snapshot.get("source_orders", []) or []
    }
    if data["source_id"] not in registered_sources:
        ordinal = max(
            (int(item.get("ordinal", 0)) for item in snapshot.get("source_orders", []) or []),
            default=0,
        ) + 1
        objects.append({
            "kind": "source_order",
            "id": f"order_{data['source_id'][:12]}",
            "source_id": data["source_id"],
            "ordinal": ordinal,
            "context_id": "canon",
        })

    # Disclosure checkpoints: the event a reader has just passed when a value
    # becomes visible. This is a reading position, not a moment of change — the
    # same field must never mean both.
    event_by_name: dict[str, str] = {}
    checkpoint_order: list[tuple[int, int, str]] = []
    for event in event_items + action_items:
        quote = event["quotes"][0] if event["quotes"] else ""
        event_id = mention_ids[(event["type"], event["name"], quote)]
        event_by_name.setdefault(event["name"], event_id)
        quote_len = len(quote)
        checkpoint_order.append((event["start"], event["start"] + quote_len, event_id))

    # Properties are one sequence of (value, checkpoint). A value is visible from
    # the checkpoint the reader reaches it at; before that it is simply unknown.
    # It does not follow that the checkpoint caused the value, or that the value
    # began to hold in the story world at that moment.
    accepted_records = ops.property_records()
    known_values = {observation_key(row): row for row in accepted_records if row.get("kind") == "property_change"}
    reserved_ids = {row["id"] for row in accepted_records} | {prop["property"] for prop in data.get("properties", [])}
    records: list[dict] = []
    property_origins: dict[str, list[int]] = {}

    for node in snapshot['nodes']:
        if node['node_type'] in {'event', 'action'} and not node.get('retracted'):
            event_by_name[node['id']] = node['id']
    for (kind, _ref), identifier in ids.items():
        if kind in {'event', 'action'}:
            event_by_name[identifier] = identifier
    definition_records = {row["id"]: row for row in accepted_records
                          if row.get("kind") == "property_definition"}
    symmetric_flags = {}
    for prop in data.get("properties", []):
        if "symmetric" not in prop:
            continue
        name = prop["property"]
        if name in symmetric_flags and symmetric_flags[name] != prop["symmetric"]:
            skipped.append({"name": name, "reason": "同一属性定义的symmetric声明冲突"})
        symmetric_flags[name] = prop["symmetric"]
    cardinalities = {}
    for prop in data.get("properties", []):
        if "cardinality" not in prop:
            continue
        name, proposed = prop["property"], prop["cardinality"]
        if name in cardinalities and cardinalities[name] != proposed:
            skipped.append({"name": name, "reason": "同一属性定义的cardinality声明冲突"})
        cardinalities[name] = proposed
        if name in definition_records and definition_records[name].get("cardinality", "one") != proposed:
            skipped.append({"name": name, "reason": "cardinality与已有定义冲突；请显式修改属性定义"})
    definition_updates = set()
    definitions: dict[str, str] = {
        str(item["id"]): str(item.get("value_type") or "string")
        for item in accepted_records
        if item["kind"] == "property_definition"
    }
    # 同一批里同一个方面可能既有 node 又有 either：either 只是允许「无 / 未知」这类普通值，
    # 两者是同一类。整批先定下类型，免得先出现的 node 把后面的 either 顶掉。
    batch_types: dict[str, str] = {}
    for prop in data.get("properties", []):
        name = str(prop["property"])
        if batch_types.get(name) != "either":
            batch_types[name] = prop["value_type"]

    current_nodes = snapshot_nodes | {row["id"]:row for row in objects if row.get("kind") == "node"}
    progress_owners = {STORYLINE_PROGRESS:"big_event", BIG_EVENT_PROGRESS:"scene"}
    def last_leaf(identifier):
        seen, pending, leaves = set(), [identifier], []
        while pending:
            here = pending.pop()
            if here in seen:
                continue
            seen.add(here)
            node = current_nodes.get(here, {})
            if node.get("events"):
                pending.extend(node["events"])
            elif node.get("node_type") in {"event", "action"}:
                leaves.append(here)
        def position(identifier):
            point = current_nodes[identifier].get("disclosure") or {}
            return orders.get(point.get("source_id"), current_ordinal), point.get("start", -1)
        return max(leaves,key=position) if leaves else None

    for prop in data.get("properties", []):
        node_id = ids.get((prop["node_type"], prop["node"])) if "node_type" in prop else None
        if node_id is None:
            for kind in NODE_TYPES:
                node_id = ids.get((kind, prop["node"]))
                if node_id:
                    break
        if node_id is None:
            for kind in NODE_TYPES:
                node_id = existing.get((kind, prop["node"]))
                if node_id:
                    break
        expected_owner = progress_owners.get(str(prop.get("property") or ""))
        if expected_owner and current_nodes.get(node_id or "", {}).get("node_type") != expected_owner:
            skipped.append({"name":prop["property"], "reason":f"该进展属性须直接记录在{expected_owner}，不自动迁移旧层级"})
            continue
        if node_id is None:
            continue
        property_id = str(prop["property"])
        proposed_type = prop["value_type"]
        known_type = definitions.get(property_id)
        if known_type is None:
            known_type = str(batch_types.get(property_id) or proposed_type)
            definitions[property_id] = known_type
            records.append({
                "kind": "property_definition", "id": property_id,
                "value_type": known_type, "cardinality": cardinalities.get(property_id, "one"),
                "symmetric": symmetric_flags.get(property_id, False),
            })
        elif known_type != proposed_type and {known_type, proposed_type} != {"node", "either"}:
            continue
        if (property_id in definition_records and property_id in symmetric_flags
                and property_id not in definition_updates
                and definition_records[property_id].get("symmetric", False) != symmetric_flags[property_id]):
            records.append(definition_records[property_id] | {"symmetric": symmetric_flags[property_id]})
            definition_updates.add(property_id)
        value_type = known_type
        value = prop.get("value")
        if "value" in prop and value_type in {"node", "either"}:
            resolved_values = []
            values = value if isinstance(value, list) else [value]
            unresolved = False
            for item in values:
                target = None
                if isinstance(item, str):
                    for kind in NODE_TYPES:
                        target = ids.get((kind, item)) or existing.get((kind, item))
                        if target:
                            break
                if target is None and value_type == "node":
                    unresolved = True
                    break
                resolved_values.append(target if target is not None else item)
            if unresolved:
                skipped.append({"name": property_id, "reason": "节点引用未能解析"})
                continue
            value = resolved_values if isinstance(value, list) else resolved_values[0]
        quote = prop["quotes"][0]
        anchor = event_by_name.get(prop.get("at_event") or "")
        if prop.get('at_event') and anchor is None:
            skipped.append({'name': property_id, 'reference': prop['at_event'], 'reason': '显式披露锚点未能解析，不使用临近事件代替'})
            continue
        if not anchor:
            # Nothing in this chapter covers the position. Anchoring to a made-up
            # start would make the value visible before the reader learns it, so
            # the value is reported instead of silently dropped.
            skipped.append({
                "name": f"{prop['node']}.{property_id}",
                "reason": "没有可锚定的披露事件：本章事件未覆盖该位置",
            })
            continue
        record = {
            "kind": "property_change",
            "node_id": node_id, "property_id": property_id,
            "op": prop['op'], "event_id": anchor, "sequence": prop['start'],
            **({'value': value} if 'value' in prop else {}),
            "disclosure": {"source_id": data["source_id"], "start": prop["start"]},
            "evidence": {"source_id": data["source_id"], "quote": quote},
        }
        # Who chose the anchor, not what it proves: "model" means the model named
        # the event; it still only locates the disclosure, not the story change.
        if prop.get("source"):
            record["source"] = str(prop["source"])
        # The category the model chose. It is a suggestion: a human can move the
        # record later, and an absent one simply resolves to 其他.
        if prop.get("category"):
            record["category"] = prop["category"]
        # Who else the sentence involves. Resolved to ids; unknown names drop out
        # rather than becoming dangling references.
        related: list[str] = []
        for name in prop.get("related_nodes") or []:
            target = None
            for kind in NODE_TYPES:
                target = ids.get((kind, name)) or existing.get((kind, name))
                if target:
                    break
            if target and target not in related:
                related.append(target)
        if related:
            record["related_nodes"] = related
        scope = prop.get("scope") or "world"
        if scope != "world":
            holder = None
            for kind in ("person", "group"):
                holder = ids.get((kind, prop.get("holder", "")))
                if holder:
                    break
            if holder is None:
                for kind in ("person", "group"):
                    holder = existing.get((kind, prop.get("holder", "")))
                    if holder:
                        break
            if holder is None:
                continue
            record["scope"] = scope
            record["holder_id"] = holder
        key = observation_key(record)
        previous = known_values.get(key)
        if previous is not None:
            if "_draft_index" in prop:
                property_origins.setdefault(previous["id"], []).append(prop["_draft_index"])
            # Classification edits preserve the identity of the original event change.
            annotations = {field: record[field] for field in ("category", "source")
                           if record.get(field) and previous.get(field) != record[field]}
            combined_related = sorted(set(previous.get("related_nodes") or []) | set(record.get("related_nodes") or []))
            if combined_related != sorted(previous.get("related_nodes") or []):
                annotations["related_nodes"] = combined_related
            if annotations:
                updated = previous | annotations
                if previous["id"] in {row["id"] for row in records}:
                    records = [updated if row["id"] == previous["id"] else row for row in records]
                else:
                    records.append(updated)
                known_values[key] = updated
            continue
        record["id"] = ops.new_property_id()
        while record["id"] in reserved_ids:
            record["id"] = ops.new_property_id()
        reserved_ids.add(record["id"])
        if "_draft_index" in prop:
            property_origins.setdefault(record["id"], []).append(prop["_draft_index"])
        known_values[key] = record
        records.append(record)

    revision = ops.graph_project_info()["revision"]
    node_ids = {item['reference']: ids[(item['type'], item['name'])] for item in data['nodes']
                if 'reference' in item and (item['type'], item['name']) in ids}
    if not objects and not records:
        return {
            "objects": [], "property_records": [], "diagnostics": [], "errors": [],
            "revision": revision, "empty": True, "node_count": 0,
            "property_count": 0, "scene_links": 0,
            "skipped": skipped, "node_ids": node_ids, "property_origins": property_origins,
        }
    diagnostics: list[dict] = []
    errors: list[dict] = []
    return {
        "node_ids": node_ids,
        "property_origins": property_origins,
        "objects": objects,
        "property_records": records,
        "diagnostics": diagnostics,
        "errors": errors,
        "revision": revision,
        "empty": False,
        "skipped": skipped,
        "node_count": len([item for item in objects if item["kind"] == "node"]),
        "property_count": len([
            item for item in records if item["kind"] == "property_change"
        ]),
        "scene_links": sum(len(items) for items in scene_children.values()),
    }
