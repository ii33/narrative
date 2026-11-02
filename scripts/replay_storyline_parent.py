"""Replay the original first submission unchanged in a new isolated project; no model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from narrative import tool_api
from narrative.operations import Operations
from narrative.project_package import layout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ["NARRATIVE_PROVIDER_DB"] = str(args.output / "providers.sqlite3")
    raw = (args.input / "tool-calls.json").read_bytes()
    calls = json.loads(raw)
    original = next(c for c in calls if c["name"] == "submit_extraction")["arguments"]
    params = json.loads(original)
    text = (args.input / "input.txt").read_bytes().decode("utf-8")
    ops = Operations.create_graph_project(args.output / "原样回放.nproj")
    sid = ops.import_source("合成告别启程与抵达入城原文", text)
    assert sid == params.get("source_id", sid), "No ID rewriting is permitted in raw replay"
    outcome = tool_api.call(
        "submit_extraction", params, project=layout(ops.project_info().path).entry
    )
    check = ops.check_project()
    graph = ops.graph_snapshot()
    duplicates = {
        n["id"]: n.get("events", [])
        for n in graph["nodes"]
        if len(n.get("events", [])) != len(set(n.get("events", [])))
    }
    report = {
        "engineering_replay_only": True,
        "provider_requests": 0,
        "original_arguments_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "source_tool_calls_sha256": hashlib.sha256(raw).hexdigest(),
        "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "extraction_sha256": hashlib.sha256(
            Path("src/narrative/extraction.py").read_bytes()
        ).hexdigest(),
        "outcome": outcome,
        "static_check": check,
        "duplicate_children": duplicates,
        "passed": outcome["status"] == "accepted"
        and not duplicates
        and not any(i["severity"] == "error" for i in check["issues"]),
    }
    (args.output / "unchanged-arguments.txt").write_bytes(original.encode())
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "snapshot.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
