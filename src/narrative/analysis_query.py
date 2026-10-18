"""Read-only, revision-scoped views for people, narrative events, and evidence."""

from __future__ import annotations

import re
from collections import Counter

_OBJECT_SCHEMA = {
    "entity": {
        "required": ["kind", "id", "name"],
        "optional": ["entity_type", "description", "aliases", "origin", "derived_from", "evidence_refs"],
        "example": {"kind": "entity", "id": "p1", "name": "人物名", "entity_type": "person", "evidence_refs": ["h1"]},
        "note": "entity_type 只能是 person/location/item/other；描述每项判断须由绑定引文支持。",
    },
    "assertion": {
        "required": ["kind", "id", "subject_id", "predicate", "value"],
        "optional": ["scope", "holder_id", "certainty", "origin", "derived_from", "properties", "evidence_refs"],
        "example": {"kind": "assertion", "id": "a1", "subject_id": "p1", "predicate": "转述的事件", "value": "说话者的原文主张", "scope": "claim", "holder_id": "p2", "evidence_refs": ["h2"]},
        "note": "传闻/推测用 claim 且 holder_id 必须是真实说话者；分析者性格推断不要用 scope=world，改用人物特点分类。",
    },
    "event": {
        "required": ["kind", "id", "title", "action", "participants"],
        "optional": ["narrative_type", "origin", "derived_from", "evidence_refs"],
        "example": {"kind": "event", "id": "e1", "title": "有证据的叙事事件", "action": "narrative", "participants": [{"entity_id": "p1", "role": "行动者"}], "narrative_type": "互动", "evidence_refs": ["h3"]},
        "note": "action=narrative 至少一名已有实体参与者，person/location/item/other 均可；角色、标题及narrative_type要有引文；不可附带世界状态或认知效果。旧动作的人物约束不变。",
    },
    "relation": {
        "required": ["kind", "id", "subject_id", "predicate", "object_id"],
        "optional": ["directed", "certainty", "origin", "derived_from", "evidence_refs"],
        "example": {"kind": "relation", "id": "r1", "subject_id": "p1", "predicate": "有据的关系", "object_id": "p2", "evidence_refs": ["h4"]},
        "note": "只表达原文直接支持的人物关系；推断关系标 origin=inference 并给 derived_from 与判断证据。",
    },
    "event_link": {
        "required": ["kind", "id", "source_event_id", "target_event_id", "link_type"],
        "optional": ["description", "origin", "derived_from", "evidence_refs"],
        "example": {"kind": "event_link", "id": "l1", "source_event_id": "e1", "target_event_id": "e2", "link_type": "contrasts", "description": "说明对照依据", "evidence_refs": ["h5"]},
        "note": "link_type 只能是 precedes/causes/motivates/contrasts/echoes/other；属于解读，不写世界效果。",
    },
    "storyline": {
        "required": ["kind", "id", "title", "event_ids"],
        "optional": ["line_type", "description", "origin", "derived_from", "evidence_refs"],
        "example": {"kind": "storyline", "id": "s1", "title": "证据化故事线", "line_type": "main", "event_ids": ["e1", "e2"], "description": "判断说明", "evidence_refs": ["h3", "h5"]},
        "note": "line_type 只能 main/branch/unknown；是可争议的分析解读，无依据则不编造支线。",
    },
    "classification": {
        "required": ["kind", "id", "target_id", "target_kind", "category"],
        "optional": ["note", "origin", "derived_from", "evidence_refs"],
        "example": {"kind": "classification", "id": "c1", "target_id": "p1", "target_kind": "entity", "category": "人物特点/有引文支持的特点", "note": "判断说明", "evidence_refs": ["h1"]},
        "note": "target_kind 只能 entity/assertion/relation/event/storyline；人物性格解读用人物特点/前缀，保留依据和判断说明。",
    },
}


def object_schema(kinds: list[str] | None = None) -> dict:
    selected = list(_OBJECT_SCHEMA) if kinds is None else kinds
    if not isinstance(selected, list) or any(kind not in _OBJECT_SCHEMA for kind in selected):
        raise ValueError("未知分析对象类型")
    return {kind: _OBJECT_SCHEMA[kind] for kind in dict.fromkeys(selected)}


def _sources(record: dict) -> list[str]:
    return list(dict.fromkeys(e["source_id"] for e in record.get("evidence", [])))


def _coverage(snapshot: dict, selected: list[str] | None = None) -> dict:
    sources = snapshot["sources"]
    selected_ids = set(selected) if selected is not None else {source["id"] for source in sources}
    observed = {
        evidence["source_id"]
        for table in ("entities", "assertions", "events", "records")
        for record in snapshot[table]
        for evidence in record.get("evidence", [])
    }
    return {
        "source_ids": [source["id"] for source in sources if source["id"] in selected_ids],
        "covered_source_ids": [source["id"] for source in sources if source["id"] in selected_ids and source["id"] in observed],
        "uncovered_source_ids": [source["id"] for source in sources if source["id"] in selected_ids and source["id"] not in observed],
        "basis": "有正式对象引用该来源的证据；不表示整章已完整分析",
    }


def _matching(record: dict, selected: set[str] | None) -> bool:
    return selected is None or bool(selected.intersection(_sources(record)))


def _participants(event: dict) -> list[dict]:
    pairs = [(item["entity_id"], item["role"]) for item in event.get("participants", [])]
    pairs += [
        (event[key], role)
        for key, role in (("actor_id", "actor"), ("target_id", "target"))
        if event.get(key)
    ]
    return [{"entity_id": entity_id, "role": role} for entity_id, role in dict.fromkeys(pairs)]


def character_profile(snapshot: dict, entity_id: str, source_ids: list[str] | None = None) -> dict:
    entity = next((item for item in snapshot["entities"] if item["id"] == entity_id), None)
    if entity is None or entity.get("entity_type") != "person":
        raise ValueError("人物不存在")
    known_sources = {source["id"] for source in snapshot["sources"]}
    if source_ids is not None and set(source_ids) - known_sources:
        raise ValueError("来源不存在")
    selected = set(source_ids) if source_ids is not None else None
    assertions = [
        item for item in snapshot["assertions"]
        if item.get("subject_id") == entity_id and _matching(item, selected)
    ]
    traits = [
        item for item in assertions
        if item.get("properties", {}).get("analysis_kind") == "trait"
        or item.get("predicate", "").casefold() in {
            "trait", "personality", "character_trait", "特点", "人物特点", "性格", "性格特点"
        }
    ]
    trait_ids = {item["id"] for item in traits}
    classifications = [
        item for item in snapshot["records"]
        if item["kind"] == "classification"
        and item["target_id"] in {entity_id, *trait_ids}
        and _matching(item, selected)
    ]
    traits += [
        {**item, "title": item["category"]}
        for item in classifications
        if item["target_kind"] == "entity"
        and item["target_id"] == entity_id
        and item["category"].startswith("人物特点/")
    ]
    relations = [
        {
            "record": item,
            "counterpart_id": item["object_id"] if item["subject_id"] == entity_id else item["subject_id"],
            "direction": "outgoing" if item["subject_id"] == entity_id else "incoming",
        }
        for item in snapshot["records"]
        if item["kind"] == "relation"
        and entity_id in (item["subject_id"], item["object_id"])
        and _matching(item, selected)
    ]
    states = [
        item for item in snapshot["records"]
        if item["kind"] == "state" and item["subject_id"] == entity_id and _matching(item, selected)
    ]
    events = [
        {"event": item, "roles": [part["role"] for part in _participants(item) if part["entity_id"] == entity_id]}
        for item in snapshot["events"]
        if any(part["entity_id"] == entity_id for part in _participants(item))
        and _matching(item, selected)
    ]
    coverage = _coverage(snapshot, source_ids)
    related_sources = {
        source_id
        for item in [entity, *assertions, *classifications, *states, *(row["record"] for row in relations), *(row["event"] for row in events)]
        for source_id in _sources(item)
    }
    coverage["person_evidenced_source_ids"] = [
        source_id for source_id in coverage["source_ids"] if source_id in related_sources
    ]
    coverage["person_unlinked_source_ids"] = [
        source_id for source_id in coverage["source_ids"] if source_id not in related_sources
    ]
    return {
        "revision": snapshot["revision"],
        "entity": entity,
        "traits": traits,
        "classifications": classifications,
        "assertions": assertions,
        "relations": relations,
        "states": states,
        "events": events,
        "coverage": coverage,
    }


def event_profile(snapshot: dict, event_id: str) -> dict:
    event = next((item for item in snapshot["events"] if item["id"] == event_id), None)
    if event is None:
        raise ValueError("事件不存在")
    entities = {item["id"]: item for item in snapshot["entities"]}
    participants = [
        {"entity": entities.get(item["entity_id"]), "role": item["role"]}
        for item in _participants(event)
    ]
    links = [
        {
            "record": item,
            "other_event_id": item["target_event_id"] if item["source_event_id"] == event_id else item["source_event_id"],
            "direction": "outgoing" if item["source_event_id"] == event_id else "incoming",
        }
        for item in snapshot["records"]
        if item["kind"] == "event_link"
        and event_id in (item["source_event_id"], item["target_event_id"])
    ]
    storylines = [
        item for item in snapshot["records"]
        if item["kind"] == "storyline" and event_id in item["event_ids"]
    ]
    classifications = [
        item for item in snapshot["records"]
        if item["kind"] == "classification" and item["target_id"] == event_id
    ]
    return {
        "revision": snapshot["revision"],
        "event": event,
        "participants": participants,
        "links": links,
        "storylines": storylines,
        "classifications": classifications,
        "evidence": event.get("evidence", []),
    }


def _title(record: dict) -> str:
    return str(record.get("title") or record.get("name") or record.get("predicate") or record.get("kind"))


def _related_entities(record: dict) -> set[str]:
    ids = {
        record[key]
        for key in ("id", "subject_id", "object_id", "holder_id", "actor_id", "target_id", "item_id", "location_id", "value_entity_id")
        if record.get(key)
    }
    ids.update(item["entity_id"] for item in record.get("participants", []))
    ids.update(record.get("entity_ids", []))
    return ids


def _analysis_entities(record: dict, objects: dict[str, dict]) -> set[str]:
    ids = _related_entities(record)
    if record["kind"] == "event_link":
        for event_id in (record["source_event_id"], record["target_event_id"]):
            ids.update(_related_entities(objects.get(event_id, {})))
    elif record["kind"] == "storyline":
        for event_id in record["event_ids"]:
            ids.update(_related_entities(objects.get(event_id, {})))
    elif record["kind"] == "classification":
        target = objects.get(record["target_id"])
        if target:
            ids.update(_analysis_entities(target, objects))
    return ids


def search_analysis(
    snapshot: dict,
    query: str = "",
    *,
    source_ids: list[str] | None = None,
    entity_ids: list[str] | None = None,
    kinds: list[str] | None = None,
    narrative_types: list[str] | None = None,
    categories: list[str] | None = None,
    statuses: list[str] | None = None,
    storyline_ids: list[str] | None = None,
    limit: int = 100,
) -> dict:
    if limit < 1 or limit > 500:
        raise ValueError("检索结果上限须为 1–500")
    known_sources = {item["id"] for item in snapshot["sources"]}
    if source_ids is not None and set(source_ids) - known_sources:
        raise ValueError("来源不存在")
    selected_sources = set(source_ids) if source_ids is not None else None
    selected_entities = set(entity_ids) if entity_ids is not None else None
    selected_kinds = set(kinds) if kinds is not None else None
    selected_types = set(narrative_types) if narrative_types is not None else None
    selected_categories = set(categories) if categories is not None else None
    selected_statuses = set(statuses) if statuses is not None else None
    lines = {
        item["id"]: item for item in snapshot["records"] if item["kind"] == "storyline"
    }
    if storyline_ids is not None and set(storyline_ids) - lines.keys():
        raise ValueError("故事线不存在")
    selected_line_events = (
        {event_id for line_id in storyline_ids or [] for event_id in lines[line_id]["event_ids"]}
        if storyline_ids is not None else None
    )
    needle = query.casefold().strip()
    objects = {
        item["id"]: item
        for table in ("entities", "assertions", "events", "records")
        for item in snapshot[table]
    }
    classified = {}
    for item in snapshot["records"]:
        if item["kind"] == "classification":
            classified.setdefault(item["target_id"], set()).add(item["category"])
    names = {item["id"]: " ".join([item["name"], *item.get("aliases", [])]) for item in snapshot["entities"]}
    rows = []
    for record in [*snapshot["entities"], *snapshot["assertions"], *snapshot["events"], *snapshot["records"]]:
        kind = record["kind"]
        if selected_kinds is not None and kind not in selected_kinds:
            continue
        if not _matching(record, selected_sources):
            continue
        related_entities = _analysis_entities(record, objects)
        if selected_entities is not None and not (related_entities & selected_entities):
            continue
        if selected_types is not None and record.get("narrative_type") not in selected_types:
            continue
        record_categories = classified.get(record["id"], set()).copy()
        for category in (
            record.get("narrative_type"), record.get("category"),
            record.get("line_type"), record.get("predicate"),
        ):
            if category:
                record_categories.add(category)
        if selected_categories is not None and not (record_categories & selected_categories):
            continue
        if selected_statuses is not None and record.get("status", "accepted") not in selected_statuses:
            continue
        if selected_line_events is not None:
            on_line = (
                record["id"] in selected_line_events
                or record["id"] in (storyline_ids or [])
                or record["kind"] == "event_link"
                and record["source_event_id"] in selected_line_events
                and record["target_event_id"] in selected_line_events
                or record["kind"] == "classification"
                and record["target_id"] in selected_line_events.union(storyline_ids or [])
            )
            if not on_line:
                continue
        haystack = " ".join([
            _title(record), str(record.get("description") or ""),
            str(record.get("narrative_type") or ""),
            str(record.get("category") or ""), str(record.get("line_type") or ""),
            str(record.get("value") if record.get("value") is not None else ""),
            str(record.get("note") or ""),
            *(item.get("quote", "") for item in record.get("evidence", [])),
            *(names.get(ref, "") for ref in related_entities),
        ]).casefold()
        if needle and needle not in haystack:
            continue
        rows.append({
            "kind": kind, "id": record["id"], "title": _title(record), "record": record,
            "source_ids": _sources(record), "evidence": record.get("evidence", []),
        })
    # Search original text as well; these hits never masquerade as reviewed facts.
    if needle and (selected_kinds is None or "source" in selected_kinds) and selected_entities is None and selected_line_events is None and selected_types is None and selected_categories is None:
        for source in snapshot["sources"]:
            if selected_sources is not None and source["id"] not in selected_sources:
                continue
            match = re.search(re.escape(query.strip()), source["text"], re.IGNORECASE)
            if match:
                position = match.start()
                quote = match.group()
                evidence = {
                    "source_id": source["id"], "source_hash": source["hash"],
                    "start": position, "end": position + len(quote), "quote": quote,
                }
                rows.append({
                    "kind": "source", "id": source["id"], "title": source["title"],
                    "record": {"id": source["id"], "kind": "source", "title": source["title"]},
                    "source_ids": [source["id"]], "evidence": [evidence],
                })
    return {
        "revision": snapshot["revision"],
        "rows": rows[:limit],
        "total": len(rows),
        "coverage": _coverage(snapshot, source_ids),
    }


def classification_table(snapshot: dict, source_ids: list[str] | None = None) -> dict:
    known = {source["id"] for source in snapshot["sources"]}
    if source_ids is not None and set(source_ids) - known:
        raise ValueError("来源不存在")
    selected = set(source_ids) if source_ids is not None else known
    all_objects = {
        item["id"]: item
        for table in ("entities", "assertions", "events", "records")
        for item in snapshot[table]
    }
    def origin_sources(item: dict) -> list[str]:
        direct = list(dict.fromkeys([
            *_sources(item),
            *(source_id for tag in snapshot["records"]
              if tag["kind"] == "classification" and tag["target_id"] == item["id"]
              for source_id in _sources(tag)),
        ]))
        if item["kind"] == "storyline":
            return list(dict.fromkeys([
                *direct,
                *(source_id for event_id in item["event_ids"]
                  for source_id in _sources(all_objects[event_id]))
            ]))
        if item["kind"] == "entity":
            related = [
                obj for obj in [*snapshot["assertions"], *snapshot["events"], *snapshot["records"]]
                if item["id"] in _related_entities(obj)
            ]
            return list(dict.fromkeys([
                *direct, *(source_id for obj in related for source_id in _sources(obj))
            ]))
        return direct
    counts: dict[tuple[str, str, str], list[str]] = {}
    for item in [*snapshot["entities"], *snapshot["assertions"], *snapshot["events"], *snapshot["records"]]:
        if item["kind"] == "classification":
            continue
        if item["kind"] == "entity":
            categories = ["人物"]
        elif item["kind"] == "event":
            categories = [item.get("narrative_type") or "未分类"]
        elif item["kind"] == "relation":
            categories = [item["predicate"]]
        elif item["kind"] == "storyline":
            categories = [item["line_type"]]
        elif item["kind"] == "state":
            categories = [item["property_id"]]
        elif item["kind"] == "assertion" and (
            item.get("properties", {}).get("analysis_kind") == "trait"
            or item.get("predicate", "").casefold() in {
                "trait", "personality", "character_trait", "特点", "人物特点", "性格", "性格特点"
            }
        ):
            categories = [str(item.get("value") or item["predicate"])]
        else:
            categories = []
        facet = "trait" if item["kind"] == "assertion" and categories else item["kind"]
        categories += [
            tag["category"] for tag in snapshot["records"]
            if tag["kind"] == "classification"
            and tag["target_id"] == item["id"]
            and tag.get("status", "accepted") == "accepted"
        ]
        for source_id in origin_sources(item):
            if source_id in selected:
                for category in dict.fromkeys(categories):
                    counts.setdefault((source_id, facet, category), []).append(item["id"])
    titles = {source["id"]: source["title"] for source in snapshot["sources"]}
    rows = [
        {
            "source_id": source_id, "source_title": titles[source_id],
            "facet": facet, "category": category,
            "narrative_type": category if facet == "event" else None,
            "count": len(object_ids), "object_ids": object_ids,
            "event_ids": object_ids if facet == "event" else [],
            "statuses": dict(Counter(
                all_objects[object_id].get("status", "accepted") for object_id in object_ids
            )),
        }
        for (source_id, facet, category), object_ids in sorted(
            counts.items(), key=lambda pair: (list(titles).index(pair[0][0]), pair[0][1], pair[0][2])
        )
    ]
    return {"revision": snapshot["revision"], "rows": rows, "coverage": _coverage(snapshot, source_ids)}


def analysis_context(snapshot: dict, source_ids: list[str] | None = None) -> dict:
    known = {source["id"] for source in snapshot["sources"]}
    if source_ids is not None and set(source_ids) - known:
        raise ValueError("来源不存在")
    selected = set(source_ids) if source_ids is not None else known
    return {
        "revision": snapshot["revision"],
        "sources": [source for source in snapshot["sources"] if source["id"] in selected],
        "entities": snapshot["entities"],
        "events": snapshot["events"],
        "assertions": snapshot["assertions"],
        "records": snapshot["records"],
        "coverage": _coverage(snapshot, source_ids),
    }
