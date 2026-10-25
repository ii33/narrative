"""Read-only local dense retrieval checks for an official standard index."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

import lancedb
import pandas as pd
from graphrag_configure import verify_local_embedding


def embed(text: str) -> list[float]:
    request = Request(
        "http://127.0.0.1:11434/api/embed",
        data=json.dumps({"model": "bge-m3", "input": [text]}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with build_opener(ProxyHandler({})).open(request, timeout=120) as response:
        vector = json.loads(response.read())["embeddings"][0]
    if len(vector) != 1024 or not all(math.isfinite(value) for value in vector):
        raise ValueError("Unexpected local BGE-M3 dense vector")
    return vector


def check(root: Path, report_name: str) -> dict:
    verify_local_embedding()
    index_report = root / "narrative-graphrag-resume-4.json"
    if not index_report.is_file():
        index_report = root / "narrative-graphrag-resume-3.json"
    if not index_report.is_file():
        raise ValueError("Completed standard run report missing")
    report = json.loads(index_report.read_text(encoding="utf-8"))
    if report["real_acceptance"] != "complete":
        raise ValueError("Official standard index incomplete")
    local_tmp = (root / "local-tmp").resolve()
    local_tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = os.environ["TEMP"] = os.environ["TMPDIR"] = str(local_tmp)
    output = root / "output"
    entities = pd.read_parquet(output / "entities.parquet")
    relationships = pd.read_parquet(output / "relationships.parquet")
    units = pd.read_parquet(output / "text_units.parquet")
    degrees = Counter(relationships["source"].tolist() + relationships["target"].tolist())
    chosen = entities.loc[entities["title"].isin(degrees)].copy()
    chosen["degree"] = chosen["title"].map(degrees)
    chosen = chosen.sort_values(["degree", "title"], ascending=[False, True]).head(3)
    db = lancedb.connect(str(output / "lancedb"))
    tables = {name: db.open_table(name) for name in ("entity_description", "community_full_content", "text_unit_text")}
    rows = {name: table.count_rows() for name, table in tables.items()}
    if rows != {"entity_description": len(entities), "community_full_content": len(pd.read_parquet(output / "community_reports.parquet")), "text_unit_text": len(units)}:
        raise ValueError("Dense vector table counts do not match standard outputs")
    queries = []
    for _, entity in chosen.iterrows():
        query = f"关于{entity['title']}的经历和关系"
        vector = embed(query)
        entity_hits = tables["entity_description"].search(vector).limit(5).to_list()
        unit_hits = tables["text_unit_text"].search(vector).limit(5).to_list()
        expected_id = str(entity["id"])
        entity_rank = next((index + 1 for index, row in enumerate(entity_hits) if str(row["id"]) == expected_id), None)
        units_by_id = units.set_index("id")
        mention_count = sum(
            str(entity["title"]) in str(units_by_id.loc[row["id"], "text"])
            for row in unit_hits if row["id"] in units_by_id.index
        )
        queries.append({
            "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "entity_id": expected_id,
            "entity_degree": int(entity["degree"]),
            "entity_rank_top5": entity_rank,
            "unit_top5_mention_count": mention_count,
            "top5_entity_ids": [str(row["id"]) for row in entity_hits],
            "top5_text_unit_ids": [str(row["id"]) for row in unit_hits],
            "embedding_dimensions": len(vector),
            "embedding_l2_norm": round(math.sqrt(sum(value * value for value in vector)), 6),
        })
    result = {
        "schema": "microsoft-graphrag-vector-acceptance/1",
        "provider": "local Ollama bge-m3",
        "vector_table_rows": rows,
        "queries": queries,
    }
    path = root / report_name
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--report-name", default="narrative-graphrag-vector-acceptance.json")
    args = parser.parse_args()
    print(json.dumps(check(args.root, args.report_name), ensure_ascii=False, indent=2))
