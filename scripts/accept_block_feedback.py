"""One real fixed-block feedback edit; untouched adjacent text is immutable."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_screenplay_compile import seed as consistent_seed

TASK = "请依据意见润色所选第1块既定交钥匙动作，让手部动作更清楚、表达更简洁，不增加剧情。只改所选块，保持夜晚旧车站及铜钥匙交接，不改其他正文、类型或块ID。先读取当前上下文，使用apply_script_block_changes保存，完成即简述。"


def seed(ops):
    fixture = consistent_seed(ops)
    c = ops.read_script_unit_context(fixture["unit_id"])
    written = ops.submit_script_unit(fixture["unit_id"], [
        {"type": "action", "payload": {"text": "夜，旧车站门口。周川把铜钥匙交给林岚。"}},
        {"type": "action", "payload": {"text": "林岚握住铜钥匙。"}},
    ], c["expected_plan_revision"], c["expected_body_revision"], c["span_fingerprint"], c["context_fingerprint"], c["plan_fingerprint"], reason="新局部修改验收两个短块")
    fixture["before_blocks"] = written["blocks"]
    fixture["block_id"] = written["blocks"][0]["id"]
    fixture["before_plan"] = ops.read_script_segment(fixture["segment_id"])["units"][0]["plan"]
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    unit = ops.read_script_segment(fixture["segment_id"])["units"][0]
    blocks = unit["blocks"]
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    from narrative.tool_api import TOOLS
    writes = [s for s in audit["steps"] if s.get("ok") and s["tool"] in TOOLS and TOOLS[s["tool"]].writes]
    report["block_feedback_evidence"] = {"before": fixture["before_blocks"], "after": blocks, "successful_writes": writes, "semantic_review": "Read first block for clearer concise key-handoff behavior without new plot."}
    return {"target_ids_preserved": [b["id"] for b in blocks] == [b["id"] for b in fixture["before_blocks"]],
            "first_block_changed": blocks[0]["payload"]["text"] != fixture["before_blocks"][0]["payload"]["text"] and blocks[0]["type"] == "action",
            "second_block_exactly_unchanged": blocks[1] == fixture["before_blocks"][1],
            "only_fixed_block_writes": bool(writes) and all(s["tool"] == "apply_script_block_changes" and s["args"]["target_block_ids"] == [fixture["block_id"]] for s in writes),
            "plan_unchanged": unit["plan"] == fixture["before_plan"]}


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = "fixed-block-feedback/1"
    qa.FIXTURE_ORIGIN = "New isolated key handoff at night station consistent with source/timeline/scene plan; two controlled blocks, first-only edit."
    return qa.main(validate=validate, required_tools=["read_script_unit_context", "apply_script_block_changes"],
                   target_factory=lambda f: {"kind": "blocks", "ids": [f["block_id"]], "screenplay_id": f["screenplay_id"]},
                   prompt_factory=lambda _: TASK)


if __name__ == "__main__":
    raise SystemExit(main())
