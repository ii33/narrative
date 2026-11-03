"""Operations for screenplay containers, inclusive spans, structure and export."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Literal

from .graph_store import GraphStore
from .screenplay_commands import ACTION_ADAPTER
from .screenplay_models import AdaptationSpan, Screenplay, ScriptSegment
from .screenplay_spans import resolve_span, span_endpoints
from .screenplay_store import (
    expect_revision,
    now,
    object_fields,
    read_context,
    validate_state,
    write_transaction,
)
from .screenplay_structure import (
    KIND_TABLE,
    add_entry,
    child_entries,
    create_episode,
    default_parent,
    entry_for,
    move_entry,
    ordered_segment_ids,
    require,
    tree,
)
from .screenplay_timeline import new_script_timeline, screenplay_timeline, timeline_owner


def _require_span(state, identifier):
    row = state["adaptation_spans"].get(identifier)
    if not row:
        raise ValueError("改编时间段不存在")
    return row


def _segment_view(state, adaptation, identifier):
    from .screenplay_writing import writing_segment_view
    view = writing_segment_view(state, adaptation, identifier)
    span = _span_result(state, adaptation, _require_span(state, view["span_id"]))
    return view | {"span": span, "diagnostics": span["diagnostics"]}


def _screenplay_view(state, adaptation, identifier):
    screenplay = require(state, "screenplay", identifier)
    local_timeline = screenplay_timeline(state, adaptation, identifier)
    order = ordered_segment_ids(state, identifier)
    segments = [_segment_view(state, adaptation, key) for key in order]
    return {"screenplay": copy.deepcopy(screenplay), "timeline_id": local_timeline["timeline_id"],
            "timeline_revision": local_timeline["revision"], "timeline": local_timeline,
            "episodes": [copy.deepcopy(e) for e in state["script_episodes"].values() if e["screenplay_id"] == identifier],
            "acts": [copy.deepcopy(a) for a in state["script_acts"].values() if a["screenplay_id"] == identifier],
            "structure_entries": [copy.deepcopy(e) for e in state["script_structure_entries"].values() if e["screenplay_id"] == identifier],
            "structure_tree": copy.deepcopy(tree(state, identifier)), "segment_order": order, "segments": segments,
            "diagnostics": [d for s in segments for d in s.get("diagnostics", [])]}


def _span_result(state, adaptation, span, selected=None):
    adaptation = screenplay_timeline(state, adaptation, timeline_owner(state, span["timeline_id"]))
    result = resolve_span(adaptation, span)
    result["affected_segment_ids"] = [s["id"] for s in state["script_segments"].values() if s["span_id"] == span["id"]]
    members = set(result["member_node_ids"])
    if selected is not None:
        extra = members - set(selected)
        if extra:
            result["diagnostics"].append({"severity": "warning", "code": "SPAN_INTERLEAVED", "message": "连续选区包含交错或未单独选中的时间点", "node_ids": [key for key in result["member_node_ids"] if key in extra]})
    for other in state["adaptation_spans"].values():
        if other["id"] != span["id"] and other["timeline_id"] == span["timeline_id"] and members & set(resolve_span(adaptation, other)["member_node_ids"]):
            result["diagnostics"].append({"severity": "warning", "code": "SPAN_OVERLAP", "message": "时间段范围重叠，可用于有意的重复叙述", "span_id": other["id"]})
    return result


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + "不能为空")
    return value.strip()


def _update(row, changes):
    row.update(changes)
    row["revision"] += 1
    row["updated_at"] = now()


def _check_snapshot(state, adaptation, screenplay_id):
    require(state, "screenplay", screenplay_id)
    issues = []
    try:
        validate_state(state, adaptation)
    except ValueError as exc:
        issues.append({"severity": "error", "code": "SCREENPLAY_STRUCTURE", "message": str(exc)})
    for identifier in ordered_segment_ids(state, screenplay_id):
        segment = state["script_segments"][identifier]
        span = _span_result(state, adaptation, _require_span(state, segment["span_id"]))
        issues.extend({**d, "segment_id": identifier} for d in span["diagnostics"])
        view = _segment_view(state, adaptation, identifier)
        issues.extend({**d, "segment_id": identifier} for d in view.get("scene_partition_diagnostics", []))
        if view.get("outline_basis_state") == "changed":
            issues.append({"severity": "warning", "code": "OUTLINE_CHANGED", "message": "场表依据已变化，正文保留", "segment_id": identifier})
        estimated = sum(unit["plan"].get("estimated_duration_seconds") or 0
                        for unit in view.get("units", []) if unit["enabled"])
        if segment.get("target_duration_seconds") and estimated > segment["target_duration_seconds"]:
            issues.append({"severity": "warning", "code": "SEGMENT_DURATION_EXCEEDED", "message": "场表估计时长超过段落目标时长", "segment_id": identifier})
        for unit in view.get("units", []):
            issues.extend({**d, "unit_id": unit["id"]} for d in unit.get("diagnostics", []))
            if unit.get("basis_state") in {"changed", "invalid", "empty_source"}:
                issues.append({"severity": "warning", "code": "BODY_BASIS_" + unit["basis_state"].upper(),
                               "message": "正文依据需复查，保留当前正文", "unit_id": unit["id"]})
    return {"ok": not any(i["severity"] == "error" for i in issues), "screenplay_id": screenplay_id, "issues": issues}



class ScreenplayOperationsMixin:
    _store: GraphStore

    def inspect_screenplay_storage(self) -> dict:
        from .screenplay_store import inspect_screenplay_storage
        return inspect_screenplay_storage(self)

    def reset_screenplay_storage(self, reason, scope, expected_storage_fingerprint, confirm_discard) -> dict:
        from .screenplay_store import reset_screenplay_storage
        return reset_screenplay_storage(self, reason, scope, expected_storage_fingerprint, confirm_discard)

    def preview_adaptation_span(self, screenplay_id, selection_ids=None, start_node_id=None, end_node_id=None) -> dict:
        state, source = read_context(self)
        adaptation = screenplay_timeline(state, source, screenplay_id)
        start, end, selected = span_endpoints(adaptation, selection_ids, start_node_id, end_node_id)
        span = AdaptationSpan(**object_fields("preview"), timeline_id=adaptation["timeline_id"], name="预览时间段",
                              start_node_id=start, end_node_id=end).model_dump(mode="json")
        return _span_result(state, adaptation, span, selected)

    def create_adaptation_span(self, screenplay_id, name, start_node_id=None, end_node_id=None, selection_ids=None,
                               expected_timeline_revision=None, reason=None) -> dict:
        def mutate(state, source, _db):
            adaptation = screenplay_timeline(state, source, screenplay_id)
            expect_revision(adaptation, expected_timeline_revision)
            start, end, selected = span_endpoints(adaptation, selection_ids, start_node_id, end_node_id)
            span = AdaptationSpan(**object_fields("span"), timeline_id=adaptation["timeline_id"], name=_text(name, "时间段名称"),
                                  start_node_id=start, end_node_id=end).model_dump(mode="json")
            result = _span_result(state, adaptation, span, selected)
            if result["status"] == "invalid":
                raise ValueError(result["diagnostics"][0]["message"])
            state["adaptation_spans"][span["id"]] = span
            return result
        return write_transaction(self, reason, mutate)

    def read_adaptation_span(self, span_id) -> dict:
        state, adaptation = read_context(self)
        return _span_result(state, adaptation, _require_span(state, span_id))

    def update_adaptation_span(self, span_id, changes, expected_revision, expected_timeline_revision,
                               reason) -> dict:
        if not isinstance(changes, dict) or set(changes) - {"name", "start_node_id", "end_node_id", "selection_ids"}:
            raise ValueError("时间段只允许改名或重设边界")
        def mutate(state, source, _db):
            span = _require_span(state, span_id)
            adaptation = screenplay_timeline(state, source, timeline_owner(state, span["timeline_id"]))
            expect_revision(span, expected_revision)
            expect_revision(adaptation, expected_timeline_revision)
            patch = copy.deepcopy(changes)
            selected = None
            if "selection_ids" in patch:
                start, end, selected = span_endpoints(adaptation, patch.pop("selection_ids"), patch.get("start_node_id"), patch.get("end_node_id"))
                patch.update(start_node_id=start, end_node_id=end)
            if "name" in patch:
                patch["name"] = _text(patch["name"], "时间段名称")
            _update(span, patch)
            result = _span_result(state, adaptation, span, selected)
            if set(changes) - {"name"} and result["status"] == "invalid":
                raise ValueError(result["diagnostics"][0]["message"])
            return result
        return write_transaction(self, reason, mutate)

    def list_screenplays(self) -> list[dict]:
        state, _adaptation = read_context(self)
        return list(copy.deepcopy(state["screenplays"]).values())

    def create_screenplay(self, title, format: Literal["single", "series"] = "single", language="zh-CN", creative_brief=None, reason=None) -> dict:
        def mutate(state, adaptation, _db):
            fields = object_fields("screenplay")
            local = new_script_timeline(fields["id"], adaptation)
            screenplay = Screenplay(**fields, timeline_id=local["id"], title=_text(title, "剧本名称"),
                                    format=format, language=language, creative_brief={} if creative_brief is None else creative_brief).model_dump(mode="json")
            state["screenplays"][screenplay["id"]] = screenplay
            state["script_timelines"][local["id"]] = local
            if format == "series":
                create_episode(state, screenplay["id"], "第1集")
            return _screenplay_view(state, adaptation, screenplay["id"])
        return write_transaction(self, reason, mutate, initialize=True)

    def read_screenplay(self, screenplay_id=None):
        state, adaptation = read_context(self)
        if screenplay_id is None:
            return list(copy.deepcopy(state["screenplays"]).values())
        return _screenplay_view(state, adaptation, screenplay_id)

    def create_script_segment(self, screenplay_id, span_id, title, expected_structure_revision, reason,
                              parent_kind=None, parent_id=None, after_entry_id=None, role: Literal["normal", "prologue", "epilogue"] = "normal", brief=None,
                              target_duration_seconds=None) -> dict:
        def mutate(state, adaptation, _db):
            screenplay = require(state, "screenplay", screenplay_id)
            expect_revision(screenplay, expected_structure_revision, "structure_revision")
            span = _require_span(state, span_id)
            if span["timeline_id"] != screenplay["timeline_id"]:
                raise ValueError("段落不可跨改编时间线绑定")
            segment = ScriptSegment(**object_fields("segment"), screenplay_id=screenplay_id, span_id=span_id,
                                    title=_text(title, "段落标题"), role=role, brief={} if brief is None else brief,
                                    target_duration_seconds=target_duration_seconds).model_dump(mode="json")
            state["script_segments"][segment["id"]] = segment
            if (parent_kind is None) != (parent_id is None):
                raise ValueError("段落归属须同时指定父级类型与ID")
            kind, identifier = (parent_kind, parent_id) if parent_kind else default_parent(state, screenplay)
            add_entry(state, screenplay_id, kind, identifier, "segment", segment["id"], after_entry_id)
            screenplay["structure_revision"] += 1
            screenplay["updated_at"] = now()
            return _segment_view(state, adaptation, segment["id"])
        return write_transaction(self, reason, mutate)

    def update_script_segment(self, segment_id, changes, expected_revision, reason) -> dict:
        if not isinstance(changes, dict) or set(changes) - {"title", "span_id", "role", "brief", "target_duration_seconds", "enabled"}:
            raise ValueError("段落仅允许修改标题、绑定、角色、要求、时长和启用状态")
        def mutate(state, adaptation, _db):
            segment = require(state, "segment", segment_id)
            expect_revision(segment, expected_revision)
            _update(segment, copy.deepcopy(changes))
            return _segment_view(state, adaptation, segment_id)
        return write_transaction(self, reason, mutate)

    def read_script_segment(self, segment_id) -> dict:
        state, adaptation = read_context(self)
        require(state, "segment", segment_id)
        return _segment_view(state, adaptation, segment_id)

    def apply_screenplay_structure(self, screenplay_id, actions, expected_structure_revision, reason, format=None) -> dict:
        parsed = ACTION_ADAPTER.validate_python(actions)
        def mutate(state, adaptation, _db):
            screenplay = require(state, "screenplay", screenplay_id)
            expect_revision(screenplay, expected_structure_revision, "structure_revision")
            for command in parsed:
                action = command.model_dump(exclude_unset=True)
                op = action["op"]
                if op == "create_episode":
                    create_episode(state, screenplay_id, action["title"], action.get("number", "1"), action.get("brief"),
                                   action.get("target_duration_seconds"), action.get("id"), action.get("after_entry_id"))
                elif op == "create_act":
                    episode_id = action.get("episode_id")
                    if action.get("id") and action["id"] in state["script_acts"]:
                        raise ValueError("幕ID已存在")
                    act = {**object_fields("act", action.get("id") or None), "screenplay_id": screenplay_id, "episode_id": episode_id,
                           "title": action["title"], "brief": action.get("brief", {}), "target_duration_seconds": action.get("target_duration_seconds")}
                    state["script_acts"][act["id"]] = act
                    add_entry(state, screenplay_id, "episode" if episode_id else "screenplay", episode_id or screenplay_id,
                              "act", act["id"], action.get("after_entry_id"))
                elif op in {"update_episode", "update_act", "update_screenplay"}:
                    kind = op.removeprefix("update_")
                    row = require(state, kind, screenplay_id if kind == "screenplay" else action[kind + "_id"])
                    if kind != "screenplay" and row["screenplay_id"] != screenplay_id:
                        raise ValueError("不能修改其他剧本对象")
                    expect_revision(row, action["expected_revision"])
                    _update(row, action["changes"])
                elif op == "move":
                    entry = entry_for(state, action["child_kind"], action["child_id"])
                    if entry["screenplay_id"] != screenplay_id:
                        raise ValueError("不能移动其他剧本对象")
                    move_entry(state, entry, action["parent_kind"], action["parent_id"], action.get("after_entry_id"))
                elif op == "delete_segment":
                    segment = require(state, "segment", action["segment_id"])
                    if segment["screenplay_id"] != screenplay_id:
                        raise ValueError("不能删除其他剧本段落")
                    expect_revision(segment, action["expected_revision"])
                    units = {u["id"] for u in state["script_units"].values() if u["segment_id"] == segment["id"]}
                    blocks = {b["id"] for b in state["script_blocks"].values() if b["unit_id"] in units}
                    if units and action["body_policy"] == "require_empty":
                        raise ValueError("段落已有场次，须显式discard处理场表与正文")
                    for table, ids in (("script_units", units), ("script_blocks", blocks)):
                        for identifier in ids:
                            del state[table][identifier]
                    for identifier, usage in list(state["script_entity_usages"].items()):
                        if usage["unit_id"] in units:
                            del state["script_entity_usages"][identifier]
                    for identifier, basis in list(state["script_writing_bases"].items()):
                        if (basis["target_kind"] == "segment" and basis["target_id"] == segment["id"]) or (basis["target_kind"] == "unit" and basis["target_id"] in units):
                            del state["script_writing_bases"][identifier]
                    entry = entry_for(state, "segment", segment["id"])
                    del state["script_structure_entries"][entry["id"]]
                    del state["script_segments"][segment["id"]]
                elif op in {"delete_episode", "delete_act"}:
                    kind = op.removeprefix("delete_")
                    row = require(state, kind, action[kind + "_id"])
                    if row["screenplay_id"] != screenplay_id:
                        raise ValueError("不能删除其他剧本对象")
                    expect_revision(row, action["expected_revision"])
                    children = child_entries(state, kind, row["id"])
                    target = action.get("move_to")
                    if children and not target:
                        raise ValueError("删除非空集／幕必须显式指定搬移目标")
                    if target and target["parent_kind"] == kind and target["parent_id"] == row["id"]:
                        raise ValueError("搬移目标不能是待删除对象自身")
                    if target:
                        destination = child_entries(state, target["parent_kind"], target["parent_id"])
                        previous = destination[-1]["id"] if destination else None
                        for entry in children:
                            move_entry(state, entry, target["parent_kind"], target["parent_id"], previous)
                            previous = entry["id"]
                    entry = entry_for(state, kind, row["id"])
                    del state["script_structure_entries"][entry["id"]]
                    del state[KIND_TABLE[kind]][row["id"]]
            if format is not None:
                screenplay["format"] = format
                screenplay["revision"] += 1
            screenplay["structure_revision"] += 1
            screenplay["updated_at"] = now()
            # Validate before constructing a recursively traversed read projection.
            validate_state(state, adaptation)
            return _screenplay_view(state, adaptation, screenplay_id)
        return write_transaction(self, reason, mutate)

    def check_screenplay(self, screenplay_id) -> dict:
        state, adaptation = read_context(self)
        return _check_snapshot(state, adaptation, screenplay_id)

    def export_screenplay(self, screenplay_id, format="text", path=None) -> dict:
        if format not in {"text", "json"}:
            raise ValueError("导出格式仅支持text或json")
        state, adaptation = read_context(self)
        document = _screenplay_view(state, adaptation, screenplay_id)
        local_timeline = screenplay_timeline(state, adaptation, screenplay_id)
        diagnostics = _check_snapshot(state, adaptation, screenplay_id)["issues"]
        document["diagnostics"] = diagnostics
        lines = [document["screenplay"]["title"]]
        if diagnostics:
            lines += ["", "【范围与依据提示】"] + [d["message"] for d in diagnostics]
        units_by_segment = {s["id"]: s.get("units", []) for s in document["segments"]}
        def emit(parent_kind, parent_id, enabled=True):
            for entry in child_entries(state, parent_kind, parent_id):
                kind, identifier = entry["child_kind"], entry["child_id"]
                obj = require(state, kind, identifier)
                if kind in {"episode", "act"}:
                    prefix = "第" + obj["number"] + "集" if kind == "episode" else ""
                    heading = obj["title"] if not prefix or obj["title"].startswith(prefix) else prefix + " " + obj["title"]
                    lines.extend(["", heading])
                    emit(kind, identifier, enabled)
                elif enabled and obj["enabled"]:
                    lines.extend(["", "【" + obj["title"] + "】"])
                    for unit in units_by_segment[identifier]:
                        if not unit["enabled"]:
                            continue
                        plan = unit["plan"]
                        heading = " ".join(str(x) for x in (unit.get("scene_number"), plan.get("interior_exterior"),
                                          plan.get("location_text"), plan.get("time_label")) if x and x != "UNSPECIFIED")
                        lines.extend(["", heading or plan.get("title") or "画面段"])
                        for block in unit.get("blocks", []):
                            payload = block["payload"]
                            text = payload.get("text", "")
                            if block["type"] == "dialogue":
                                speaker = payload.get("speaker_label") or next((n["name"] for n in local_timeline["nodes"] if n["id"] == payload.get("speaker_id")), "未绑定人物")
                                delivery = {"off_screen": "画外", "voice_over": "旁白", "inner_voice": "内心声"}.get(payload.get("delivery"), "")
                                lines.append(speaker + ("（" + delivery + "）" if delivery else ""))
                                if payload.get("parenthetical"):
                                    lines.append("（" + payload["parenthetical"] + "）")
                            elif block["type"] == "screen_text":
                                text = "【字幕】" + text
                            elif block["type"] == "sound":
                                text = "【声音】" + text
                            elif block["type"] == "transition":
                                text = "【转场】" + (text or payload.get("kind", ""))
                            lines.append(text)
        emit("screenplay", screenplay_id)
        text = json.dumps(document, ensure_ascii=False, indent=2) if format == "json" else "\n".join(lines) + "\n"
        result = {"format": format, "text": text, "document": document, "diagnostics": diagnostics}
        if path is not None:
            target = Path(path).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            result["path"] = str(target)
        return result
