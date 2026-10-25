"""Configure a freshly initialized Microsoft GraphRAG 3.3 standard run.

Execute with the isolated GraphRAG interpreter after ``graphrag init``. The
cloud key is resolved from GRAPHRAG_API_KEY only at runtime, never saved here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import yaml

OLLAMA_BGE_M3_F16_BLOB = "daec91ffb5dd0c27411bd71f29932917c49cf529a641d0168496c3a501e3062c"
OLLAMA_QWEN_IQ3_BLOB = "c0b7c3038681ed2e3040456c1dd45f9858b6c2290bed172c70388a94874f3eee"
LOCAL_OLLAMA_BASE = "http://127.0.0.1:11434/v1"


def verify_local_model(model: str, expected_blob: str) -> None:
    model_root = Path(os.environ.get("OLLAMA_MODELS", Path.home() / ".ollama" / "models"))
    name, tag = model.split(":", 1)
    manifest_path = model_root / "manifests" / "registry.ollama.ai" / "library" / name / tag
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Local model manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    blobs = [layer["digest"] for layer in manifest["layers"] if layer["mediaType"] == "application/vnd.ollama.image.model"]
    if blobs != [f"sha256:{expected_blob}"]:
        raise ValueError(f"Local {model} blob changed; build a fresh, versioned index")


def verify_local_embedding() -> None:
    verify_local_model("bge-m3:latest", OLLAMA_BGE_M3_F16_BLOB)


def verify_local_completion() -> None:
    verify_local_model("qwen3.8:iq3-mtp3", OLLAMA_QWEN_IQ3_BLOB)


def configure(root: Path, model: str) -> dict:
    verify_local_embedding()
    verify_local_completion()
    if model != "qwen3.8:iq3-mtp3":
        raise ValueError("Only the verified installed local completion model is supported")
    settings_path = root / "settings.yaml"
    if not settings_path.is_file() or not (root / "narrative-source-map.json").is_file():
        raise ValueError("Initialize GraphRAG after staging Operations sources first")
    manifest_path = root / "narrative-graphrag-config.json"
    if manifest_path.exists():
        raise FileExistsError("GraphRAG configuration already recorded")
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    settings["concurrent_requests"] = 1
    completion = settings["completion_models"]["default_completion_model"]
    completion.update({
        "model_provider": "openai",
        "model": model,
        "api_base": "${GRAPHRAG_API_BASE}",
        "api_key": "${GRAPHRAG_API_KEY}",
        "retry": None,
        "call_args": {"max_tokens": 5000, "num_retries": 0, "temperature": 0},
    })
    embedding = settings["embedding_models"]["default_embedding_model"]
    embedding.update({
        "model_provider": "ollama",
        "model": "bge-m3",
        "api_base": "http://127.0.0.1:11434",
        "api_key": "ollama",
        "retry": None,
    })
    settings["extract_graph"]["entity_types"] = ["person", "organization", "geo", "event", "item"]
    settings["extract_graph"]["max_gleanings"] = 0
    settings["extract_claims"]["enabled"] = True
    settings["extract_claims"]["max_gleanings"] = 0
    settings_path.write_text(yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8")
    record = {
        "schema": "microsoft-graphrag-config/1",
        "graphrag_version": "3.3.0",
        "method": "standard",
        "completion_provider": "local Ollama OpenAI-compatible endpoint",
        "completion_model": model,
        "completion_api_base": LOCAL_OLLAMA_BASE,
        "completion_blob_sha256": OLLAMA_QWEN_IQ3_BLOB,
        "max_completion_tokens_per_request": 5000,
        "concurrent_requests": 1,
        "retry": None,
        "extract_graph_max_gleanings": 0,
        "extract_claims_enabled": True,
        "extract_claims_max_gleanings": 0,
        "embedding_provider": "local Ollama",
        "embedding_model": "bge-m3",
        "embedding_blob_sha256": OLLAMA_BGE_M3_F16_BLOB,
        "embedding_dimensions": 1024,
        "embedding_query_instruction": None,
        "chunk_size": settings["chunking"]["size"],
        "chunk_overlap": settings["chunking"]["overlap"],
        "chunk_encoding": settings["chunking"]["encoding_model"],
        "prompt_version": "microsoft-graphrag/3.3.0-stock-prompts",
        "prompt_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root / "prompts").glob("*.txt"))
        },
    }
    manifest_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--model", default="qwen3.8:iq3-mtp3")
    args = parser.parse_args()
    print(json.dumps(configure(args.run_root, args.model), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
