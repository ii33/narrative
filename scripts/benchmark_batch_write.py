"""Offline SQL/call benchmark; synthetic data only, no model requests."""

from __future__ import annotations

import argparse
import cProfile
import json
import os
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from agent_fixtures import TEXT, draft_result

from narrative.graph_store import GraphStore
from narrative.operations import Operations


def measure(operation):
    calls, sql = Counter(), Counter()
    original = GraphStore.connect

    @contextmanager
    def connect(store, *, write=False):
        calls["graph_connect_write" if write else "graph_connect_read"] += 1
        with original(store, write=write) as db:

            def trace(statement):
                parts = statement.strip().split()
                if parts:
                    sql[parts[0].upper()] += 1

            db.set_trace_callback(trace)
            yield db

    profile = cProfile.Profile()
    started = time.perf_counter()
    with patch.object(GraphStore, "connect", connect):
        profile.enable()
        result = operation()
        profile.disable()
    elapsed = time.perf_counter() - started
    for entry in profile.getstats():
        code = entry.code
        if not isinstance(code, str) and Path(code.co_filename).name in {
            "batch_write.py",
            "temporal_state.py",
        }:
            calls[Path(code.co_filename).stem + "." + code.co_name] += entry.callcount
    return {"seconds_profiled": elapsed, "calls": dict(calls), "sql": dict(sql), "result": result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ["NARRATIVE_PROVIDER_DB"] = str(args.output / "providers.sqlite3")
    results = []
    for count, chars in [(0, 0), (1000, 0), (1000, 2000000)]:
        ops = Operations.create_graph_project(args.output / f"n{count}-chars{chars}.nproj")
        sid = ops.import_source("批次原文", TEXT)
        if chars:
            ops.import_source("无关原文", "背" * chars)
        if count:
            nodes = [
                {
                    "kind": "node",
                    "id": f"P{i + 100}",
                    "node_type": "person",
                    "name": f"背景{i}",
                    "context_id": "canon",
                    "disclosure": {"source_id": sid, "start": 0},
                    "evidence": {"source_id": sid, "quote": TEXT},
                }
                for i in range(count)
            ]
            seeded = ops.apply_changes(
                nodes, [], ops.graph_project_info()["revision"], reason="离线规模样本"
            )
            assert seeded["status"] == "accepted", seeded
        draft = draft_result()
        preview = measure(lambda ops=ops, sid=sid, draft=draft: ops.preview_extraction(sid, draft))
        submitted = measure(lambda ops=ops, sid=sid, draft=draft: ops.submit_extraction(sid, draft))
        assert submitted["result"]["status"] == "accepted", submitted
        replay = ops.submit_extraction(sid, draft)
        assert replay["status"] == "unchanged", replay
        results.append(
            {
                "existing_persons": count,
                "unrelated_source_characters": chars,
                "batch_nodes": 6,
                "batch_properties": 2,
                "preview": preview,
                "submit": submitted,
                "input_characters": len(json.dumps(draft, ensure_ascii=False)),
                "submit_output_characters": len(
                    json.dumps(submitted["result"], ensure_ascii=False)
                ),
            }
        )
    baseline = json.loads(
        Path("artifacts/batch-write-audit-20261102/results.json").read_text(encoding="utf-8")
    )
    (args.output / "results.json").write_text(
        json.dumps(
            {"engineering_only": True, "baseline": baseline, "current": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            [
                {
                    "n": r["existing_persons"],
                    "chars": r["unrelated_source_characters"],
                    "submit": r["submit"],
                }
                for r in results
            ],
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
