"""Run a fixed GraphRAG query suite and record pass/fail evidence.

Each question is executed by ``graphrag_query.py`` in a separate process, so a
failure or timeout cannot poison the next query. Checks are deliberately
simple and literal: exit code, non-empty answer, required terms, forbidden
terms, and at least one ``[Data: ...]`` citation. This is a regression suite,
not an answer-quality score.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _run_one(
    *,
    root: Path,
    query: dict,
    out_dir: Path,
    timeout: int,
    venv: Path,
) -> dict:
    query_id = query["id"]
    out_path = out_dir / f"{query_id}.md"
    command = [
        sys.executable,
        str(Path(__file__).with_name("graphrag_query.py")),
        "--root",
        str(root),
        "--method",
        query["method"],
        "--query",
        query["query"],
        "--timeout",
        str(timeout),
        "--venv",
        str(venv),
        "--out",
        str(out_path),
    ]
    started = datetime.now(UTC).isoformat()
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 60)
    stdout = completed.stdout.strip().splitlines()
    payload = {}
    for line in reversed(stdout):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    answer = out_path.read_text(encoding="utf-8") if out_path.is_file() else ""
    metadata_path = out_path.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    missing = [term for term in query.get("must_include", []) if term not in answer]
    forbidden = [term for term in query.get("must_exclude", []) if term in answer]
    citations = len(re.findall(r"\[Data:\s*[^\]]+\]", answer))
    passed = (
        completed.returncode == 0
        and bool(answer.strip())
        and not missing
        and not forbidden
        and citations > 0
    )
    return {
        "id": query_id,
        "method": query["method"],
        "query": query["query"],
        "started_utc": started,
        "returncode": completed.returncode,
        "duration_seconds": metadata.get("duration_seconds"),
        "characters": len(answer),
        "citations": citations,
        "missing_terms": missing,
        "forbidden_terms": forbidden,
        "passed": passed,
        "answer_path": str(out_path),
        "metadata_path": str(metadata_path) if metadata_path.is_file() else None,
        "stderr_tail": completed.stderr[-1000:],
        "wrapper": payload,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path(__file__).with_name("graphrag_query_suite.json"),
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--venv", type=Path, default=Path(".venv-graphrag"))
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    queries = suite.get("queries") or []
    if not queries:
        print(json.dumps({"event": "error", "error": "suite has no queries"}, ensure_ascii=False))
        return 2
    out_dir = (args.out_dir or root / f"query-suite-{_utc_stamp()}").resolve()
    out_dir.mkdir(parents=True, exist_ok=False)

    results = []
    for query in queries:
        results.append(
            _run_one(
                root=root,
                query=query,
                out_dir=out_dir,
                timeout=args.timeout,
                venv=args.venv.resolve(),
            )
        )
        print(json.dumps({k: results[-1][k] for k in ("id", "passed", "duration_seconds")}, ensure_ascii=False))

    summary = {
        "schema": "microsoft-graphrag-query-suite-result/1",
        "root": str(root),
        "suite": str(args.suite.resolve()),
        "started_utc": results[0]["started_utc"] if results else None,
        "finished_utc": datetime.now(UTC).isoformat(),
        "passed": sum(1 for row in results if row["passed"]),
        "total": len(results),
        "results": results,
    }
    (out_dir / "results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# GraphRAG query suite",
        "",
        f"- root: `{root}`",
        f"- passed: **{summary['passed']}/{summary['total']}**",
        "",
        "| id | method | passed | seconds | chars | citations | missing | forbidden |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in results:
        lines.append(
            f"| {row['id']} | {row['method']} | {'yes' if row['passed'] else 'no'} | "
            f"{row['duration_seconds']} | {row['characters']} | {row['citations']} | "
            f"{', '.join(row['missing_terms']) or '-'} | {', '.join(row['forbidden_terms']) or '-'} |"
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"event": "finished", "passed": summary["passed"], "total": summary["total"], "out_dir": str(out_dir)}, ensure_ascii=False))
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
