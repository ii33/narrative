"""Bounded, observable v5 graph-agent validation runner.

This runner is a thin, Operations-only driver. It never touches the database
directly, never accepts a candidate, never retries a model request, and never
kills a process. It:

* verifies ``--source`` against ``--source-manifest`` (exactly three contiguous
  chapter slices and their SHA-256) before any model start;
* creates an isolated v5 graph project in a freshly and exclusively created
  ``--output-dir`` that is never overwritten;
* saves the supplied provider profile (CLI flags or a safe JSON ``--config``)
  through ``Operations`` and starts ``start_graph_analysis``;
* writes an atomic ``progress.json`` every second with a UTC heartbeat and a
  distinct ModelGateway upstream response-byte activity timestamp;
* keeps candidates for human review (``review_action`` is always ``none``);
* on Ctrl-C calls ``cancel_graph_analysis`` for the named run and still writes
  the final report.

The manifest is JSON::

    {
      "source_sha256": "<optional whole-file sha256>",
      "chapters": [
        {"title": "一", "start": 0, "end": 1200, "sha256": "<64 hex>"},
        {"title": "二", "start": 1200, "end": 2600, "sha256": "<64 hex>"},
        {"title": "三", "start": 2600, "end": 4100, "sha256": "<64 hex>"}
      ]
    }

Chapter ranges are half-open ``[start, end)`` Unicode character offsets and
must cover the whole source contiguously. ``slices`` is accepted as an alias
for ``chapters``. The API key is read from the provider's environment variable
by the existing runner; it is never passed on the command line or persisted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from narrative.project_catalog import create_graph_project

MANIFEST_SLICE_COUNT = 3
MAX_TOKENS_PER_ROUND_CEILING = 160_000
PROGRESS_HEARTBEAT_SECONDS = 1.0
SECRET_FIELD_NAMES = frozenset(
    {"apikey", "key", "secret", "authorization", "bearer", "accesstoken", "credential"}
)
PROFILE_FIELDS = (
    "provider",
    "base_url",
    "model",
    "reasoning_effort",
    "max_tokens",
    "context_window",
    "output_reserve",
    "compaction_reserve",
    "compaction_max_tokens",
    "timeout_seconds",
    "key_env",
    "token_parameter",
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_secret_field(name: str) -> bool:
    normalized = "".join(char for char in str(name).lower() if char.isalnum())
    return normalized in SECRET_FIELD_NAMES


def verify_source_manifest(source_path: Path, manifest_path: Path) -> dict:
    """Verify exactly three contiguous chapters and their SHA-256.

    Must run before any model start. Returns a non-sensitive verification record;
    raises ``ValueError`` when the source does not exactly match the manifest.
    """
    source_bytes = Path(source_path).read_bytes()
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"原文不是 UTF-8：{exc}") from None
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("来源清单须为 JSON 对象")
    slices = manifest.get("chapters", manifest.get("slices"))
    if not isinstance(slices, list) or len(slices) != MANIFEST_SLICE_COUNT:
        raise ValueError(f"来源清单必须恰好包含 {MANIFEST_SLICE_COUNT} 个连续章节切片")
    checks = []
    cursor = 0
    for index, item in enumerate(slices):
        position = index + 1
        if not isinstance(item, dict):
            raise ValueError(f"第 {position} 个章节切片须为对象")
        title = item.get("title")
        start = item.get("start")
        end = item.get("end")
        expected = item.get("sha256", item.get("sha256_utf8"))
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"第 {position} 个章节切片缺少标题")
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            raise ValueError(f"第 {position} 个章节切片范围无效")
        if start != cursor:
            raise ValueError(f"章节切片不连续：第 {position} 段从 {start} 开始，应为 {cursor}")
        if end > len(text):
            raise ValueError(f"第 {position} 个章节切片超出来源长度")
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"第 {position} 个章节切片缺少 64 位 SHA-256")
        actual = _sha256_hex(text[start:end].encode("utf-8"))
        if actual != expected.lower():
            raise ValueError(f"第 {position} 个章节切片 SHA-256 不匹配")
        checks.append(
            {
                "index": position,
                "title": title,
                "start": start,
                "end": end,
                "sha256": actual,
                "characters": end - start,
            }
        )
        cursor = end
    if cursor != len(text):
        raise ValueError("章节切片未覆盖完整来源正文")
    source_sha256 = _sha256_hex(source_bytes)
    declared_source = manifest.get("source_sha256", manifest.get("source_sha256_utf8"))
    if declared_source is not None and declared_source.lower() != source_sha256:
        raise ValueError("来源整体 SHA-256 与清单不匹配")
    return {
        "source_sha256": source_sha256,
        "manifest_sha256": _sha256_hex(Path(manifest_path).read_bytes()),
        "slice_count": len(checks),
        "chapters": checks,
        "characters": len(text),
        "input_hash": _sha256_hex("".join(row["sha256"] for row in checks).encode("ascii")),
    }


def create_output_dir(path: Path) -> Path:
    """Create a distinct result directory exclusively; never reuse or overwrite it."""
    target = Path(path).resolve()
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise ValueError(f"输出目录已存在，禁止覆盖：{target}") from None
    return target


def _atomic_write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def build_progress(
    *,
    run_id: str | None,
    runner_pid: int,
    status: str | None,
    phase: str | None,
    rounds: int,
    request_state: str | None,
    usage: dict | None,
    candidate_count: int,
    upstream_activity: dict | None,
    budget: dict,
    started_utc: str,
    heartbeat_utc: str | None = None,
) -> dict:
    """One atomic progress record; heartbeat and upstream activity stay distinct."""
    activity = upstream_activity or {}
    return {
        "run_id": run_id,
        "runner_pid": runner_pid,
        "heartbeat_utc": heartbeat_utc or _utc_now(),
        "status": status,
        "phase": phase,
        "rounds": rounds,
        "request_state": request_state,
        "usage": usage,
        "candidate_count": candidate_count,
        "budget": budget,
        # Only ModelGateway response bytes update this; it is never inferred from
        # the local heartbeat above.
        "last_upstream_response_utc": activity.get("last_response_utc"),
        "last_upstream_response_age_seconds": activity.get("last_response_age_seconds"),
        "upstream_response_bytes": activity.get("response_bytes"),
        "upstream_response_events": activity.get("response_events"),
        "upstream_activity_source": activity.get("source"),
        "started_utc": started_utc,
    }


def _candidate_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": row.get("id"),
            "status": row.get("status"),
            "version": row.get("version"),
        }
        for row in rows
    ]


def _step_summary(step: dict) -> dict:
    return {
        "round": step.get("round"),
        "phase": step.get("phase"),
        "input_hash": step.get("input_hash"),
        "prompt_hash": step.get("prompt_hash"),
        "finish_reason": step.get("finish_reason"),
        "usage": step.get("usage"),
        "usage_incomplete": step.get("usage_incomplete"),
        "candidate_id": step.get("candidate_id"),
        "tools": [tool.get("tool") for tool in step.get("tools", [])],
        "action_diagnostics": step.get("action_diagnostics", []),
        "diagnostics": step.get("diagnostics", []),
    }


def build_report(
    *,
    runner_pid: int,
    run_id: str,
    status_state: dict,
    verification: dict,
    profile: dict,
    budget: dict,
    journal_path: Path,
    source_id: str,
    source_hash: str,
    started_utc: str,
    ended_utc: str,
    candidates: dict,
    upstream_activity: dict | None,
    review_action: str = "none",
) -> dict:
    """Final, non-sensitive report. Model raw responses stay only in the journal."""
    steps = [step for step in status_state.get("steps", []) if isinstance(step, dict)]
    finish_reasons = [
        step["finish_reason"]
        for step in steps
        if isinstance(step.get("finish_reason"), str) and step["finish_reason"]
    ]
    return {
        "runner_pid": runner_pid,
        "run_id": run_id,
        "status": status_state.get("status"),
        "request_state": status_state.get("request_state"),
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "model_started": True,
        "prompt_version": status_state.get("prompt_version"),
        "prompt_hash": status_state.get("prompt_hash"),
        "schema_hash": status_state.get("schema_hash"),
        "code_hash": status_state.get("code_hash"),
        "provider": status_state.get("provider"),
        "model": status_state.get("model"),
        "provider_profile": profile,
        "context_policy": status_state.get("context_policy"),
        "config": status_state.get("config"),
        "budget": budget,
        "input_hash": verification.get("input_hash"),
        "source_sha256": verification.get("source_sha256"),
        "manifest_sha256": verification.get("manifest_sha256"),
        "source_id": source_id,
        "source_hash": source_hash,
        "chapters": verification.get("chapters"),
        "response_journal_location": str(journal_path),
        "response_journal_sha256": _sha256_hex(journal_path.read_bytes())
        if journal_path.is_file()
        else None,
        "finish_reason": finish_reasons[-1] if finish_reasons else None,
        "finish_reasons": finish_reasons,
        "usage": status_state.get("usage"),
        "request_count": status_state.get("request_count"),
        "compaction_request_count": status_state.get("compaction_request_count"),
        "precheck_diagnostics": status_state.get("precheck_diagnostics", []),
        "candidates": candidates,
        "review_action": review_action,
        "errors": status_state.get("error"),
        "upstream_activity": upstream_activity
        or {
            "last_response_utc": None,
            "last_response_age_seconds": None,
            "response_bytes": None,
            "response_events": None,
            "source": "model_gateway_upstream",
        },
        "steps": [_step_summary(step) for step in steps],
    }


def _resolve_profile(args: argparse.Namespace) -> dict:
    profile: dict = {}
    if args.config is not None:
        config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
        if not isinstance(config, dict):
            raise ValueError("--config 须为 JSON 对象")
        secret_fields = sorted(key for key in config if _is_secret_field(key))
        if secret_fields:
            raise ValueError(f"provider 配置不得包含密钥字段：{secret_fields}")
        profile.update(config)
    for field in PROFILE_FIELDS:
        value = getattr(args, field, None)
        if value is not None:
            profile[field] = value
    if not profile.get("provider"):
        raise ValueError("必须通过 --provider 或 --config 提供 provider")
    return profile


def _budget(args: argparse.Namespace) -> dict:
    if not 1 <= args.max_tokens_per_round <= MAX_TOKENS_PER_ROUND_CEILING:
        raise ValueError(
            f"--max-tokens-per-round 须为 1–{MAX_TOKENS_PER_ROUND_CEILING} 的正整数"
        )
    if args.max_rounds <= 0 or args.max_total_tokens <= 0:
        raise ValueError("--max-rounds 与 --max-total-tokens 须为正整数")
    if not 5 <= args.request_seconds <= 600:
        raise ValueError("--request-seconds 须为 5–600 的整数")
    return {
        "research_rounds": args.research_rounds,
        "max_rounds": args.max_rounds,
        "max_tokens_per_round": args.max_tokens_per_round,
        "max_total_tokens": args.max_total_tokens,
        "wall_seconds": None,
        "request_seconds": args.request_seconds,
    }


def _candidates(ops) -> dict:
    graph_rows = _candidate_rows(ops.graph_candidates())
    property_rows = _candidate_rows(ops.property_candidates())
    combined = graph_rows + property_rows
    return {
        "graph": graph_rows,
        "property": property_rows,
        "count": len(combined),
        "accepted": sum(1 for row in combined if row.get("status") == "accepted"),
    }


def _write_progress(
    path: Path,
    *,
    ops,
    run_id: str,
    runner_pid: int,
    state: dict,
    budget: dict,
    started_utc: str,
) -> dict:
    try:
        activity = _upstream_activity(ops, run_id)
    except ValueError:
        activity = None
    progress = build_progress(
        run_id=run_id,
        runner_pid=runner_pid,
        status=state.get("status"),
        phase=state.get("phase"),
        rounds=state.get("rounds", 0),
        request_state=state.get("request_state"),
        usage=state.get("usage"),
        candidate_count=len(state.get("candidate_ids") or []),
        upstream_activity=activity,
        budget=budget,
        started_utc=started_utc,
    )
    _atomic_write_json(path, progress)
    return progress


def _upstream_activity(ops, run_id: str) -> dict | None:
    from narrative import graph_agent

    return graph_agent.upstream_activity(ops, run_id)


def _progress_line(progress: dict) -> str:
    return json.dumps(
        {
            "event": "progress",
            "run_id": progress["run_id"],
            "status": progress["status"],
            "phase": progress["phase"],
            "rounds": progress["rounds"],
            "request_state": progress["request_state"],
            "candidate_count": progress["candidate_count"],
            "total_tokens": (progress.get("usage") or {}).get("total_tokens"),
            "heartbeat_utc": progress["heartbeat_utc"],
            "last_upstream_response_utc": progress["last_upstream_response_utc"],
        },
        ensure_ascii=False,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, type=Path, help="UTF-8 原文文件")
    parser.add_argument("--source-manifest", required=True, type=Path, help="来源清单 JSON（恰好 3 个连续章节）")
    parser.add_argument("--output-dir", required=True, type=Path, help="独立结果目录；必须不存在，禁止覆盖")
    parser.add_argument("--task", default=None, help="本轮原文分析目标")
    parser.add_argument("--config", type=Path, default=None, help="安全的 provider JSON 配置（不含密钥）")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", dest="base_url", default=None)
    parser.add_argument("--reasoning-effort", dest="reasoning_effort", default=None)
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, default=None)
    parser.add_argument("--context-window", dest="context_window", type=int, default=None)
    parser.add_argument("--output-reserve", dest="output_reserve", type=int, default=None)
    parser.add_argument("--compaction-reserve", dest="compaction_reserve", type=int, default=None)
    parser.add_argument("--compaction-max-tokens", dest="compaction_max_tokens", type=int, default=None)
    parser.add_argument("--timeout-seconds", dest="timeout_seconds", type=int, default=None)
    parser.add_argument("--key-env", dest="key_env", default=None)
    parser.add_argument("--token-parameter", dest="token_parameter", choices=("max_tokens", "max_completion_tokens"), default=None)
    parser.add_argument("--research-rounds", type=int, default=3)
    parser.add_argument("--max-rounds", type=int, default=32)
    parser.add_argument("--max-total-tokens", type=int, default=3_000_000)
    parser.add_argument("--max-tokens-per-round", type=int, default=MAX_TOKENS_PER_ROUND_CEILING)
    parser.add_argument("--request-seconds", type=int, default=180)
    parser.add_argument("--poll-seconds", type=float, default=PROGRESS_HEARTBEAT_SECONDS)
    parser.add_argument("--cancel-wait-seconds", type=float, default=60.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    started_utc = _utc_now()
    runner_pid = os.getpid()
    try:
        budget = _budget(args)
        profile = _resolve_profile(args)
    except (OSError, ValueError) as exc:
        print(json.dumps({"event": "invalid_arguments", "error": str(exc)}, ensure_ascii=False), flush=True)
        return 2

    try:
        output = create_output_dir(Path(args.output_dir))
    except (OSError, ValueError) as exc:
        print(json.dumps({"event": "output_dir_rejected", "error": str(exc)}, ensure_ascii=False), flush=True)
        return 2

    _atomic_write_json(
        output / "runner.json",
        {
            "runner_pid": runner_pid,
            "started_utc": started_utc,
            "source": str(Path(args.source).resolve()),
            "source_manifest": str(Path(args.source_manifest).resolve()),
            "review_action": "none",
        },
    )
    progress_path = output / "progress.json"
    _atomic_write_json(
        progress_path,
        build_progress(
            run_id=None,
            runner_pid=runner_pid,
            status="precheck",
            phase=None,
            rounds=0,
            request_state=None,
            usage=None,
            candidate_count=0,
            upstream_activity=None,
            budget=budget,
            started_utc=started_utc,
        ),
    )
    print(
        json.dumps(
            {"event": "precheck", "runner_pid": runner_pid, "output_dir": str(output)},
            ensure_ascii=False,
        ),
        flush=True,
    )

    try:
        verification = verify_source_manifest(Path(args.source), Path(args.source_manifest))
    except (OSError, ValueError) as exc:
        _atomic_write_json(
            output / "report.json",
            {
                "runner_pid": runner_pid,
                "run_id": None,
                "status": "precheck_failed",
                "started_utc": started_utc,
                "ended_utc": _utc_now(),
                "model_started": False,
                "budget": budget,
                "review_action": "none",
                "errors": str(exc),
            },
        )
        print(json.dumps({"event": "precheck_failed", "error": str(exc)}, ensure_ascii=False), flush=True)
        return 2
    _atomic_write_json(
        output / "precheck.json",
        {"model_started": False, **verification},
    )

    source_text = Path(args.source).read_bytes().decode("utf-8")
    try:
        ops = create_graph_project(output / "graph.sqlite3")
        source_id = ops.import_source(
            (verification.get("chapters") or [{}])[0].get("title") or Path(args.source).stem,
            source_text,
        )
        source_hash = ops.source(source_id)["hash"]
        if source_hash != verification["source_sha256"]:
            raise ValueError("导入原文哈希与来源清单不一致")
        ops.save_provider_profile(profile)
        saved_profile = ops.harness_settings()
        run_id = ops.start_graph_analysis([source_id], task=args.task, budget=budget)
    except (OSError, ValueError) as exc:
        _atomic_write_json(
            output / "report.json",
            {
                "runner_pid": runner_pid,
                "run_id": None,
                "status": "start_failed",
                "started_utc": started_utc,
                "ended_utc": _utc_now(),
                "model_started": False,
                "input_hash": verification.get("input_hash"),
                "source_sha256": verification.get("source_sha256"),
                "manifest_sha256": verification.get("manifest_sha256"),
                "provider_profile": profile,
                "budget": budget,
                "review_action": "none",
                "errors": str(exc),
            },
        )
        print(json.dumps({"event": "start_failed", "error": str(exc)}, ensure_ascii=False), flush=True)
        return 2

    journal_path = Path(ops.run_dir()) / run_id / "journal.json"
    print(json.dumps({"event": "started", "run_id": run_id, "journal": str(journal_path)}, ensure_ascii=False), flush=True)

    state = ops.graph_analysis_status(run_id)
    cancelled = False
    try:
        while True:
            state = ops.graph_analysis_status(run_id)
            progress = _write_progress(
                progress_path,
                ops=ops,
                run_id=run_id,
                runner_pid=runner_pid,
                state=state,
                budget=budget,
                started_utc=started_utc,
            )
            print(_progress_line(progress), flush=True)
            if state.get("status") not in {"queued", "running"}:
                break
            time.sleep(max(0.05, args.poll_seconds))
    except KeyboardInterrupt:
        cancelled = True
        try:
            ops.cancel_graph_analysis(run_id)
            print(json.dumps({"event": "cancel_requested", "run_id": run_id}, ensure_ascii=False), flush=True)
        except (OSError, ValueError) as exc:
            print(json.dumps({"event": "cancel_failed", "error": str(exc)}, ensure_ascii=False), flush=True)
        deadline = time.monotonic() + max(0.0, args.cancel_wait_seconds)
        while time.monotonic() < deadline:
            try:
                state = ops.graph_analysis_status(run_id)
            except ValueError:
                break
            _write_progress(
                progress_path,
                ops=ops,
                run_id=run_id,
                runner_pid=runner_pid,
                state=state,
                budget=budget,
                started_utc=started_utc,
            )
            if state.get("status") not in {"queued", "running"}:
                break
            try:
                time.sleep(0.5)
            except KeyboardInterrupt:
                break

    state = ops.graph_analysis_status(run_id)
    try:
        activity = _upstream_activity(ops, run_id)
    except ValueError:
        activity = None
    report = build_report(
        runner_pid=runner_pid,
        run_id=run_id,
        status_state=state,
        verification=verification,
        profile=saved_profile,
        budget=budget,
        journal_path=journal_path,
        source_id=source_id,
        source_hash=source_hash,
        started_utc=started_utc,
        ended_utc=_utc_now(),
        candidates=_candidates(ops),
        upstream_activity=activity,
        review_action="none",
    )
    report["cancelled_by_user"] = cancelled
    _atomic_write_json(output / "report.json", report)
    print(
        json.dumps(
            {
                "event": "finished",
                "run_id": run_id,
                "status": report["status"],
                "candidate_count": report["candidates"]["count"],
                "review_action": report["review_action"],
                "report": str(output / "report.json"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 130 if cancelled else (0 if report["status"] == "complete" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
