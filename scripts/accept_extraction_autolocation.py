"""One real extraction submits quotes for program-owned location and validation."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

TEXT = "周宁在资料室保管铜钥匙。林岚走进资料室，周宁把铜钥匙交给林岚。林岚带着铜钥匙离开资料室。"
TASK = "请读取给定短原文，按当前工程精细粒度完整提取明确的人物、地点、事件、物品及世界事实持有者变化。核对事实语义依据、结构归属与状态有效区间后完成入库；无需创建动作或主要故事。只处理这份原文，报告实际结果与存疑，不修改其他业务对象，不另开任务或重试服务。"


def seed(ops):
    source = ops.import_source("钥匙交接短样本", TEXT)
    script = ops.create_screenplay("仅供验收采集的空容器", reason="共享验收器确定性空容器")["screenplay"]
    return {"source_id": source, "screenplay_id": script["id"], "source_before": ops.source(source)}


def validate(ops, fixture, screenplay, calls, report):
    from narrative.tool_api import TOOLS
    graph, records = ops.graph_snapshot(), ops.property_records()
    nodes = graph["nodes"]
    named = {node["name"]: node for node in nodes}
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    steps = audit["steps"]
    attempts = [step for step in steps if step["tool"] in TOOLS and TOOLS[step["tool"]].writes]
    submissions = [step for step in attempts if step["tool"] == "submit_extraction"]
    accepted = [step for step in submissions if step.get("ok") and step.get("result", {}).get("status") == "accepted"]
    spans = [span for node in nodes for span in node.get("evidence_spans", [])]
    key = named.get("铜钥匙", {}).get("id")
    holders = [row for row in records if row.get("kind") == "property_change" and row.get("node_id") == key and row.get("property_id") == "持有者" and row.get("scope", "world") == "world" and not row.get("holder_id")]
    check = ops.check_project()
    metadata = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(Path(report["audit_directory"]).glob("metadata-*.json"))]
    report["autolocation_evidence"] = {"source_graph": graph, "property_records": records, "attempted_writes": attempts,
        "submission_count": len(submissions), "accepted_submission_count": len(accepted), "locate_calls": [step for step in steps if step["tool"] == "locate_source"],
        "preview_calls": [step for step in steps if step["tool"] == "preview_extraction"], "static_check": check,
        "default_extraction_prompt_snapshot": audit.get("prompt_config", {}).get("texts", {}).get("extraction"),
        "timing": {"tool_program_duration_ms": [{"tool": step["tool"], "ok": step.get("ok"), "duration_ms": step.get("duration_ms")} for step in steps],
            "sdk_turn_duration_seconds": [item.get("duration_seconds") for item in metadata],
            "note": "Tool duration_ms measures the local business call. SDK turn duration includes model/network waits plus tool callbacks and is not individual provider-request duration; the latter is not exposed by current request metadata."},
        "semantic_review_required": "Review the actual submitted facts and evidence against the short source; avoiding locate alone is insufficient."}
    return {"source_read_succeeded": any(step["tool"] == "read_source" and step.get("ok") for step in steps),
        "routine_locate_avoided": not report["autolocation_evidence"]["locate_calls"],
        "routine_preview_avoided": not report["autolocation_evidence"]["preview_calls"],
        "automatic_submission_valid_and_accepted": bool(accepted) and all(step["result"].get("valid") for step in accepted),
        "only_extraction_writes": bool(attempts) and all(step["tool"] == "submit_extraction" for step in attempts),
        "entities_and_events_covered": {"周宁", "林岚", "资料室", "铜钥匙"} <= set(named) and sum(node["node_type"] == "event" for node in nodes) >= 2,
        "no_duplicate_key_or_default_action": sum(node["name"] == "铜钥匙" for node in nodes) == 1 and not any(node["node_type"] == "action" for node in nodes),
        "coordinates_program_located_exactly": bool(nodes) and len(spans) >= len(nodes) and all(span.get("source_id") == fixture["source_id"] and TEXT[span["start"]:span["end"]] == span["quote"] for span in spans),
        "historical_holder_preserved": any(row.get("value") == named.get("周宁", {}).get("id") for row in holders),
        "current_holder_correct": bool(key) and ops.property_state(key, "持有者")["state"] == {"kind": "known", "value": named.get("林岚", {}).get("id")},
        "event_location_supported": any(row.get("kind") == "property_change" and row.get("node_id") in {node["id"] for node in nodes if node["node_type"] == "event"} and row.get("value") == named.get("资料室", {}).get("id") for row in records),
        "static_precheck_no_errors": check["counts"].get("error", 0) == 0,
        "source_text_unchanged": ops.source(fixture["source_id"]) == fixture["source_before"]}


def main():
    qa.seed, qa.TEXT, qa.TASK = seed, TEXT, TASK
    qa.PROMPT_VERSION = "direct-extraction-program-autolocation/1"
    qa.FIXTURE_ORIGIN = "Operations imports only a short source with repeated entity mentions and a holder transfer; real model authors the complete extraction through submit_extraction"
    return qa.main(validate=validate, required_tools=["read_source", "submit_extraction"],
        prompt_factory=lambda fixture: TASK + "\nsource_id=" + fixture["source_id"], use_writing_target=False, allow_extraction_change=True)


if __name__ == "__main__":
    raise SystemExit(main())
