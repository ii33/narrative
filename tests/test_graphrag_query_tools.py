"""Deterministic checks for the GraphRAG query wrapper's log parser."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_metrics_parser_extracts_each_provider_block():
    module = _load("graphrag_query")
    text = (
        "2026-10-20 01:00:00 - INFO - x - Metrics for openai/model-a: {\n"
        '  "attempted_request_count": 2,\n'
        '  "prompt_tokens": 10,\n'
        '  "total_tokens": 12\n'
        "}\n"
        "2026-10-20 01:00:01 - INFO - x - Metrics for ollama/bge-m3: {\n"
        '  "attempted_request_count": 1,\n'
        '  "prompt_tokens": 8,\n'
        '  "total_tokens": 8\n'
        "}\n"
    )
    rows = module._metrics(text)
    assert [row["provider"] for row in rows] == ["openai/model-a:", "ollama/bge-m3:"]
    assert [row["prompt_tokens"] for row in rows] == [10, 8]


def test_metrics_parser_ignores_text_without_blocks():
    module = _load("graphrag_query")
    assert module._metrics("") == []
    assert module._metrics("no metrics here") == []
