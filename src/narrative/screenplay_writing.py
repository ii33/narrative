"""Atomic scene-local writing; input archives stay in existing task storage."""
from __future__ import annotations

import copy
import hashlib
import json

from pydantic import TypeAdapter

from .screenplay_writing_models import (
    BlockChange,
    BlockDraft,
    EntityUsage,
    ScriptUnit,
    UnitChange,
    UnitDraft,
    WritingBasis,
)

EXCLUDED = {"revision", "plan_revision", "body_revision", "structure_revision", "outline_revision",
            "created_at", "updated_at", "sort_id", "position", "source_node_id", "disclosure", "evidence", "evidence_spans"}


def _normalized(value):
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items() if key not in EXCLUDED}
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    return value


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _get(state, table, identifier):
    result = state[table].get(identifier)
    if result is None:
        raise ValueError(f"对象不存在：{table}/{identifier}")
    return result


def _units(state, segment_id):
    return sorted((row for row in state["script_units"].values() if row["segment_id"] == segment_id), key=lambda row: row["position"])


def _blocks(state, unit_id):
    return sorted((row for row in state["script_blocks"].values() if row["unit_id"] == unit_id), key=lambda row: row["position"])


def _ordered_segments(state, screenplay_id):
    result, seen = [], set()
    def walk(kind, identifier):
        if (kind, identifier) in seen:
            raise ValueError("剧本结构循环")
        seen.add((kind, identifier))
        children = sorted((row for row in state["script_structure_entries"].values() if row["screenplay_id"] == screenplay_id
                           and row["parent_kind"] == kind and row["parent_id"] == identifier), key=lambda row: row["position"])
        for row in children:
            if row["child_kind"] == "segment":
                result.append(_get(state, "script_segments", row["child_id"]))
            else:
                walk(row["child_kind"], row["child_id"])
    walk("screenplay", screenplay_id)
    return result


def _ordered_units(state, screenplay_id):
    return [unit for segment in _ordered_segments(state, screenplay_id) if segment.get("enabled", True)
            for unit in _units(state, segment["id"]) if unit.get("enabled", True)]


def _plan_input(unit):
    plan = copy.deepcopy(unit["plan"])
    plan.pop("title", None)
    return {"kind": unit["kind"], "enabled": unit["enabled"], "plan": plan}


def _entity(adaptation, identifier, types=None):
    node = next((node for node in adaptation["nodes"] if node["id"] == identifier), None)
    if not node or (types and node["node_type"] not in types):
        raise ValueError(f"剧本实体引用不存在或类型错误：{identifier}")
    return node


def _segment_timeline(state, source_adaptation, segment_id):
    from .screenplay_timeline import screenplay_timeline
    segment = _get(state, "script_segments", segment_id)
    return screenplay_timeline(state, source_adaptation, segment["screenplay_id"])


def validate_writing(state, adaptation):
    positions = set()
    unit_timelines = {}
    screenplay_timelines = {}
    for unit in state["script_units"].values():
        segment = _get(state, "script_segments", unit["segment_id"])
        screenplay_id = segment["screenplay_id"]
        if screenplay_id not in screenplay_timelines:
            screenplay_timelines[screenplay_id] = _segment_timeline(state, adaptation, unit["segment_id"])
        timeline = screenplay_timelines[screenplay_id]
        unit_timelines[unit["id"]] = timeline
        key = unit["segment_id"], unit["position"]
        if key in positions:
            raise ValueError("段内场次position重复")
        positions.add(key)
        plan = unit["plan"]
        if plan.get("location_id"):
            _entity(timeline, plan["location_id"], {"location"})
        for identifier in plan.get("cast_ids", []):
            _entity(timeline, identifier, {"person", "group"})
        if len(set(plan.get("cast_ids", []))) != len(plan.get("cast_ids", [])):
            raise ValueError("场表cast_ids不能重复")
        for requirement in plan.get("continuity_requirements", []):
            if requirement.get("entity_id"):
                _entity(timeline, requirement["entity_id"])
        if unit["kind"] == "screen_segment" and not plan.get("presentation"):
            raise ValueError("独立画面段须指定presentation")
        if unit.get("body_basis_id"):
            basis = _get(state, "script_writing_bases", unit["body_basis_id"])
            if (basis["target_kind"], basis["target_id"], basis["stage"]) != ("unit", unit["id"], "body"):
                raise ValueError("场次正文依据不属于当前场")
    positions.clear()
    for block in state["script_blocks"].values():
        _get(state, "script_units", block["unit_id"])
        timeline = unit_timelines[block["unit_id"]]
        key = block["unit_id"], block["position"]
        if key in positions:
            raise ValueError("场内正文块position重复")
        positions.add(key)
        payload = block["payload"]
        if block["type"] == "dialogue" and payload.get("speaker_id"):
            _entity(timeline, payload["speaker_id"], {"person", "group"})
        if block["type"] == "sound" and payload.get("entity_id"):
            _entity(timeline, payload["entity_id"])
    for usage in state["script_entity_usages"].values():
        _get(state, "script_units", usage["unit_id"])
        _entity(unit_timelines[usage["unit_id"]], usage["entity_id"], {"person", "group"} if usage["role"] == "speaker" else None)
        if usage.get("block_id") and _get(state, "script_blocks", usage["block_id"])["unit_id"] != usage["unit_id"]:
            raise ValueError("实体使用记录正文块不属于当前场")
    targets = set()
    for basis in state["script_writing_bases"].values():
        key = basis["target_kind"], basis["target_id"], basis["stage"]
        if key in targets:
            raise ValueError("只允许保存当前写作依据")
        targets.add(key)
        _get(state, "script_units" if basis["target_kind"] == "unit" else "script_segments", basis["target_id"])


def _ancestors(state, segment):
    result, current, seen = [], ("segment", segment["id"]), set()
    entries = list(state["script_structure_entries"].values())
    while current not in seen:
        seen.add(current)
        row = next((row for row in entries if (row["child_kind"], row["child_id"]) == current), None)
        if not row:
            break
        current = row["parent_kind"], row["parent_id"]
        if current[0] == "screenplay":
            break
        result.append(current)
    return result


REF_TABLES = {"screenplay": "screenplays", "segment": "script_segments", "episode": "script_episodes", "act": "script_acts",
              "unit_plan": "script_units", "unit_body": "script_units", "previous_unit": "script_units"}


def _default_dependencies(state, segment, unit=None):
    refs = [{"kind": "screenplay", "id": segment["screenplay_id"]}, {"kind": "segment", "id": segment["id"]}]
    refs += [{"kind": kind, "id": identifier} for kind, identifier in _ancestors(state, segment)]
    if unit:
        refs.append({"kind": "previous_unit", "id": unit["id"]})
    return refs


def _dependencies(state, adaptation, refs, screenplay_id, effective_node_ids=None):
    result, normalized_refs, seen = [], [], set()
    for raw in refs:
        if not isinstance(raw, dict) or set(raw) != {"kind", "id"} or not isinstance(raw["kind"], str) or not isinstance(raw["id"], str):
            raise ValueError("dependency_refs须为明确的kind/id对象数组")
        kind, identifier = raw["kind"], raw["id"]
        owner = None
        if (kind, identifier) in seen:
            continue
        seen.add((kind, identifier))
        normalized_refs.append(dict(raw))
        if kind == "adaptation_entity":
            from .screenplay_spans import generation_node
            node = _entity(adaptation, identifier)
            projected = generation_node(adaptation, node, effective_node_ids)
            # An explicit reference expands reading, never the creation span.
            value = copy.deepcopy(node)
            value["reference_only"] = projected is None or bool(projected.get("reference_only"))
            value["current_creation_excluded"] = value["reference_only"]
            if value["reference_only"]:
                value["reference_note"] = "屏蔽、范围外或上级参考资料；知悉不代表纳入本次创作，以当前改编与用户要求为准"
        else:
            if kind not in REF_TABLES:
                raise ValueError("未知写作依赖类型")
            obj = _get(state, REF_TABLES[kind], identifier)
            owner = obj.get("screenplay_id") or (state["script_segments"][obj["segment_id"]]["screenplay_id"] if "segment_id" in obj else obj["id"])
            # References may belong to another screenplay in this project;
            # their identity is recorded below, without granting write scope.
            if kind == "previous_unit":
                ordered = _ordered_units(state, owner)
                index = next((index for index, unit in enumerate(ordered) if unit["id"] == identifier), -1)
                previous = ordered[index - 1] if index > 0 else None
                value = {"previous_unit_id": previous["id"], "plan": _plan_input(previous),
                         "blocks": _normalized(_blocks(state, previous["id"]))} if previous else None
            elif kind == "unit_plan":
                value = _plan_input(obj)
            elif kind == "unit_body":
                value = {"blocks": _normalized(_blocks(state, identifier)), "enabled": obj["enabled"]}
            elif kind == "screenplay":
                value = {key: obj.get(key) for key in ("creative_brief", "language", "target_duration_seconds")}
            else:
                value = {key: obj.get(key) for key in ("brief", "role", "target_duration_seconds", "enabled")}
        result.append({"kind": kind, "id": identifier, "input": value}
                      | ({"source_screenplay_id": owner} if kind != "adaptation_entity" and owner != screenplay_id else {}))
    return normalized_refs, result


def _context(state, adaptation, segment_id, unit_id=None, dependency_refs=None, *, allow_unusable=False):
    from .screenplay_spans import resolve_span
    segment = _get(state, "script_segments", segment_id)
    from .prompt_defaults import DEFAULT_PROMPTS
    project_style = copy.deepcopy(adaptation.get("_project_style", {"revision": 0, "text": ""}))
    prompt_texts = adaptation.get("_prompt_config", {}).get("texts") or {key: row["text"] for key, row in DEFAULT_PROMPTS.items()}
    adaptation = _segment_timeline(state, adaptation, segment_id)
    resolved = resolve_span(adaptation, _get(state, "adaptation_spans", segment["span_id"]))
    unit = _get(state, "script_units", unit_id) if unit_id else None
    refs = _default_dependencies(state, segment, unit) if dependency_refs is None else (
        _default_dependencies(state, segment) + dependency_refs)
    refs, inputs = _dependencies(state, adaptation, refs, segment["screenplay_id"], resolved["effective_node_ids"])
    entity_ids = []
    if unit:
        entity_ids = [unit["plan"].get("location_id"), *unit["plan"].get("cast_ids", [])]
        entity_ids += [row.get("entity_id") for row in unit["plan"].get("continuity_requirements", [])]
    entities = []
    for identifier in dict.fromkeys(identifier for identifier in entity_ids if identifier):
        try:
            from .screenplay_spans import generation_node
            projected = generation_node(adaptation, _entity(adaptation, identifier), resolved["effective_node_ids"])
            if projected is None or projected.get("reference_only"):
                raise ValueError("本场实体已屏蔽或不属于可用生成内容")
            entities.append(projected)
        except ValueError:
            if not allow_unusable:
                raise
            entities.append({"id": identifier, "invalid": True})
    plan_input = _plan_input(unit) if unit else None
    effective_events = list((resolved.get("input") or {}).get("source_event_ids", []))
    bound_events = list(unit["plan"].get("source_event_ids", [])) if unit else []
    invalid_events = [identifier for identifier in bound_events if identifier not in effective_events]
    if invalid_events and not allow_unusable:
        raise ValueError("本场场表引用的剧本事件已删除、屏蔽或不在指定范围，请先修正本场场表；旧正文已保留")
    creation_scope = {"screenplay_id": segment["screenplay_id"], "timeline_id": adaptation["timeline_id"],
        "segment_id": segment_id, "unit_id": unit_id,
        "effective_event_ids": [identifier for identifier in (bound_events or effective_events) if identifier in effective_events],
        "plan_event_binding": "explicit" if bound_events else "plan_and_range", "current_scene_plan": plan_input,
        "plot_authority": ["specified_screenplay_timeline_and_bound_range", "consistent_current_scene_plan"],
        "expression_authority": ["current_user_request", "project_style", "screenplay_creative_brief", "segment_and_scene_creative_requirements"],
        "invalid_event_ids": invalid_events,
        "reference_only": ["original", "extraction", "source_adaptation", "other_screenplay_timelines", "previous_chat", "old_drafts"]}
    context_input = {"fingerprint_version": 3, "timeline_id": adaptation["timeline_id"],
                     "dependencies": inputs, "plan_entities": entities, "project_style": project_style,
                     "creation_scope": creation_scope}
    return {"segment_id": segment_id, "unit_id": unit_id, "timeline_id": adaptation["timeline_id"], "span": resolved,
            "span_fingerprint": resolved["span_fingerprint"], "context_fingerprint": _fingerprint(context_input),
            "plan_fingerprint": _fingerprint(plan_input) if unit else None, "dependency_refs": refs,
            "reference_context": context_input, "plan_input": plan_input,
            "project_style": project_style, "creation_scope": creation_scope,
            "generation_allowed": resolved["status"] == "valid" and not invalid_events and (not unit or bool(unit["plan"]["summary"].strip())),
            "reference_policy": {"guidance": prompt_texts["reference_policy"]}}



def _basis_state(state, adaptation, target_kind, target_id, basis_id):
    unit = state["script_units"].get(target_id) if target_kind == "unit" else None
    segment_id = unit["segment_id"] if unit else target_id
    basis = state["script_writing_bases"].get(basis_id)
    try:
        context = _context(state, adaptation, segment_id, target_id if unit else None,
                           basis.get("dependency_refs") if basis else None, allow_unusable=True)
    except ValueError:
        return "changed" if basis else "unbased"
    if context["span"]["status"] == "invalid" or context["creation_scope"]["invalid_event_ids"]:
        return "invalid"
    if context["span"]["status"] == "empty":
        return "empty_source"
    if not basis:
        return "unbased"
    fields = ("span_fingerprint", "context_fingerprint", "plan_fingerprint") if unit else ("span_fingerprint", "context_fingerprint")
    return "current" if all(basis.get(key) == context[key] for key in fields) else "changed"


def writing_unit_view(state, adaptation, unit_id):
    unit = copy.deepcopy(_get(state, "script_units", unit_id))
    blocks = copy.deepcopy(_blocks(state, unit_id))
    diagnostics = [{"code": "DIALOGUE_SPEAKER_UNBOUND", "severity": "warning", "block_id": block["id"],
                    "message": "对白人物尚未绑定实体"} for block in blocks if block["type"] == "dialogue" and not block["payload"].get("speaker_id")]
    if not unit["plan"].get("location_id") and not unit["plan"].get("location_text") and unit["kind"] == "scene":
        diagnostics.append({"code": "SCENE_LOCATION_UNSPECIFIED", "severity": "warning", "message": "场次地点尚未指定"})
    return unit | {"blocks": blocks, "entity_usages": [copy.deepcopy(row) for row in state["script_entity_usages"].values() if row["unit_id"] == unit_id],
                   "body_presence": "present" if blocks else "empty", "basis_state": _basis_state(state, adaptation, "unit", unit_id, unit.get("body_basis_id")),
                   "diagnostics": diagnostics}


def writing_segment_view(state, adaptation, segment_id):
    from .screenplay_scene_policy import scene_source_diagnostics
    from .screenplay_spans import resolve_span
    segment = copy.deepcopy(_get(state, "script_segments", segment_id))
    adopted = segment.get("adopted_outline_revision")
    resolved = resolve_span(_segment_timeline(state, adaptation, segment_id), state["adaptation_spans"][segment["span_id"]])
    return segment | {"units": [writing_unit_view(state, adaptation, unit["id"]) for unit in _units(state, segment_id)],
                      "scene_partition_diagnostics": scene_source_diagnostics(_units(state, segment_id), resolved),
                      "outline_state": "planning" if adopted is None else "adopted" if adopted == segment["outline_revision"] else "changed",
                      "outline_basis_state": _basis_state(state, adaptation, "segment", segment_id, segment.get("outline_basis_id"))}


def _touch_outline(segment):
    from .screenplay_store import now
    segment["outline_revision"] += 1
    segment["revision"] += 1
    segment["updated_at"] = now()


def _check_context(context, *, span_fingerprint, context_fingerprint, plan_fingerprint=None):
    if not context["generation_allowed"]:
        raise ValueError("绑定范围invalid/empty或场次概要为空，不能提交新的生成结果；旧正文已保留")
    if context["span_fingerprint"] != span_fingerprint:
        raise ValueError("写作范围输入已变化，拒绝过期结果")
    if context["context_fingerprint"] != context_fingerprint:
        raise ValueError("写作实际上下文依赖已变化，拒绝过期结果")
    if plan_fingerprint is not None and context["plan_fingerprint"] != plan_fingerprint:
        raise ValueError("本场plan输入已变化，拒绝过期结果")


def _save_basis(state, target_kind, target_id, context, reason, manifest_ref=None, task_id=None):
    from .screenplay_store import new_id
    table = state["script_writing_bases"]
    existing = [row for row in table.values() if row["target_kind"] == target_kind and row["target_id"] == target_id]
    for row in existing:
        del table[row["id"]]
    identifier = existing[0]["id"] if existing else new_id("basis")
    table[identifier] = WritingBasis(id=identifier, target_kind=target_kind, target_id=target_id,
        stage="body" if target_kind == "unit" else "scene_table", span_fingerprint=context["span_fingerprint"],
        context_fingerprint=context["context_fingerprint"], plan_fingerprint=context["plan_fingerprint"],
        fingerprint_version=context["reference_context"]["fingerprint_version"],
        dependency_refs=context["dependency_refs"], project_style=context["project_style"], creation_scope=context["creation_scope"],
        manifest_ref=manifest_ref, task_id=task_id, reason=reason).model_dump(mode="json")
    target = state["script_units" if target_kind == "unit" else "script_segments"][target_id]
    target["body_basis_id" if target_kind == "unit" else "outline_basis_id"] = identifier
    return identifier


def _place(state, table, parent_key, parent_id, identifier, after_id):
    rows = sorted((row for row in state[table].values() if row[parent_key] == parent_id and row["id"] != identifier), key=lambda row: row["position"])
    index = 0 if after_id is None else next((index + 1 for index, row in enumerate(rows) if row["id"] == after_id), -1)
    if index < 0 or after_id == identifier:
        raise ValueError("排序参照不存在、跨所属对象或指向自身")
    rows.insert(index, state[table][identifier])
    for index, row in enumerate(rows, 1):
        row["position"] = index * 1024


def _update_unit(unit, changes):
    from .screenplay_store import now
    from .screenplay_writing_models import ScenePlan
    changes = copy.deepcopy(changes)
    if "plan" in changes:
        changes["plan"] = ScenePlan.model_validate(changes["plan"]).model_dump(mode="json")
    before = _plan_input(unit)
    unit.update(copy.deepcopy(changes))
    if _plan_input(unit) != before:
        unit["plan_revision"] += 1
    unit["revision"] += 1
    unit["updated_at"] = now()


def _create_unit(state, segment_id, draft, position):
    from .screenplay_store import new_id
    draft = UnitDraft.model_validate(draft).model_dump(mode="json")
    if draft["id"]:
        raise ValueError("新场次ID由程序分配；已有场次须按稳定ID更新")
    identifier = new_id("unit")
    state["script_units"][identifier] = ScriptUnit.model_validate(draft | {"id": identifier, "segment_id": segment_id, "position": position}).model_dump(mode="json")
    return identifier


def _delete_unit(state, unit, policy):
    blocks = _blocks(state, unit["id"])
    if blocks and policy != "discard":
        raise ValueError("删除已有正文的场次须显式body_policy=discard")
    for block in blocks:
        del state["script_blocks"][block["id"]]
    for table in ("script_entity_usages", "script_writing_bases"):
        for identifier, row in list(state[table].items()):
            if row.get("unit_id") == unit["id"] or row.get("target_kind") == "unit" and row.get("target_id") == unit["id"]:
                del state[table][identifier]
    del state["script_units"][unit["id"]]


def _block_usages(state, adaptation, unit_id, entity_usages=None, old_blocks=None):
    from .screenplay_store import new_id
    # Preserve explicit usages on surviving blocks during a local edit.
    old = [row for row in state["script_entity_usages"].values() if row["unit_id"] == unit_id]
    old_by_key = {(row.get("block_id"), row["entity_id"], row["role"]): row for row in old}
    old_derived = set()
    for block in old_blocks or []:
        payload = block["payload"]
        if block["type"] == "dialogue" and payload.get("speaker_id"):
            old_derived.add((block["id"], payload["speaker_id"], "speaker"))
        if block["type"] == "sound" and payload.get("entity_id"):
            old_derived.add((block["id"], payload["entity_id"], "used"))
    valid_blocks = {row["id"] for row in _blocks(state, unit_id)}
    for row in old:
        del state["script_entity_usages"][row["id"]]
    entries = [{key: row.get(key) for key in ("block_id", "entity_id", "role")} for row in old
               if entity_usages is None and (row.get("block_id") is None or row["block_id"] in valid_blocks)
               and (row.get("block_id"), row["entity_id"], row["role"]) not in old_derived]
    for block in _blocks(state, unit_id):
        payload = block["payload"]
        if block["type"] == "dialogue" and payload.get("speaker_id"):
            entries.append({"block_id": block["id"], "entity_id": payload["speaker_id"], "role": "speaker"})
        if block["type"] == "sound" and payload.get("entity_id"):
            entries.append({"block_id": block["id"], "entity_id": payload["entity_id"], "role": "used"})
    for raw in entity_usages or []:
        if set(raw) - {"block_id", "entity_id", "role"}:
            raise ValueError("EntityUsage仅允许block_id/entity_id/role")
        entries.append(raw)
    seen = set()
    for raw in entries:
        key = raw.get("block_id"), raw.get("entity_id"), raw.get("role")
        if key in seen:
            continue
        seen.add(key)
        identifier = old_by_key[key]["id"] if key in old_by_key else new_id("usage")
        row = EntityUsage(id=identifier, unit_id=unit_id, **raw).model_dump(mode="json")
        _entity(adaptation, row["entity_id"])
        state["script_entity_usages"][identifier] = row


def _target_blocks(state, unit_id, target_block_ids):
    if not isinstance(target_block_ids, list) or any(not isinstance(identifier, str) for identifier in target_block_ids) or len(set(target_block_ids)) != len(target_block_ids):
        raise ValueError("target_block_ids须为明确唯一ID清单")
    for identifier in target_block_ids:
        if _get(state, "script_blocks", identifier)["unit_id"] != unit_id:
            raise ValueError("正文目标块不属于当前场")
    return set(target_block_ids)


class WritingOperationsMixin:
    def read_scene_table_context(self, segment_id, dependency_refs=None):
        from .screenplay_scene_policy import scene_table_policy
        from .screenplay_store import read_context
        state, adaptation = read_context(self)
        segment = copy.deepcopy(_get(state, "script_segments", segment_id))
        # Keep default output metadata concise; callers can explicitly request
        # unit_plan/unit_body dependencies or read the complete segment.
        segment["units"] = [{key: copy.deepcopy(unit[key]) for key in (
            "id", "kind", "enabled", "scene_number", "plan_revision", "body_revision", "revision")}
            | {"body_presence": "present" if _blocks(state, unit["id"]) else "empty",
               "source_event_ids": list(unit["plan"].get("source_event_ids", []))}
            for unit in _units(state, segment_id)]
        from .prompt_settings import prompt_snapshot
        context = _context(state, adaptation, segment_id, dependency_refs=dependency_refs, allow_unusable=True)
        return context | {"segment": segment, "expected_outline_revision": segment["outline_revision"],
                          "scene_table_policy": scene_table_policy(context["span"], prompt_snapshot(self))}

    def submit_scene_table(self, segment_id, units, expected_outline_revision, span_fingerprint,
                           context_fingerprint, *, dependency_refs=None, manifest_ref=None, task_id=None, reason):
        from .screenplay_store import expect_revision, write_transaction
        drafts = TypeAdapter(list[UnitDraft]).validate_python(units)
        def mutate(state, adaptation, _db):
            segment = _get(state, "script_segments", segment_id)
            expect_revision(segment, expected_outline_revision, "outline_revision")
            context = _context(state, adaptation, segment_id, dependency_refs=dependency_refs)
            _check_context(context, span_fingerprint=span_fingerprint, context_fingerprint=context_fingerprint)
            from .screenplay_scene_policy import prepare_generated_scene_table
            prepared = prepare_generated_scene_table([draft.model_dump(mode="json") for draft in drafts], context["span"])
            old_ids = {unit["id"] for unit in _units(state, segment_id)}
            incoming = [draft.id for draft in drafts if draft.id]
            if len(incoming) != len(set(incoming)) or not old_ids <= set(incoming):
                raise ValueError("重拆场须保留已有稳定ID；删除/拆合须显式动作及正文策略")
            for index, draft in enumerate(prepared, 1):
                if draft["id"]:
                    unit = _get(state, "script_units", draft.pop("id"))
                    if unit["segment_id"] != segment_id:
                        raise ValueError("场次不能跨段更新")
                    _update_unit(unit, draft)
                    unit["position"] = index * 1024
                else:
                    _create_unit(state, segment_id, draft, index * 1024)
            _touch_outline(segment)
            _save_basis(state, "segment", segment_id, context, reason, manifest_ref, task_id)
            return writing_segment_view(state, adaptation, segment_id) | {"status": "accepted"}
        return write_transaction(self, reason, mutate)

    def apply_scene_table_changes(self, segment_id, changes, expected_outline_revision, *, reason):
        from .screenplay_store import expect_revision, write_transaction
        edits = TypeAdapter(list[UnitChange]).validate_python(changes)
        def mutate(state, adaptation, _db):
            segment = _get(state, "script_segments", segment_id)
            expect_revision(segment, expected_outline_revision, "outline_revision")
            for model in edits:
                action = model.model_dump(mode="json", exclude_unset=True)
                op = action["op"]
                if op == "insert":
                    identifier = _create_unit(state, segment_id, action["unit"], 0)
                    _place(state, "script_units", "segment_id", segment_id, identifier, action.get("after_id"))
                else:
                    unit = _get(state, "script_units", action["unit_id"])
                    if unit["segment_id"] != segment_id:
                        raise ValueError("场表操作不能扩大到别段")
                    if op == "update":
                        patch = action["changes"]
                        if any(value is None for key, value in patch.items() if key != "scene_number"):
                            raise ValueError("场次字段不可设为null（scene_number除外）")
                        _update_unit(unit, patch)
                    elif op == "move":
                        _place(state, "script_units", "segment_id", segment_id, unit["id"], action.get("after_id"))
                        unit["revision"] += 1
                    else:
                        _delete_unit(state, unit, action["body_policy"])
            if edits:
                _touch_outline(segment)
            return writing_segment_view(state, adaptation, segment_id) | {"status": "accepted"}
        return write_transaction(self, reason, mutate)

    def adopt_scene_table(self, segment_id, expected_outline_revision, adopted=True, *, reason):
        from .screenplay_store import expect_revision, now, write_transaction
        if not isinstance(adopted, bool):
            raise ValueError("adopted须为boolean")
        def mutate(state, adaptation, _db):
            segment = _get(state, "script_segments", segment_id)
            expect_revision(segment, expected_outline_revision, "outline_revision")
            segment["adopted_outline_revision"] = segment["outline_revision"] if adopted else None
            segment["revision"] += 1
            segment["updated_at"] = now()
            return writing_segment_view(state, adaptation, segment_id)
        return write_transaction(self, reason, mutate)

    def read_script_unit_context(self, unit_id, dependency_refs=None, target_block_ids=None):
        from .screenplay_store import read_context
        state, adaptation = read_context(self)
        unit = _get(state, "script_units", unit_id)
        if target_block_ids is not None:
            _target_blocks(state, unit_id, target_block_ids)
        return _context(state, adaptation, unit["segment_id"], unit_id, dependency_refs, allow_unusable=True) | {
            "unit": writing_unit_view(state, adaptation, unit_id), "expected_plan_revision": unit["plan_revision"],
            "expected_body_revision": unit["body_revision"], "target_block_ids": target_block_ids}

    def submit_script_unit(self, unit_id, blocks, expected_plan_revision, expected_body_revision,
                           span_fingerprint, context_fingerprint, plan_fingerprint, *, dependency_refs=None,
                           manifest_ref=None, task_id=None, entity_usages=None, reason):
        from .screenplay_store import expect_revision, new_id, now, write_transaction
        drafts = TypeAdapter(list[BlockDraft]).validate_python(blocks)
        if not drafts:
            raise ValueError("清空正文须使用显式正文块删除操作；生成提交不得为空")
        def mutate(state, adaptation, _db):
            unit = _get(state, "script_units", unit_id)
            expect_revision(unit, expected_plan_revision, "plan_revision")
            expect_revision(unit, expected_body_revision, "body_revision")
            context = _context(state, adaptation, unit["segment_id"], unit_id, dependency_refs)
            _check_context(context, span_fingerprint=span_fingerprint, context_fingerprint=context_fingerprint, plan_fingerprint=plan_fingerprint)
            old_blocks = copy.deepcopy(_blocks(state, unit_id))
            old_ids = {block["id"] for block in old_blocks}
            seen = set()
            for index, model in enumerate(drafts, 1):
                draft = model.model_dump(mode="json")
                identifier = draft.pop("id") or new_id("block")
                if identifier in seen or identifier in state["script_blocks"] and identifier not in old_ids:
                    raise ValueError("正文块ID重复或属于别场")
                if model.id and identifier not in old_ids:
                    raise ValueError("新正文块ID由程序分配")
                seen.add(identifier)
                state["script_blocks"][identifier] = draft | {"id": identifier, "unit_id": unit_id, "position": index * 1024}
            for identifier in old_ids - seen:
                del state["script_blocks"][identifier]
            _block_usages(state, _segment_timeline(state, adaptation, unit["segment_id"]), unit_id, entity_usages, old_blocks)
            unit["body_revision"] += 1
            unit["revision"] += 1
            unit["updated_at"] = now()
            _save_basis(state, "unit", unit_id, context, reason, manifest_ref, task_id)
            return writing_unit_view(state, adaptation, unit_id) | {"status": "accepted"}
        return write_transaction(self, reason, mutate)

    def apply_script_block_changes(self, unit_id, changes, target_block_ids, expected_body_revision, *, reason):
        from .screenplay_store import expect_revision, new_id, now, write_transaction
        edits = TypeAdapter(list[BlockChange]).validate_python(changes)
        def mutate(state, adaptation, _db):
            unit = _get(state, "script_units", unit_id)
            expect_revision(unit, expected_body_revision, "body_revision")
            targets = _target_blocks(state, unit_id, target_block_ids)
            if not edits:
                return writing_unit_view(state, adaptation, unit_id) | {"status": "accepted"}
            old_blocks = copy.deepcopy(_blocks(state, unit_id))
            for model in edits:
                action = model.model_dump(mode="json", exclude_unset=True)
                op = action["op"]
                if op == "insert":
                    draft = BlockDraft.model_validate(action["block"]).model_dump(mode="json")
                    if draft.pop("id"):
                        raise ValueError("新增正文块ID由程序分配")
                    identifier = new_id("block")
                    state["script_blocks"][identifier] = draft | {"id": identifier, "unit_id": unit_id, "position": 0}
                    _place(state, "script_blocks", "unit_id", unit_id, identifier, action.get("after_id"))
                else:
                    identifier = action["block_id"]
                    if identifier not in targets:
                        raise ValueError("局部正文修改不得覆盖非目标块")
                    block = _get(state, "script_blocks", identifier)
                    if op == "update":
                        draft = BlockDraft.model_validate(action["block"]).model_dump(mode="json")
                        if draft.pop("id") not in (None, identifier):
                            raise ValueError("更新正文块不得改变身份")
                        block.update(draft)
                    elif op == "move":
                        _place(state, "script_blocks", "unit_id", unit_id, identifier, action.get("after_id"))
                    else:
                        del state["script_blocks"][identifier]
            _block_usages(state, _segment_timeline(state, adaptation, unit["segment_id"]), unit_id, old_blocks=old_blocks)
            if edits:
                unit["body_revision"] += 1
                unit["revision"] += 1
                unit["updated_at"] = now()
            return writing_unit_view(state, adaptation, unit_id) | {"status": "accepted"}
        return write_transaction(self, reason, mutate)

    def mark_writing_basis_current(self, target_kind, target_id, expected_revision, *, reason,
                                   dependency_refs=None, manifest_ref=None, task_id=None):
        from .screenplay_store import expect_revision, now, write_transaction
        if target_kind not in {"segment", "unit"}:
            raise ValueError("写作依据目标须为segment或unit")
        def mutate(state, adaptation, _db):
            target = _get(state, "script_units" if target_kind == "unit" else "script_segments", target_id)
            expect_revision(target, expected_revision)
            segment_id = target["segment_id"] if target_kind == "unit" else target_id
            context = _context(state, adaptation, segment_id, target_id if target_kind == "unit" else None, dependency_refs)
            if not context["generation_allowed"]:
                raise ValueError("无效或空范围不能标记写作依据为current")
            _save_basis(state, target_kind, target_id, context, reason, manifest_ref, task_id)
            target["revision"] += 1
            target["updated_at"] = now()
            return writing_unit_view(state, adaptation, target_id) if target_kind == "unit" else writing_segment_view(state, adaptation, target_id)
        return write_transaction(self, reason, mutate)

    def prepare_script_writing_targets(self, screenplay_id, unit_ids=None, include_existing=False, include_screen_segments=False):
        from .screenplay_store import read_context
        if not isinstance(include_existing, bool) or not isinstance(include_screen_segments, bool):
            raise ValueError("批量选择选项须为boolean")
        state, _adaptation = read_context(self)
        screenplay = _get(state, "screenplays", screenplay_id)
        ordered = _ordered_units(state, screenplay_id)
        by_id = {unit["id"]: unit for unit in ordered}
        if unit_ids is None:
            selected = [unit for unit in ordered if (include_screen_segments or unit["kind"] == "scene")
                        and (include_existing or not _blocks(state, unit["id"]))]
        else:
            if not isinstance(unit_ids, list) or any(not isinstance(identifier, str) for identifier in unit_ids) or len(set(unit_ids)) != len(unit_ids) or any(identifier not in by_id for identifier in unit_ids):
                raise ValueError("写作目标须为本剧本启用场次的唯一ID清单")
            selected = [unit for unit in ordered if unit["id"] in set(unit_ids)]
            if not include_existing and any(_blocks(state, unit["id"]) for unit in selected):
                raise ValueError("已有正文须明确include_existing=true才允许重写")
            if not include_screen_segments and any(unit["kind"] != "scene" for unit in selected):
                raise ValueError("独立画面段须明确纳入任务")
        return {"screenplay_id": screenplay_id, "timeline_id": screenplay["timeline_id"],
                "unit_ids": [unit["id"] for unit in selected],
                "ordered_unit_ids": [unit["id"] for unit in ordered], "include_existing": include_existing,
                "include_screen_segments": include_screen_segments, "fingerprint_version": 1,
                "targets": [{"unit_id": unit["id"], "segment_id": unit["segment_id"], "plan_revision": unit["plan_revision"],
                             "body_revision": unit["body_revision"], "body_presence": "present" if _blocks(state, unit["id"]) else "empty"} for unit in selected]}

    def read_script_writing_targets(self, target_manifest):
        from .screenplay_store import read_context
        _validate_target_manifest(target_manifest)
        state, adaptation = read_context(self)
        screenplay_id = target_manifest["screenplay_id"]
        screenplay = _get(state, "screenplays", screenplay_id)
        if target_manifest.get("timeline_id", screenplay["timeline_id"]) != screenplay["timeline_id"]:
            raise ValueError("批量目标不属于当前剧本时间线")
        current = [unit["id"] for unit in _ordered_units(state, screenplay_id)]
        frozen = list(target_manifest["unit_ids"])
        diagnostics, targets = [], []
        if current != target_manifest["ordered_unit_ids"]:
            diagnostics.append({"code": "WRITING_TARGET_LIST_CHANGED", "message": "场次清单或顺序已变化，任务目标未自动调整"})
        for identifier in frozen:
            if identifier not in state["script_units"] or identifier not in current:
                diagnostics.append({"code": "WRITING_TARGET_UNAVAILABLE", "unit_id": identifier, "message": "目标已删除或屏蔽"})
                continue
            unit = state["script_units"][identifier]
            if state["script_segments"][unit["segment_id"]]["screenplay_id"] != screenplay_id:
                raise ValueError("批量目标不能跨剧本")
            targets.append(writing_unit_view(state, adaptation, identifier))
        return {"target_manifest": copy.deepcopy(target_manifest), "unit_ids": frozen, "targets": targets,
                "diagnostics": diagnostics, "list_changed": current != target_manifest["ordered_unit_ids"]}

    def prepare_script_writing_task(self, target, prompt=""):
        from .screenplay_store import read_context
        state, _adaptation = read_context(self)
        if not isinstance(target, dict) or target.get("kind") not in {"screenplay", "episode", "act", "segment", "scene_table", "unit", "blocks"}:
            raise ValueError("写作任务须显式指定目标kind及ids")
        ids = target.get("ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(identifier, str) for identifier in ids) or len(set(ids)) != len(ids):
            raise ValueError("写作目标ids须为非空唯一ID列表")
        kind = target["kind"]
        manifest = target.get("target_manifest")
        if manifest is not None:
            _validate_target_manifest(manifest)
            if kind != "unit" or ids != manifest["unit_ids"]:
                raise ValueError("批量任务IDs必须与冻结target_manifest清单一致")
        for option in ("include_existing", "include_screen_segments"):
            if option in target and not isinstance(target[option], bool):
                raise ValueError("写作任务选项须为boolean")
        segment_ids, unit_ids, block_ids = [], [], []
        if kind in {"unit", "blocks"}:
            if kind == "blocks":
                block_ids = list(ids)
                unit_ids = list(dict.fromkeys(_get(state, "script_blocks", identifier)["unit_id"] for identifier in ids))
            else:
                unit_ids = list(ids)
            segment_ids = list(dict.fromkeys(_get(state, "script_units", identifier)["segment_id"] for identifier in unit_ids))
        elif kind in {"segment", "scene_table"}:
            segment_ids = list(ids)
        else:
            table = {"screenplay": "screenplays", "episode": "script_episodes", "act": "script_acts"}[kind]
            for identifier in ids:
                obj = _get(state, table, identifier)
                screenplay_id = obj.get("screenplay_id", obj["id"])
                segment_ids += [segment["id"] for segment in _ordered_segments(state, screenplay_id)
                                if kind == "screenplay" or (kind, identifier) in _ancestors(state, segment)]
        if not unit_ids and kind != "scene_table":
            unit_ids = [unit["id"] for segment_id in segment_ids for unit in _units(state, segment_id)
                        if unit.get("enabled", True) and (target.get("include_screen_segments", False) or unit["kind"] == "scene")
                        and (target.get("include_existing", False) or not _blocks(state, unit["id"]))]
        screenplays = list(dict.fromkeys(_get(state, "script_segments", identifier)["screenplay_id"] for identifier in segment_ids))
        if len(screenplays) != 1:
            raise ValueError("一个写作任务必须属于同一剧本")
        if target.get("screenplay_id", screenplays[0]) != screenplays[0]:
            raise ValueError("写作目标ID不属于指定剧本")
        if manifest is not None and manifest["screenplay_id"] != screenplays[0]:
            raise ValueError("批量清单不属于目标剧本")
        screenplay = _get(state, "screenplays", screenplays[0])
        if manifest is not None and manifest.get("timeline_id", screenplay["timeline_id"]) != screenplay["timeline_id"]:
            raise ValueError("批量清单不属于目标剧本时间线")
        result = {"target": copy.deepcopy(target), "screenplay_id": screenplays[0], "segment_ids": segment_ids,
                  "timeline_id": screenplay["timeline_id"], "project_style": copy.deepcopy(_adaptation["_project_style"]),
                  "unit_ids": unit_ids, "block_ids": block_ids, "prompt": str(prompt),
                  "include_existing": manifest["include_existing"] if manifest else bool(target.get("include_existing", False))}
        if manifest is not None:
            result["target_manifest"] = copy.deepcopy(manifest)
        if kind not in {"blocks", "unit", "scene_table"}:
            result["target_manifest"] = self.prepare_script_writing_targets(screenplays[0], unit_ids,
                include_existing=bool(target.get("include_existing", False)), include_screen_segments=bool(target.get("include_screen_segments", False)))
        from .prompt_settings import effective_prompt
        key = "scene_table" if kind == "scene_table" else "writing" if kind in {"unit", "blocks"} else "batch_writing"
        result["instruction"] = effective_prompt(self, key)
        return result


def _validate_target_manifest(manifest):
    if not isinstance(manifest, dict) or not isinstance(manifest.get("screenplay_id"), str):
        raise ValueError("批量目标manifest须含screenplay_id")
    for key in ("unit_ids", "ordered_unit_ids"):
        value = manifest.get(key)
        if not isinstance(value, list) or any(not isinstance(identifier, str) for identifier in value) or len(set(value)) != len(value):
            raise ValueError("批量目标清单须为唯一ID数组")
    if not set(manifest["unit_ids"]) <= set(manifest["ordered_unit_ids"]):
        raise ValueError("批量目标不在冻结剧本顺序中")
    if any(not isinstance(manifest.get(key), bool) for key in ("include_existing", "include_screen_segments")):
        raise ValueError("批量目标选项须为boolean")


def validate_script_writing_tool_call(task, name, args, ops=None):
    """Host guard over an immutable captured target, never mutable UI state."""
    from .tool_api import TOOLS
    tool = TOOLS.get(name)
    if tool is None or not tool.writes:
        return
    if "project_style" in task:
        if ops is None:
            raise ValueError("写作提交须核对工程风格约定修订")
        if ops.load_project_style() != task["project_style"]:
            raise ValueError("项目风格约定已变化，本次写作依据已过期；请按新约定重新开始任务，旧正文未改动")
    segments, units, blocks = set(task["segment_ids"]), set(task["unit_ids"]), set(task["block_ids"])
    kind = task["target"]["kind"]
    if name in {"submit_scene_table", "apply_scene_table_changes", "adopt_scene_table"}:
        if kind in {"blocks", "unit"} or args.get("segment_id") not in segments:
            raise ValueError("写作任务无权修改非目标场表")
        return
    if name in {"submit_script_unit", "apply_script_block_changes"}:
        identifier = args.get("unit_id")
        if task.get("target_manifest") and kind == "unit":
            if ops is None:
                raise ValueError("批量写作提交须校验当前场次")
            from .screenplay_store import read_context
            state, _adaptation = read_context(ops)
            unit = _get(state, "script_units", identifier)
            if not unit.get("enabled", True):
                raise ValueError("冻结目标场已停用，不可继续生成")
            if not task["include_existing"] and _blocks(state, identifier):
                raise ValueError("批量任务未授权重写已有正文")
        if kind not in {"unit", "blocks", "scene_table"} and ops is not None:
            from .screenplay_store import read_context
            state, _adaptation = read_context(ops)
            unit = _get(state, "script_units", identifier)
            if unit["segment_id"] in segments and unit.get("enabled", True):
                if kind in {"screenplay", "episode", "act", "segment"} and not task.get("include_existing") and _blocks(state, identifier):
                    # Already successful targets remain protected from a second overwrite.
                    raise ValueError("批量任务未授权重写已有正文")
                units.add(identifier)
        if identifier not in units or kind == "scene_table":
            raise ValueError("写作任务无权修改非目标场次")
        if kind == "blocks":
            if name == "submit_script_unit" or not set(args.get("target_block_ids", [])) <= blocks:
                raise ValueError("局部正文任务不得覆盖非目标块或整场")
            for action in args.get("changes", []):
                if action.get("op") == "insert" or action.get("block_id") not in blocks:
                    raise ValueError("局部正文任务不得插入或修改非目标块")
        return
    if name == "mark_writing_basis_current":
        if args.get("target_kind") == "unit" and kind in {"screenplay", "episode", "act", "segment"} and ops is not None:
            from .screenplay_store import read_context
            state, _adaptation = read_context(ops)
            unit = _get(state, "script_units", args.get("target_id"))
            if unit["segment_id"] in segments:
                units.add(unit["id"])
        allowed = units if args.get("target_kind") == "unit" else segments if args.get("target_kind") == "segment" and kind != "blocks" else set()
        if args.get("target_id") not in allowed:
            raise ValueError("写作任务无权核对非目标依据")
        return
    raise ValueError("写作任务仅能修改显式剧本目标，不得修改原作、改编或其他对象")
