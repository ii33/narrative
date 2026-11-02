"""General project changes through deterministic validation and atomic acceptance."""
from __future__ import annotations


def preview(ops, graph_objects: list[dict], property_records: list[dict], expected_revision: int) -> dict:
    snapshot = ops.capture_changes()
    return ops.prepare_changes(graph_objects, property_records, expected_revision, snapshot=snapshot).preview()


def apply(ops, graph_objects: list[dict], property_records: list[dict], expected_revision: int, reason: str) -> dict:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("请说明本批修改的reason以保留审计")
    from .conversation_session import record_business_action, use_chat
    with use_chat(ops) as chat:
        snapshot = ops.capture_changes()
        prepared = ops.prepare_changes(graph_objects, property_records, expected_revision, snapshot=snapshot)
        if not prepared.valid:
            return prepared.preview() | {"status": "rejected"}
        result = ops.commit_changes(prepared, reason)
        if result['status'] == 'accepted':
            try:
                record_business_action(ops, {"action": "automatic_changes", "chat_id": chat["id"], "result": result})
            except Exception as exc:
                result['feedback_error'] = str(exc)
        return result


def schema(ops) -> dict:
    from .graph_models import GRAPH_OBJECTS

    return ops.graph_schema() | {"graph_json_schema": GRAPH_OBJECTS.json_schema(),
        "general_changes": {"preview": "preview_changes", "apply": "apply_changes",
            "rules": "提供expected_revision及reason；仅静态校验，合法批次原子入库。所有活动event须属于同语境scene，可同批修复旧孤立事件。"}}


def state(ops) -> dict:
    from .containment_refs import event_scene_issues, storyline_issues
    from .item_possession import item_possession_issues

    graph = ops.graph_snapshot()
    records = ops.property_records()
    diagnostics = (event_scene_issues(graph["objects"]) + storyline_issues(graph["objects"], graph)
                   + item_possession_issues(graph["objects"], records))
    return {"diagnostics": diagnostics, "revision": graph["revision"], "graph": graph,
            "property_records": records}
