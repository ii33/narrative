"""Switch one already initialized, unrun index from Ark to pinned local Ollama."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from graphrag_configure import LOCAL_OLLAMA_BASE, OLLAMA_QWEN_IQ3_BLOB, verify_local_completion


def switch(root: Path) -> None:
    verify_local_completion()
    record_path = root / "narrative-graphrag-config.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("completion_provider") != "Ark Agent Plan OpenAI-compatible endpoint":
        raise ValueError("Expected the unrun Ark configuration")
    if (root / "output").exists() or (root / "narrative-graphrag-run.json").exists():
        raise ValueError("Index already started; make a new isolated root")
    settings_path = root / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    completion = settings["completion_models"]["default_completion_model"]
    if completion.get("model") != "deepseek-v4.1-flash":
        raise ValueError("Unexpected previous completion model")
    completion.update({
        "model_provider": "openai",
        "model": "qwen3.8:iq3-mtp3",
        "api_base": "${GRAPHRAG_API_BASE}",
        "api_key": "${GRAPHRAG_API_KEY}",
        "retry": None,
        "call_args": {"max_tokens": 5000, "num_retries": 0, "temperature": 0},
    })
    record.update({
        "completion_provider": "local Ollama OpenAI-compatible endpoint",
        "completion_model": "qwen3.8:iq3-mtp3",
        "completion_api_base": LOCAL_OLLAMA_BASE,
        "completion_blob_sha256": OLLAMA_QWEN_IQ3_BLOB,
    })
    settings_path.write_text(yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8")
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    switch(parser.parse_args().root)
