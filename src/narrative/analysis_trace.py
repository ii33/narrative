"""Project recorded agent actions into a compact, user-readable process trace."""

from __future__ import annotations


def _clip(value: object, limit: int = 180) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _tool_summary(observation: dict, name: str | None = None) -> dict:
    if "error" in observation:
        return {"error": _clip(observation["error"], 500), "summary": "工具返回错误"}
    result = observation.get("result")
    if name == "extraction_observation" and isinstance(result, dict):
        diagnostics = [
            {key: _clip(item.get(key, ""), 240) for key in ("path", "code", "message")}
            for item in result.get("diagnostics", [])[:5]
            if isinstance(item, dict)
        ]
        entity_types = [
            _clip(item.get("entity_type", ""), 80)
            for item in result.get("entities", [])[:10]
            if isinstance(item, dict)
        ]
        return {
            "summary": f"旧提取观察：{result.get('raw_characters', 0)} 字原始响应、{len(result.get('diagnostics', []))} 条诊断",
            "diagnostics": diagnostics,
            "entity_types": entity_types,
            "raw_sha256": result.get("raw_sha256"),
        }
    if isinstance(result, list):
        matches = []
        for item in result[:5]:
            if not isinstance(item, dict):
                continue
            if "handle" in item:
                matches.append({"handle": item["handle"], "quote": _clip(item.get("quote", ""))})
            elif "matches" in item:
                matches.extend(
                    {"handle": match.get("handle"), "quote": _clip(match.get("quote", ""))}
                    for match in item.get("matches", [])[:3]
                    if isinstance(match, dict)
                )
        return {"summary": f"返回 {len(result)} 项", "matches": matches[:10]}
    if isinstance(result, dict):
        if "text" in result:
            return {"summary": f"原文摘录 {len(result['text'])} 字", "preview": _clip(result["text"])}
        if result.get("truncated"):
            return {"summary": "结果过长，已截短", "preview": _clip(result.get("preview", ""))}
        counts = {key: len(value) for key, value in result.items() if isinstance(value, list)}
        return {"summary": "返回结构化资料", "counts": counts}
    return {"summary": _clip(result)}


def project(state: dict, control: dict, display_map: dict) -> dict:
    labels = display_map.get("items", {})
    workspace = state.get("workspace", {})
    config = state.get("config") or {}
    budget = state.get("budget") or {}
    requested_limit = budget.get("max_tokens_per_round")
    output_mode = (
        requested_limit if requested_limit in {"inherit", "service-default"}
        else "override" if type(requested_limit) is int else "unknown"
    )
    output_value = config.get("max_tokens")
    output_sent = output_value is not None if "max_tokens" in config else None

    def label(object_id: str | None, seen: frozenset[str] = frozenset()) -> str:
        if not object_id:
            return "未指定"
        if object_id in seen:
            return "关联对象"
        item = workspace.get(object_id) or {}
        if item:
            nested = seen | {object_id}
            kind = item.get("kind")
            if kind == "relation":
                return f"{label(item.get('subject_id'), nested)} → {item.get('predicate', '关系')} → {label(item.get('object_id'), nested)}"
            if kind == "event_link":
                return f"{label(item.get('source_event_id'), nested)} → {item.get('link_type', '关联')} → {label(item.get('target_event_id'), nested)}"
            if kind == "classification":
                return f"{label(item.get('target_id'), nested)} · {item.get('category', '分类')}"
            if kind == "storyline":
                return f"故事线：{item.get('title', '未命名')}"
            return str(item.get("name") or item.get("title") or item.get("category") or item.get("predicate") or "未命名对象")
        return labels.get(object_id, {}).get("label", "未找到的对象")

    def diagnostic(item: dict) -> dict:
        return {**item, "object_label": label(item["object_id"])} if item.get("object_id") else item

    steps = []
    for step in state.get("steps", []):
        finish = step.get("finish_reason")
        if finish:
            request_state = "completed" if finish == "completed" else "stopped"
        elif state.get("status") in {"queued", "running"}:
            request_state = step.get("request_state") or "in_flight"
        else:
            request_state = "stopped"
        tools = []
        for action in step.get("tools", []):
            args = action.get("args") or {}
            display_args = {
                key: label(value) if key.endswith("_id") and isinstance(value, str)
                else [label(item) for item in value] if key.endswith("_ids") and isinstance(value, list)
                else _clip(value)
                for key, value in args.items()
            } if isinstance(args, dict) else {"value": _clip(args)}
            tools.append({
                "name": action.get("name"),
                "args": display_args,
                "result": _tool_summary(action.get("observation") or {}, action.get("name")),
            })
        diagnostics = [
            diagnostic(item)
            for key in ("action_diagnostics", "stage_diagnostics", "diagnostics")
            for item in (step.get(key) or [])
            if isinstance(item, dict)
        ]
        steps.append({
            "round": step.get("round"),
            "phase": step.get("phase"),
            "request_state": request_state,
            "plan": step.get("plan", ""),
            "input_hash": step.get("input_hash"),
            "finish_reason": finish,
            "usage": step.get("usage"),
            "duration_seconds": step.get("duration_seconds"),
            "service_error": step.get("service_error"),
            "raw_available": bool(step.get("raw")),
            "raw_length": len(str(step.get("raw") or "")),
            "tools": tools,
            "staged": [{"id": object_id, "label": label(object_id)} for object_id in step.get("stage_ids", []) if isinstance(object_id, str)],
            "removed": [{"id": object_id, "label": label(object_id)} for object_id in step.get("removed_stage_ids", []) if isinstance(object_id, str)],
            "diagnostics": diagnostics,
            "candidate_id": step.get("candidate_id"),
            "candidate_label": label(step.get("candidate_id")) if step.get("candidate_id") else None,
            "instruction_ids": step.get("instruction_ids", []),
        })
    last = steps[-1] if steps else None
    return {
        "run_id": state.get("id"),
        "status": state.get("status"),
        "observer_state": state.get("observer_state", "recorded_terminal" if state.get("status") not in {"queued", "running"} else "unverified_external"),
        "worker_local": state.get("worker_local"),
        "phase": state.get("phase"),
        "request_state": state.get("request_state") or (last["request_state"] if last else "not_started"),
        "provider": state.get("provider"),
        "model": state.get("model"),
        "prompt_version": state.get("prompt_version"),
        "output_limit_mode": output_mode,
        "output_token_parameter": config.get("token_parameter"),
        "output_token_value": output_value,
        "output_parameter_sent": output_sent,
        "frozen_service": {
            "provider": state.get("provider"),
            "model": state.get("model"),
            "token_parameter": config.get("token_parameter"),
            "max_tokens": output_value,
            "output_limit_mode": output_mode,
            "output_parameter_sent": output_sent,
        },
        "source_ids": state.get("source_ids", []),
        "source_labels": [label(source_id) for source_id in state.get("source_ids", [])],
        "rounds": state.get("rounds", 0),
        "tool_count": state.get("tool_count", 0),
        "usage": state.get("usage", {}),
        "task": state.get("task"),
        "feedback": state.get("feedback"),
        "feedback_origin": state.get("feedback_origin"),
        "feedback_hash": state.get("lineage", {}).get("feedback_hash"),
        "coverage": state.get("coverage", {}),
        "lineage": state.get("lineage", {}),
        "control": control,
        "workspace": [{"id": object_id, "kind": item.get("kind"), "label": label(object_id)} for object_id, item in workspace.items()],
        "precheck_diagnostics": [diagnostic(item) for item in state.get("precheck_diagnostics", []) if isinstance(item, dict)],
        "candidate_ids": state.get("candidate_ids", []),
        "candidate_labels": [label(candidate_id) for candidate_id in state.get("candidate_ids", [])],
        "steps": steps,
        "error": state.get("error"),
        "notice": "仅展示已记录的模型响应与工具动作；不展示或推断模型内部思考。",
    }
