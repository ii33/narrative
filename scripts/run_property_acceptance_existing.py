"""Run one chapter-scale property extraction on an existing reviewed graph.

This is the second stage of the v5 property gate: the graph candidate has
already been accepted by a human, so the model may reference its stable node
and event IDs. The runner never accepts a property candidate, never stages
graph objects, and records every request and diagnostic.

Example:

    uv run python scripts/run_property_acceptance_existing.py \
        --project data/example-run/graph.sqlite3 \
        --output-dir data/example-run/property-acceptance-20261020/run-ark
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from narrative.graph_operations import GraphOperations
from narrative.project_catalog import open_graph_project

try:
    from narrative.graph_agent import PROMPT_VERSION
except Exception:  # noqa: BLE001 - fall back to a recorded value
    PROMPT_VERSION = "graph-agent/11"

DEFAULT_TASK = (
    "本轮只做节点属性提取，不新增或修改任何图节点、事件、时序断言或组织断言。"
    "图中已有已接受的人物、物品、地点和事件；先用 graph_event_index 或 graph_snapshot 读取稳定 ID，"
    "再用 source_excerpt 核对原文。只提交 property_definition、property_initial、"
    "property_observation、event_change 记录，不提交 node/assertion/evidence 对象。"
    "优先提取对连续性有用的属性，例如物品持有人、人物所在地、人物状态、物品状态；"
    "每条记录都带 evidence，evidence 的 source_id 使用已接受来源 ID，quote 必须是在原文中只出现一次的精确子串。"
    "用 property_precheck 检查，全部通过后调用 property_propose 生成待人工审核候选；"
    "不要接受、不要删除、不要改写已接受记录。无法由原文确定的属性保持未知，不猜补。"
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _profile(args: argparse.Namespace) -> dict:
    return {
        "provider": "ark",
        "base_url": args.base_url,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "max_tokens": args.max_tokens,
        "context_window": 1000000,
        "output_reserve": 131072,
        "compaction_reserve": 65536,
        "compaction_max_tokens": 32768,
        "timeout_seconds": args.request_seconds,
        "key_env": "DEEPSEEK_API_KEY",
        "token_parameter": "max_tokens",
    }


def _budget(args: argparse.Namespace) -> dict:
    return {
        "research_rounds": args.research_rounds,
        "max_rounds": args.max_rounds or None,
        "max_tokens_per_round": args.max_tokens,
        "max_total_tokens": args.max_total_tokens or None,
        "wall_seconds": None,
        "request_seconds": args.request_seconds,
    }


def _candidates(ops: GraphOperations) -> dict:
    def rows(items: list[dict]) -> list[dict]:
        return [
            {
                "id": row.get("id"),
                "status": row.get("status"),
                "version": row.get("version"),
                "record_count": len(row.get("records") or row.get("objects") or []),
            }
            for row in items
        ]

    graph = rows(ops.graph_candidates())
    prop = rows(ops.property_candidates())
    return {"graph": graph, "property": prop, "count": len(graph) + len(prop)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--base-url", default="https://ark.cn-beijing.volces.com/api/plan/v3")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--max-tokens", type=int, default=160000)
    parser.add_argument("--research-rounds", type=int, default=3)
    parser.add_argument("--max-rounds", type=int, default=0, help="0 表示不设总轮数上限")
    parser.add_argument("--max-total-tokens", type=int, default=0, help="0 表示不设总 token 上限")
    parser.add_argument("--request-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--wall-deadline-seconds", type=int, default=7200)
    args = parser.parse_args(argv)

    output = args.output_dir.resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(json.dumps({"event": "rejected", "error": f"输出目录已存在：{output}"}, ensure_ascii=False))
        return 2

    ops = open_graph_project(args.project)
    info = ops.graph_project_info()
    sources = [row["id"] for row in ops.sources()]
    if not sources:
        print(json.dumps({"event": "error", "error": "项目没有原文来源"}, ensure_ascii=False))
        return 2
    ops.save_provider_profile(_profile(args))
    saved_profile = ops.harness_settings()
    budget = _budget(args)
    started = _utc_now()
    run_id = ops.start_graph_analysis(sources, task=args.task, budget=budget)
    journal = Path(ops.run_dir()) / run_id / "journal.json"

    _atomic_json(
        output / "run.json",
        {
            "schema": "narrative-property-acceptance/1",
            "started_utc": started,
            "project": str(args.project.resolve()),
            "project_info": info,
            "source_ids": sources,
            "run_id": run_id,
            "prompt_version": PROMPT_VERSION,
            "provider_profile": saved_profile,
            "budget": budget,
            "task": args.task,
            "journal": str(journal),
            "review_action": "none",
        },
    )

    deadline = time.monotonic() + args.wall_deadline_seconds
    state = ops.graph_analysis_status(run_id)
    while state.get("status") in {"queued", "running"} and time.monotonic() < deadline:
        _atomic_json(
            output / "progress.json",
            {
                "run_id": run_id,
                "status": state.get("status"),
                "phase": state.get("phase"),
                "rounds": state.get("rounds"),
                "request_count": state.get("request_count"),
                "usage": state.get("usage"),
                "heartbeat_utc": _utc_now(),
            },
        )
        time.sleep(max(0.5, args.poll_seconds))
        state = ops.graph_analysis_status(run_id)

    candidates = _candidates(ops)
    property_recheck = []
    property_records = []
    for row in ops.property_candidates():
        candidate = ops.property_candidate(row["id"])
        records = candidate.get("records") or []
        property_records.append(
            {"candidate_id": row["id"], "version": candidate.get("version"), "records": records}
        )
        diagnostics = ops.property_precheck(records, candidate.get("expected_revision"))
        property_recheck.append(
            {
                "candidate_id": row["id"],
                "diagnostics": diagnostics,
                "passed": not any(item.get("severity") == "error" for item in diagnostics),
            }
        )

    report = {
        "schema": "narrative-property-acceptance/1",
        "started_utc": started,
        "project": str(args.project.resolve()),
        "project_info": ops.graph_project_info(),
        "source_ids": sources,
        "run_id": run_id,
        "prompt_version": state.get("prompt_version"),
        "prompt_hash": state.get("prompt_hash"),
        "provider": state.get("provider"),
        "model": state.get("model"),
        "provider_profile": saved_profile,
        "budget": budget,
        "status": state.get("status"),
        "phase": state.get("phase"),
        "rounds": state.get("rounds"),
        "request_count": state.get("request_count"),
        "usage": state.get("usage"),
        "finish_reason": state.get("finish_reason"),
        "error": state.get("error"),
        "precheck_diagnostics": state.get("precheck_diagnostics", []),
        "candidates": candidates,
        "property_candidate_records": property_records,
        "property_recheck": property_recheck,
        "review_action": "none",
        "steps": [
            {
                "round": step.get("round"),
                "phase": step.get("phase"),
                "finish_reason": step.get("finish_reason"),
                "usage": step.get("usage"),
                "tools": [tool.get("tool") for tool in step.get("tools", [])],
                "diagnostics": step.get("diagnostics", []),
            }
            for step in state.get("steps", [])
        ],
        "journal": str(journal),
    }
    _atomic_json(output / "report.json", report)
    print(
        json.dumps(
            {
                "event": "finished",
                "status": report["status"],
                "request_count": report["request_count"],
                "usage": report["usage"],
                "candidates": report["candidates"],
                "property_passed": [row["passed"] for row in property_recheck],
                "report": str(output / "report.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
