"""Read-only readiness check for the Microsoft GraphRAG sidecar.

Run with the GraphRAG environment interpreter, for example:

    .venv-graphrag\\Scripts\\python.exe scripts/graphrag_env_check.py \
        --root data/ms-graphrag-threechapters-20261018/standard-run

It checks the interpreter, package version, settings placeholders, index
tables, LanceDB directories and the local Ollama models. It never starts a
model request and never prints secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

TABLES = (
    "documents",
    "text_units",
    "entities",
    "relationships",
    "communities",
    "community_reports",
    "covariates",
)
VECTOR_DIRS = ("entity_description", "community_full_content", "text_unit_text")


def _parquet_rows(path: Path) -> int | None:
    try:
        import pyarrow.parquet as pq

        return pq.read_metadata(path).num_rows
    except Exception:
        try:
            import pandas as pd

            return int(len(pd.read_parquet(path)))
        except Exception:
            return None


def _ollama_models(base: str) -> tuple[list[str], str | None]:
    url = base.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return [row.get("name", "") for row in payload.get("models", [])], None
    except Exception as exc:  # noqa: BLE001 - report the failure verbatim
        return [], f"{type(exc).__name__}: {exc}"


def check(root: Path, venv: Path) -> dict:
    checks: dict[str, dict] = {}
    python = venv / "Scripts" / "python.exe"
    checks["interpreter"] = {"path": str(python), "exists": python.is_file()}

    version = None
    if python.is_file():
        result = subprocess.run(
            [str(python), "-c", "import importlib.metadata as m; print(m.version('graphrag'))"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
        else:
            checks["interpreter"]["error"] = (result.stderr or "").strip()[-500:]
    checks["graphrag_version"] = {"value": version, "expected": "3.3.0", "ok": version == "3.3.0"}

    settings_path = root / "settings.yaml"
    settings_text = settings_path.read_text(encoding="utf-8") if settings_path.is_file() else ""
    checks["settings"] = {
        "path": str(settings_path),
        "exists": settings_path.is_file(),
        "uses_api_base_placeholder": "${GRAPHRAG_API_BASE}" in settings_text,
        "uses_api_key_placeholder": "${GRAPHRAG_API_KEY}" in settings_text,
    }

    tables = {}
    for name in TABLES:
        path = root / "output" / f"{name}.parquet"
        tables[name] = {"exists": path.is_file(), "rows": _parquet_rows(path) if path.is_file() else None}
    checks["tables"] = tables

    vectors = {}
    for name in VECTOR_DIRS:
        path = root / "output" / "lancedb" / f"{name}.lance"
        vectors[name] = {"exists": path.is_dir()}
    checks["vector_dirs"] = vectors

    models: list[str] = []
    ollama_error = None
    checks["ollama"] = {"error": "settings.yaml missing"}
    if settings_path.is_file():
        try:
            import yaml

            settings = yaml.safe_load(settings_text) or {}
            completion = (
                (settings.get("completion_models") or {})
                .get("default_completion_model", {})
                .get("model")
            )
            embedding = (
                (settings.get("embedding_models") or {})
                .get("default_embedding_model", {})
                .get("model")
            )
            base = "http://127.0.0.1:11434"
            models, ollama_error = _ollama_models(base)
            checks["ollama"] = {
                "base": base,
                "completion_model": completion,
                "embedding_model": embedding,
                "completion_present": completion in models if models else None,
                "embedding_present": f"{embedding}:latest" in models or embedding in models if models else None,
                "error": ollama_error,
            }
        except Exception as exc:  # noqa: BLE001
            checks["ollama"] = {"error": f"{type(exc).__name__}: {exc}"}

    prompts = root / "prompts"
    checks["prompts"] = {
        "dir": str(prompts),
        "txt_files": len(list(prompts.glob("*.txt"))) if prompts.is_dir() else 0,
    }

    env_file = root / ".env"
    checks["secrets"] = {
        ".env_present": env_file.is_file(),
        "GRAPHRAG_API_KEY_in_process": bool(os.environ.get("GRAPHRAG_API_KEY")),
        "GRAPHRAG_API_BASE_in_process": bool(os.environ.get("GRAPHRAG_API_BASE")),
    }

    required_ok = (
        checks["interpreter"]["exists"]
        and checks["graphrag_version"]["ok"]
        and checks["settings"]["exists"]
        and all(row["exists"] for row in tables.values())
        and all(row["exists"] for row in vectors.values())
        and checks["ollama"].get("completion_present") is True
        and checks["ollama"].get("embedding_present") is True
    )
    return {"root": str(root), "venv": str(venv), "ready": required_ok, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--venv", type=Path, default=Path(".venv-graphrag"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = check(args.root.resolve(), args.venv.resolve())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"root: {report['root']}")
        print(f"ready: {report['ready']}")
        for name, value in report["checks"].items():
            print(f"- {name}: {json.dumps(value, ensure_ascii=False)}")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
