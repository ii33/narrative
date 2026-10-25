"""Bounded, isolated real-model acceptance for v5 node properties.

This runner targets the remaining v5 gap: a real ``graph-agent/11`` run that
produces property records whose evidence, value types and event references pass
``property_precheck`` and become a human-reviewable property candidate.

It deliberately pre-seeds a small, human-authored synthetic graph (nodes and
events only, no properties) through ``Operations``. The model therefore never
needs a second graph-acceptance round before it can propose properties. The
fixture is recorded in the report and must not be read as model output.

Guarantees:

* a fresh, exclusively created output directory; existing paths are rejected;
* the synthetic source and its SHA-256 are recorded before any model request;
* the model may only propose; this runner never accepts or rejects;
* exactly one bounded run, no retry, no process killing;
* provider secrets are read from the environment and never written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from narrative.graph_operations import GraphOperations
from narrative.project_catalog import create_graph_project

SOURCE_TEXT = (
    "第一章\n"
    "傍晚，林舟站在旧车站的门口，手里握着一把铜钥匙。他等沈砚。\n"
    "第二章\n"
    "沈砚赶到旧车站。林舟把铜钥匙递给沈砚，说：“仓库的门锁着，你拿钥匙去开。”\n"
    "第三章\n"
    "沈砚独自走向仓库。林舟留在旧车站，没有跟去。\n"
)

FIXTURE_NODES = [
    {"kind": "node", "id": "linzhou", "node_type": "person", "name": "林舟"},
    {"kind": "node", "id": "shenyan", "node_type": "person", "name": "沈砚"},
    {"kind": "node", "id": "key", "node_type": "item", "name": "铜钥匙"},
    {"kind": "node", "id": "station", "node_type": "location", "name": "旧车站"},
    {"kind": "node", "id": "warehouse", "node_type": "location", "name": "仓库"},
    {"kind": "node", "id": "e_wait", "node_type": "event", "name": "林舟在旧车站等沈砚"},
    {"kind": "node", "id": "e_arrive", "node_type": "event", "name": "沈砚赶到旧车站"},
    {"kind": "node", "id": "e_transfer", "node_type": "event", "name": "林舟把铜钥匙递给沈砚"},
    {"kind": "node", "id": "e_walk", "node_type": "event", "name": "沈砚独自走向仓库"},
    {"kind": "node", "id": "e_stay", "node_type": "event", "name": "林舟留在旧车站"},
]

TASK = (
    "本轮只做节点属性提取，不新增或修改任何图节点、事件、时序断言或组织断言。"
    "图中已有：人物 linzhou（林舟）、shenyan（沈砚）；物品 key（铜钥匙）；"
    "地点 station（旧车站）、warehouse（仓库）；事件 e_wait、e_arrive、e_transfer、"
    "e_walk、e_stay。请只依据原文提取两个属性：铜钥匙的 holder（持有人）和林舟的 "
    "location（所在地）。先给出 property_definition，再给出初始状态与必要的事件改变；"
    "每条记录都带 evidence，evidence 的 source_id 使用已接受来源 ID，quote 必须是在原文中"
    "只出现一次的精确子串。用 property_precheck 检查，全部通过后调用 property_propose "
    "生成待人工审核候选；不要接受、不要删除、不要改写已接受记录。"
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _profile() -> dict:
    return {
        "provider": "ark",
        "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "model": "deepseek-v4.1-flash",
        "reasoning_effort": "high",
        "max_tokens": 32000,
        "context_window": 1000000,
        "output_reserve": 65536,
        "compaction_reserve": 32768,
        "compaction_max_tokens": 16384,
        "timeout_seconds": 300,
        "key_env": "DEEPSEEK_API_KEY",
        "token_parameter": "max_tokens",
    }


def _budget() -> dict:
    return {
        "research_rounds": 1,
        "max_rounds": 8,
        "max_tokens_per_round": 32000,
        "max_total_tokens": 500000,
        "wall_seconds": 900,
        "request_seconds": 300,
    }


def _build_fixture(ops: GraphOperations, source_text: str) -> dict:
    source_id = ops.import_source("属性代理验收·合成三章", source_text)
    revision = ops.graph_project_info()["revision"]
    candidate_id = ops.graph_propose(
        FIXTURE_NODES, revision, source="manual", note="合成图夹具（非模型输出）"
    )
    new_revision = ops.graph_accept(candidate_id, 1)
    snapshot = ops.graph_snapshot()
    ids = {obj.get("id") for obj in snapshot.get("objects", []) if isinstance(obj, dict)}
    missing = [row["id"] for row in FIXTURE_NODES if row["id"] not in ids]
    if missing:
        raise ValueError(f"夹具节点未进入正式图：{missing}")
    return {
        "source_id": source_id,
        "source_hash": ops.source(source_id)["hash"],
        "fixture_candidate_id": candidate_id,
        "revision_after_fixture": new_revision,
        "node_ids": [row["id"] for row in FIXTURE_NODES],
        "provenance": "human-authored synthetic fixture; not model output",
    }


def _candidates(ops: GraphOperations) -> dict:
    def rows(items: list[dict]) -> list[dict]:
        return [
            {
                "id": row.get("id"),
                "status": row.get("status"),
                "version": row.get("version"),
                "record_count": len(row.get("records") or []),
            }
            for row in items
        ]

    graph = rows(ops.graph_candidates())
    prop = rows(ops.property_candidates())
    return {"graph": graph, "property": prop, "count": len(graph) + len(prop)}


def _run(args: argparse.Namespace) -> int:
    output = Path(args.output_dir).resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(json.dumps({"event": "rejected", "error": f"输出目录已存在：{output}"}, ensure_ascii=False))
        return 2

    started = _utc_now()
    source_sha = _sha256(SOURCE_TEXT.encode("utf-8"))
    _atomic_json(
        output / "source.json",
        {
            "title": "属性代理验收·合成三章",
            "sha256": source_sha,
            "characters": len(SOURCE_TEXT),
            "text": SOURCE_TEXT,
            "provenance": "synthetic; authored for this acceptance only",
        },
    )

    project_path = output / "graph.sqlite3"
    ops = create_graph_project(project_path)
    fixture = _build_fixture(ops, SOURCE_TEXT)
    ops.save_provider_profile(_profile())
    saved_profile = ops.harness_settings()

    fixture_report = {
        "model_started": False,
        "started_utc": started,
        "output_dir": str(output),
        "project": str(project_path),
        "source_sha256": source_sha,
        "fixture": fixture,
        "provider_profile": saved_profile,
        "budget": _budget(),
        "review_action": "none",
    }

    if args.dry_run:
        # Deterministic self-check of the fixture: a hand-authored valid batch
        # must pass precheck, proving the graph/event references are usable.
        revision = ops.graph_project_info()["revision"]
        probe = [
            {
                "kind": "property_definition",
                "id": "probe_holder_def",
                "value_type": "node",
                "cardinality": "one",
                "persistence": "until_changed",
                "owner_node_types": ["item"],
                "target_node_types": ["person"],
            },
            {
                "kind": "property_initial",
                "id": "probe_holder_initial",
                "node_id": "key",
                "property_id": "probe_holder_def",
                "state": {"kind": "known", "value": "linzhou"},
            },
            {
                "kind": "event_change",
                "id": "probe_holder_transfer",
                "event_id": "e_transfer",
                "boundary": "end",
                "effects": [
                    {
                        "node_id": "key",
                        "property_id": "probe_holder_def",
                        "op": "set",
                        "value": "shenyan",
                        "expected": {"kind": "known", "value": "linzhou"},
                    }
                ],
            },
        ]
        diagnostics = ops.property_precheck(probe, revision)
        fixture_report["dry_run"] = {
            "probe_diagnostics": diagnostics,
            "passed": not any(row.get("severity") == "error" for row in diagnostics),
        }
        _atomic_json(output / "report.json", fixture_report)
        print(json.dumps({"event": "dry_run", "passed": fixture_report["dry_run"]["passed"]}, ensure_ascii=False))
        return 0 if fixture_report["dry_run"]["passed"] else 1

    run_id = ops.start_graph_analysis([fixture["source_id"]], task=TASK, budget=_budget())
    _atomic_json(output / "run.json", {"run_id": run_id, "task": TASK, **fixture_report})

    deadline = time.monotonic() + 1200
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
        time.sleep(1.0)
        state = ops.graph_analysis_status(run_id)

    candidates = _candidates(ops)
    property_recheck = []
    property_records = []
    for row in ops.property_candidates():
        candidate = ops.property_candidate(row["id"])
        records = candidate.get("records") or []
        property_records.append({"candidate_id": row["id"], "version": candidate.get("version"), "records": records})
        diagnostics = ops.property_precheck(records, candidate.get("expected_revision"))
        property_recheck.append(
            {
                "candidate_id": row["id"],
                "diagnostics": diagnostics,
                "passed": not any(item.get("severity") == "error" for item in diagnostics),
            }
        )

    report = {
        "started_utc": started,
        "ended_utc": _utc_now(),
        "model_started": True,
        "output_dir": str(output),
        "project": str(project_path),
        "source_sha256": source_sha,
        "fixture": fixture,
        "provider": state.get("provider"),
        "model": state.get("model"),
        "provider_profile": saved_profile,
        "prompt_version": state.get("prompt_version"),
        "prompt_hash": state.get("prompt_hash"),
        "schema_hash": state.get("schema_hash"),
        "code_hash": state.get("code_hash"),
        "budget": _budget(),
        "status": state.get("status"),
        "request_state": state.get("request_state"),
        "phase": state.get("phase"),
        "rounds": state.get("rounds"),
        "request_count": state.get("request_count"),
        "usage": state.get("usage"),
        "finish_reason": state.get("finish_reason"),
        "precheck_diagnostics": state.get("precheck_diagnostics", []),
        "error": state.get("error"),
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
        "journal": str(Path(ops.run_dir()) / run_id / "journal.json"),
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true", help="只建夹具并做确定性预检，不调用模型")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except Exception as exc:  # noqa: BLE001 - report the failure verbatim
        print(json.dumps({"event": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
