"""Single isolated task evaluating compact visible process notes and program accounting."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_storyboard import TASK, seed
from accept_storyboard import validate as storyboard_validate


def validate(ops, fixture, screenplay, calls, report):
    from narrative.chat_store import ChatStore
    checks = storyboard_validate(ops, fixture, screenplay, calls, report)
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    chat = ChatStore(ops.project_info().path).load(audit["chat_id"])
    rows = [m for m in chat["messages"] if m.get("task_id") == audit["task_id"] and m.get("kind") == "model_output"]
    texts = [m["text"] for m in rows if m.get("text")]
    report["thought_evidence"] = {"model_rows": rows, "visible_texts": texts,
        "review_required": "Check actual visible notes for repeated write counts/IDs; default-folding metadata alone does not prove the model obeyed compact-output guidance."}
    summary = ops.read_task_write_summary(audit["task_id"], audit["chat_id"])
    report["program_summary"] = summary
    checks["program_accounting"] = summary["status"] == "completed" and summary["totals"]["saved"] >= 1 and bool(summary.get("compact_text"))
    checks.update(business_rows_explicit_thought=bool(rows) and all(m.get("display_kind") == "thought" for m in rows),
        raw_preserved=all(m.get("full_output") for m in rows), compact_visible_notes=all(len(t) < 400 for t in texts))
    return checks


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = "task-thought-compact/2"
    qa.FIXTURE_ORIGIN = "New isolated storyboard fixture; evaluate explicit thought presentation, unchanged raw model records and compact visible process notes."
    return qa.main(validate=validate, required_tools=["read_storyboard", "apply_storyboard_changes", "generate_storyboard_sketch"],
        target_factory=lambda f: {"kind": "storyboard", "ids": [f["unit_id"]], "screenplay_id": f["screenplay_id"]}, prompt_factory=lambda f: TASK.format(**f))


if __name__ == "__main__":
    raise SystemExit(main())
