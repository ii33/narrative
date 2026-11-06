"""Evidence-backed physical event merge, with typed references rewritten atomically."""
from __future__ import annotations

import json
from copy import deepcopy

from .graph_models import NodeEvidenceSpan
from .node_identity import graph_references


def _spans(node: dict) -> list[dict]:
    result = list(node.get("evidence_spans") or [])
    primary, disclosure = node.get("evidence") or {}, node.get("disclosure") or {}
    if primary.get("quote") and type(disclosure.get("start")) is int:
        result.append({"source_id": primary["source_id"], "start": disclosure["start"],
                       "end": disclosure["start"] + len(primary["quote"]), "quote": primary["quote"]})
    return list({(row["source_id"], row["start"], row["end"], row["quote"]): row for row in result}.values())


def _rewrite_records(records: list[dict], source_id: str, target_id: str) -> list[dict]:
    """Rewrite declared node references, never opaque values or source coordinates."""
    mapping = {source_id: target_id}
    definitions = {row["id"]: row for row in records if row.get("kind") == "property_definition"}
    result = []
    for record in records:
        row = deepcopy(record)
        for field in ("node_id", "holder_id", "event_id", "related_nodes"):
            if field in row:
                row[field] = graph_references(row[field], mapping, field)
        definition = definitions.get(row.get("property_id"), {})
        if row.get("kind") == "property_change" and definition.get("value_type") in {"node", "either"}:
            value = row.get("value")
            if isinstance(value, str):
                row["value"] = mapping.get(value, value)
            elif isinstance(value, list):
                row["value"] = [mapping.get(v, v) if isinstance(v, str) else v for v in value]
        result.append(row)
    return result


def _plan(ops, source_event_id: str, target_event_id: str, expected_revision: int,
          evidence: list[dict], reason: str) -> tuple[dict, dict, list[dict]]:
    if type(expected_revision) is not int:
        raise ValueError("expected_revision须为整数")
    snapshot = ops.graph_snapshot()
    if expected_revision != snapshot["revision"]:
        raise ValueError("工程修订已变化，请刷新后重新核对")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("合并须说明基于原文的身份判断原因")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("合并须提供覆盖两事件既有证据的原文跨度")
    proof = [NodeEvidenceSpan.model_validate(row).model_dump() for row in evidence]
    for span in proof:
        text = ops.source(span["source_id"])["text"]
        if text[span["start"]:span["end"]] != span["quote"]:
            raise ValueError("合并证据跨度与原文不符")
    nodes = {node["id"]: node for node in snapshot["nodes"]}
    source, target = nodes.get(source_event_id), nodes.get(target_event_id)
    if source is None or target is None or source_event_id == target_event_id:
        raise ValueError("须指定本工程两个不同的事件节点")
    if source.get("node_type") != "event" or target.get("node_type") != "event":
        raise ValueError("仅支持事件合并")
    if source.get("retracted") or target.get("retracted"):
        raise ValueError("仅能合并当前有效事件")
    if source.get("context_id", "canon") != target.get("context_id", "canon"):
        raise ValueError("不同语境的事件不能合并")
    if source.get("occurrence") != target.get("occurrence"):
        raise ValueError("事件发生时间信息不同，不能合并")
    left_position, right_position = source.get("disclosure") or {}, target.get("disclosure") or {}
    if (not left_position.get("source_id") or not right_position.get("source_id")
            or type(left_position.get("start")) is not int or type(right_position.get("start")) is not int):
        raise ValueError("请先核对双方事件的精确首披露位置")
    if left_position["source_id"] != right_position["source_id"]:
        orders = {row["source_id"]: row["ordinal"] for row in snapshot.get("source_orders") or []}
        if left_position["source_id"] not in orders or right_position["source_id"] not in orders:
            raise ValueError("跨原文事件缺少可核对的披露顺序，不能确定canonical主锚")
        if orders[left_position["source_id"]] < orders[right_position["source_id"]]:
            raise ValueError("请以更早披露的事件为target_event_id，避免合并后延迟首次披露")
    if (left_position.get("source_id") == right_position.get("source_id")
            and type(left_position.get("start")) is int and type(right_position.get("start")) is int
            and left_position["start"] < right_position["start"]):
        raise ValueError("请以更早披露的事件为target_event_id，避免合并后延迟首次披露")
    left_spans, right_spans = _spans(source), _spans(target)
    for spans in (left_spans, right_spans):
        if not spans or not any(p["source_id"] == old["source_id"] and p["start"] <= old["start"]
                                and p["end"] >= old["end"] for p in proof for old in spans):
            raise ValueError("合并证据须覆盖两个事件各自已记录的原文证据")
    if not {s["source_id"] for s in left_spans}.intersection(s["source_id"] for s in right_spans):
        raise ValueError("两事件须有同一原文来源可核对")
    def parents(event):
        return {n["id"] for n in snapshot["nodes"] if n.get("node_type") == "scene" and event in (n.get("events") or [])}
    if parents(source_event_id) and parents(target_event_id) and parents(source_event_id) != parents(target_event_id):
        raise ValueError("事件所属场景不同，请先核对包含结构")
    direct = {source_event_id, target_event_id}
    for node in (source, target):
        if any(direct.intersection(node.get(field) or []) for field in ("predecessors", "successors", "events")):
            raise ValueError("两个事件存在先后或包含断言，合并会造成自环")
    merged = dict(target)
    for field in ("participants", "predecessors", "successors", "events", "aliases"):
        merged[field] = list(dict.fromkeys([*(target.get(field) or []), *(source.get(field) or [])]))
    if source["name"] != target["name"] and source["name"] not in merged["aliases"]:
        merged["aliases"].append(source["name"])
    merged["source_marks"] = list({json.dumps(mark, sort_keys=True, ensure_ascii=False): mark
                                    for mark in [*(target.get("source_marks") or []), *(source.get("source_marks") or [])]}.values())
    merged["evidence_spans"] = list({(row["source_id"], row["start"], row["end"], row["quote"]): row
                                     for row in right_spans + left_spans + proof}.values())
    mapping = {source_event_id: target_event_id}
    projected = [graph_references(merged if obj["id"] == target_event_id else obj, mapping)
                 for obj in snapshot["objects"] if obj["id"] != source_event_id]
    active = {node["id"]: node for node in projected if node.get("kind") == "node" and not node.get("retracted")}
    for field in ("events", "successors", "predecessors"):
        colors = {}
        for key in active:
            stack = [(key, False)]
            while stack:
                here, leaving = stack.pop()
                if leaving:
                    colors[here] = 2
                    continue
                color = colors.get(here, 0)
                if color == 1:
                    raise ValueError("合并后的事件引用形成循环")
                if color == 2:
                    continue
                colors[here] = 1
                stack.append((here, True))
                stack.extend((child, False) for child in active.get(here, {}).get(field) or [])
    from .temporal_state import diagnostics as state_diagnostics

    future = snapshot | {"objects": projected,
                         "nodes": [obj for obj in projected if obj.get("kind") == "node"]}
    records = _rewrite_records(ops.property_records(), source_event_id, target_event_id)
    diagnostics = state_diagnostics(ops.project.store, future, records)
    valid = not any(row.get("severity") == "error" for row in diagnostics)
    return {"valid": valid, "status": "preview", "revision": expected_revision,
            "source_event_id": source_event_id, "target_event_id": target_event_id,
            "diagnostics": diagnostics, "evidence": proof, "reason": reason.strip(),
            "physical_merge": True}, future, records


def preview(ops, source_event_id: str, target_event_id: str, expected_revision: int,
            evidence: list[dict], reason: str) -> dict:
    return _plan(ops, source_event_id, target_event_id, expected_revision, evidence, reason)[0]


def merge(ops, source_event_id: str, target_event_id: str, expected_revision: int,
          evidence: list[dict], reason: str) -> dict:
    from .conversation_session import current_chat, record_business_action, use_chat

    with use_chat(ops):
        from .temporal_state import persist

        result, snapshot, records = _plan(ops, source_event_id, target_event_id, expected_revision, evidence, reason)
        if not result["valid"]:
            return result
        revision = persist(ops.project.store, snapshot, records, expected_revision=expected_revision,
                           action="event_merge", reason=reason.strip())
        result.update(status="accepted", revision=revision, chat_id=current_chat(ops)["id"])
        try:
            record_business_action(ops, {"action": "event_merge", "chat_id": result["chat_id"], "result": result})
        except Exception as exc:
            result["feedback_error"] = {"type": type(exc).__name__, "message": str(exc)}
        return result
