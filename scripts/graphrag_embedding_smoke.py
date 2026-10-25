"""Check local GraphRAG dense embeddings against a synthetic retrieval fixture.

Run with the isolated Microsoft GraphRAG environment:
    .venv-graphrag/Scripts/python.exe scripts/graphrag_embedding_smoke.py
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from graphrag_llm.config.model_config import ModelConfig
from graphrag_llm.embedding.embedding_factory import create_embedding

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "graphrag_retrieval_benchmark.json"
MODEL_BLOB_SHA256 = "daec91ffb5dd0c27411bd71f29932917c49cf529a641d0168496c3a501e3062c"


def main() -> int:
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    chunks = fixture["chunks"]
    queries = fixture["queries"]
    texts = [row["text"] for row in chunks] + [row["query"] for row in queries]
    model = create_embedding(
        ModelConfig(
            model_provider="ollama",
            model="bge-m3",
            api_base="http://127.0.0.1:11434",
            api_key="ollama",
            retry=None,
        )
    )
    response = model.embedding(input=texts)
    vectors = [row.embedding for row in sorted(response.data, key=lambda row: row.index)]
    if len(vectors) != len(texts) or any(len(vector) != 1024 for vector in vectors):
        raise RuntimeError("Unexpected local embedding count or dimensionality")

    def cosine(a: list[float], b: list[float]) -> float:
        norm_a = math.sqrt(sum(value * value for value in a))
        norm_b = math.sqrt(sum(value * value for value in b))
        return sum(x * y for x, y in zip(a, b, strict=True)) / (norm_a * norm_b)

    outcomes = []
    for offset, query in enumerate(queries):
        query_vector = vectors[len(chunks) + offset]
        ranking = sorted(
            ((chunk["id"], cosine(query_vector, vector)) for chunk, vector in zip(chunks, vectors, strict=False)),
            key=lambda item: item[1],
            reverse=True,
        )
        top_five = [chunk_id for chunk_id, _ in ranking[:5]]
        outcomes.append(
            {
                "query_id": query["id"],
                "category": query["category"],
                "gold_chunk_ids": query["gold_chunk_ids"],
                "top_five": top_five,
                "all_gold_in_top_five": set(query["gold_chunk_ids"]).issubset(top_five),
            }
        )
    report = {
        "provider": "local Ollama",
        "model": "bge-m3",
        "model_blob_sha256": MODEL_BLOB_SHA256,
        "dimensions": 1024,
        "query_instruction": None,
        "embedding_count": len(vectors),
        "norm_range": [
            min(math.sqrt(sum(value * value for value in vector)) for vector in vectors),
            max(math.sqrt(sum(value * value for value in vector)) for vector in vectors),
        ],
        "passed_queries": sum(row["all_gold_in_top_five"] for row in outcomes),
        "total_queries": len(outcomes),
        "outcomes": outcomes,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
