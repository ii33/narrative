"""Bounded real-service acceptance of slice extraction and persistent review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from narrative.extraction import PROMPT_VERSION
from narrative.harness import HarnessRunner, HarnessSettings, effective_runtime_config
from narrative.operations import Operations
from narrative.project_package import layout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ["NARRATIVE_PROVIDER_DB"] = str(out / "providers.sqlite3")
    ops = Operations.create_graph_project(out / "acceptance.nproj")
    text = "林舟来到书店，向店主周宁买下一本旧书。"
    source_id = ops.import_source("最小切片验收", text)
    part = ops.create_source_slices(source_id, [{"title": "书店买书", "start": 0, "end": len(text)}])[0]
    settings = HarnessSettings(provider="ark", timeout_seconds=20)
    ops.save_provider_profile(settings.model_dump())
    report = {
        "version": "slice-conversation-acceptance/1", "prompt_version": PROMPT_VERSION,
        "provider": settings.provider, "model": settings.model,
        "config": effective_runtime_config(settings), "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "status": "incomplete", "raw_model_output_pass": False, "call_budget": 6,
        "model_turn_attempts": 0, "provider_request_count": None,
        "session_ids": [], "phases": [], "candidate_count": 0,
        "review_actions": [], "usage": [], "finish_reasons": [], "precheck": [],
    }
    original = HarnessRunner.agent_turn

    def bounded_turn(self, instruction, session_id):
        if report["model_turn_attempts"] >= report["call_budget"]:
            raise ValueError("Acceptance model turn budget reached")
        report["model_turn_attempts"] += 1
        report["session_ids"].append(session_id)
        return original(self, instruction, session_id)

    HarnessRunner.agent_turn = bounded_turn
    try:
        extracted = ops.extract_slice(source_id, part["id"])
        report["phases"].append({"phase": "extract", "nodes": len(extracted.get("nodes") or []),
                                 "properties": len(extracted.get("properties") or [])})
        if not extracted.get("nodes"):
            raise ValueError("Real model returned no legal extracted node")
        reviewed = ops.extract_slice(source_id, part["id"], review=True)
        report["phases"].append({"phase": "review", "round": reviewed["round"]})
        if len(set(report["session_ids"])) != 1:
            raise ValueError("Extraction and review used different sessions")
        result = ops.commit_slice_extraction(source_id, part["id"])
        candidate = result.get("candidate")
        report["candidate_count"] = int(bool(candidate))
        report["review_actions"] = [{"action": "commit", "result": result}]
        if candidate:
            report["review_actions"].append({"action": "pending_human_review", "candidate": candidate})
        report["precheck"] = result.get("diagnostics") or []
        report["raw_model_output_pass"] = not result.get("empty") and not result.get("errors")
        report["status"] = "awaiting_human_review" if candidate else (
            "passed" if report["raw_model_output_pass"] else "failed")
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        message = str(exc)
        for key_name in (settings.key_env, "DEEPSEEK_API_KEY", "ARK_API_KEY"):
            key = os.environ.get(key_name)
            if key:
                message = message.replace(key, "[REDACTED]")
        report["error"] = message
        report["precheck"].append("Real acceptance incomplete; no automatic retry")
    finally:
        HarnessRunner.agent_turn = original
        directory = layout(ops.project_info().path).extraction
        requests = []
        for path in sorted((directory / "conversation" / "turns").glob("*/metadata.json")):
            metadata = json.loads(path.read_text(encoding="utf-8"))
            report["usage"].append(metadata.get("usage"))
            report["finish_reasons"].append(metadata.get("finish_reason"))
            requests.extend(metadata.get("model_requests") or [])
        if not requests and not report["phases"] and report.get("error_type") == "TransportClosedError":
            report["provider_request_count"] = 0
        report["evidence_directory"] = str(directory)
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
