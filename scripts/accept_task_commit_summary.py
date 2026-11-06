"""One real multi-write task with program-authored final commit accounting."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_screenplay_compile import seed as consistent_seed

TASK = "请在当前工程新增一个材质资产，名称“亚麻纹理”，详情“适合柔和环境光的布面参考”。随后将这个资产改名为“浅灰亚麻纹理”，保留详情。无需查找其他资产；如有重要的质感选择建议，可简短说明。"


def seed(ops):
    ops.save_harness_settings({"provider": "ark", "model": "ark-code-latest", "reasoning_effort": "low",
        "context_window": 1_000_000, "output_reserve": 131_072, "compaction_reserve": 65_536,
        "compaction_max_tokens": 32_768, "max_tokens": 131_072})
    fixture = consistent_seed(ops)
    fixture["before_screenplay"] = ops.read_screenplay(fixture["screenplay_id"])
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    from narrative.chat_store import ChatStore
    from narrative.tool_api import TOOLS

    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    summary = ops.read_task_write_summary(audit["task_id"], audit["chat_id"])
    chat = ChatStore(ops.project_info().path).load(audit["chat_id"])
    rows = [m for m in chat["messages"] if m.get("task_id") == audit["task_id"] and m.get("kind") == "task_summary"]
    assets = ops.list_assets("material")["assets"]
    writes = [step for step in audit["steps"] if step.get("ok") and TOOLS.get(step.get("tool")) and TOOLS[step["tool"]].writes]
    model_messages = [m for m in chat["messages"] if m.get("task_id") == audit["task_id"] and m.get("kind") == "model_output"]
    report["program_accounting_evidence"] = {"summary": summary, "task_summary_rows": rows,
        "successful_writes": writes, "model_messages": model_messages, "assets": assets,
        "semantic_review": "The model's creative suggestion remains model output, not accounting authority. Program counts are derived only from the two successful save_asset operations. No separate host final-summary ask is allowed."}
    return {
        "both_requested_steps_completed": len(assets) == 1 and assets[0]["title"] == "浅灰亚麻纹理" and assets[0]["detail"] == "适合柔和环境光的布面参考",
        "two_actual_asset_commits": len(writes) == 2 and all(step["tool"] == "save_asset" for step in writes),
        "program_summary_truthful": summary["source"] == "program" and summary["status"] == "completed" and summary["committed_operations"] == 2 and summary["totals"]["created"] == summary["totals"]["updated"] == 1,
        "one_persistent_program_card": len(rows) == 1 and rows[0]["role"] == "system",
        "card_and_audit_match": len(rows) == 1 and rows[0]["commit_summary"] == audit["commit_summary"] == summary,
        "natural_end_without_extra_host_summary_call": audit["model_calls"] == 1,
        "raw_model_reply_preserved_separately": bool(model_messages) and all(m.get("full_output") for m in model_messages),
        "screenplay_unchanged": screenplay == fixture["before_screenplay"],
    }


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = "program-commit-summary/1"
    qa.FIXTURE_ORIGIN = "New isolated project/chat: model creates and renames one material asset; program derives accounting from successful tool results, model may provide a short creative suggestion."
    return qa.main(validate=validate, required_tools=["save_asset"], use_writing_target=False, prompt_factory=lambda _: TASK)


if __name__ == "__main__":
    raise SystemExit(main())
