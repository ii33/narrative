"""Run one bounded GraphRAG query and record its output and metrics.

The official CLI is invoked from the isolated environment; this wrapper only
supplies the local endpoint environment and captures the result. It never
prints or stores the API key, and it does not retry.

Example:

    .venv-graphrag\\Scripts\\python.exe scripts/graphrag_query.py \
        --root data/ms-graphrag-threechapters-20261018/standard-run \
        --method local --query "林舟为什么被传送到火星？"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _tail(path: Path, offset: int) -> str:
    if not path.is_file():
        return ""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        return handle.read()


def _metrics(text: str) -> list[dict]:
    """Extract the JSON metric blocks GraphRAG appends to logs/query.log."""
    rows = []
    marker = "Metrics for "
    cursor = 0
    while True:
        start = text.find(marker, cursor)
        if start < 0:
            break
        brace = text.find("{", start)
        if brace < 0:
            break
        depth = 0
        end = None
        for index in range(brace, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end is None:
            break
        header = text[start:brace].strip()
        try:
            rows.append({"provider": header.removeprefix(marker).strip(), **json.loads(text[brace:end])})
        except json.JSONDecodeError:
            pass
        cursor = end
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--query", required=True)
    parser.add_argument("--method", default="local", choices=("local", "global", "drift", "basic"))
    parser.add_argument("--venv", type=Path, default=Path(".venv-graphrag"))
    parser.add_argument("--api-base", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--api-key", default="ollama")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--out", type=Path, default=None, help="Markdown output path")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    executable = (args.venv / "Scripts" / "graphrag.exe").resolve()
    if not executable.is_file():
        print(json.dumps({"event": "error", "error": f"graphrag.exe not found: {executable}"}, ensure_ascii=False))
        return 2

    log_path = root / "logs" / "query.log"
    log_offset = log_path.stat().st_size if log_path.is_file() else 0
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = args.out or root / f"narrative-graphrag-query-{args.method}-{stamp}.md"
    meta_path = out_path.with_suffix(".json")

    environment = os.environ.copy()
    environment["GRAPHRAG_API_BASE"] = args.api_base
    environment["GRAPHRAG_API_KEY"] = args.api_key
    # Avoid LiteLLM's remote price-map fetch; this deployment is local-only.
    environment.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

    command = [str(executable), "query", args.query, "--root", str(root), "--method", args.method]
    started = _utc_now()
    clock = time.monotonic()
    timed_out = False
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=args.timeout, env=environment
        )
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code = 124

    duration = round(time.monotonic() - clock, 3)
    out_path.write_text(stdout, encoding="utf-8")
    appended = _tail(log_path, log_offset)
    metadata = {
        "schema": "microsoft-graphrag-query/1",
        "query": args.query,
        "query_sha256": hashlib.sha256(args.query.encode("utf-8")).hexdigest(),
        "method": args.method,
        "root": str(root),
        "started_utc": started,
        "ended_utc": _utc_now(),
        "duration_seconds": duration,
        "exit_code": code,
        "timed_out": timed_out,
        "output_path": str(out_path),
        "output_characters": len(stdout),
        "stderr_tail": stderr[-2000:],
        "api_base": args.api_base,
        "metrics": _metrics(appended),
        "log_tail": appended[-4000:],
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "event": "finished",
                "method": args.method,
                "exit_code": code,
                "duration_seconds": duration,
                "output": str(out_path),
                "metadata": str(meta_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
