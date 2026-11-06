"""One real no-tool reply checks provider usage -> live ledger -> context sample."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_screenplay_compile import seed as consistent_seed

TASK = "这是用量显示的测试。不要调用任何工具，不要读取或修改工程，只回复“用量测试完成”。"


def seed(ops):
    ops.save_harness_settings({"provider": "ark", "model": "ark-code-latest", "reasoning_effort": "low",
        "context_window": 1_000_000, "output_reserve": 131_072, "compaction_reserve": 65_536,
        "compaction_max_tokens": 32_768, "max_tokens": 131_072})
    fixture = consistent_seed(ops)
    fixture["before_screenplay"] = ops.read_screenplay(fixture["screenplay_id"])
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    requests = [request for turn in audit.get("calls", []) for request in turn.get("model_requests", [])]
    actual = requests[-1].get("usage") or {} if requests else {}
    statistics = ops.read_usage_statistics(audit["chat_id"], audit["task_id"])
    task, chat, context = [statistics[key] for key in ["task_usage", "chat_usage", "context"]]
    report["usage_evidence"] = {"provider_usage": actual, "statistics": statistics,
        "sampling_boundary": "Context is the latest generation input actually measured by the provider; it is stale after output/tools, not the accumulated conversation bill."}
    return {
        "one_provider_request": len(requests) == 1,
        "no_tools_called": not calls and not audit.get("steps"),
        "task_input_matches_provider": bool(actual) and task["input_tokens"] == actual.get("prompt_tokens"),
        "task_output_matches_provider": bool(actual) and task["output_tokens"] == actual.get("completion_tokens"),
        "task_total_matches_provider": bool(actual) and task["total_tokens"] == actual.get("total_tokens"),
        "chat_not_double_counted": chat["requests"] == task["requests"] == 1 and chat["total_tokens"] == task["total_tokens"],
        "live_snapshot_published": statistics["data_sequence"] > 0,
        "context_latest_actual_input": bool(actual) and context["last_generation_input_tokens"] == actual.get("prompt_tokens") and context["source"] == "actual",
        "context_marks_post_generation_stale": context["stale"] is True,
        "actual_sample_time_preserved": bool(context.get("last_known_sampled_at")) and context["last_known_sampled_at"] == requests[-1].get("usage_sampled_at"),
        "screenplay_unchanged": screenplay == fixture["before_screenplay"],
    }


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = "usage-statistics-real/1"
    qa.FIXTURE_ORIGIN = "New isolated project/chat, deterministic source and scene fixture; real model must only reply one sentence without tools."
    return qa.main(validate=validate, required_tools=[], use_writing_target=False, prompt_factory=lambda _: TASK)


if __name__ == "__main__":
    raise SystemExit(main())
