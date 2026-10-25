"""Record a human review decision for one property candidate.

The decision is explicit on the command line (``--action accept|reject``).
Nothing is accepted by default, and the original model records are never
rewritten: accepting appends them to the authoritative history; rejecting
keeps the candidate as a rejected record.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from narrative.project_catalog import open_graph_project


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--action", required=True, choices=("accept", "reject"))
    parser.add_argument("--version", type=int, default=None)
    parser.add_argument("--reviewer-note", default="")
    parser.add_argument("--output", type=Path, default=None, help="review.json 路径")
    args = parser.parse_args(argv)

    ops = open_graph_project(args.project)
    candidate = ops.property_candidate(args.candidate)
    version = args.version if args.version is not None else candidate["version"]
    records = candidate.get("records") or []
    diagnostics = ops.property_precheck(records, candidate.get("expected_revision"))
    errors = [row for row in diagnostics if row.get("severity") == "error"]

    result: dict = {
        "reviewed_utc": _utc_now(),
        "project": str(args.project),
        "candidate_id": args.candidate,
        "candidate_version": version,
        "model_record_count": len(records),
        "precheck_diagnostics": diagnostics,
        "precheck_has_errors": bool(errors),
        "action": args.action,
        "reviewer_note": args.reviewer_note,
    }

    if args.action == "accept":
        if errors:
            result["status"] = "blocked_by_precheck"
            _atomic_json(args.output or Path("review.json"), result)
            print(json.dumps(result, ensure_ascii=False))
            return 1
        result["revision_after_accept"] = ops.property_accept(args.candidate, version)
        result["post_accept"] = {
            "key_holder_at_transfer_end": ops.property_state(
                "key",
                "holder",
                {"timeline_id": "story", "form": "event_boundary",
                 "event_id": "e_transfer", "boundary": "end"},
            ),
            "key_holder_current": ops.property_state("key", "holder"),
            "linzhou_location": ops.property_state("linzhou", "location"),
            "key_history": ops.property_history("key", "holder"),
        }
        result["status"] = "accepted"
    else:
        ops.property_reject(args.candidate, version)
        result["status"] = "rejected"

    _atomic_json(args.output or Path("review.json"), result)
    print(json.dumps({key: result[key] for key in ("status", "candidate_id", "action")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
