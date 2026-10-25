"""Check indexed retrieval hits against GraphRAG text-unit provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


def check(root: Path, vector_report: str, report_name: str) -> dict:
    output = root / "output"
    entities = pd.read_parquet(output / "entities.parquet").set_index("id")
    relationships = pd.read_parquet(output / "relationships.parquet")
    units = pd.read_parquet(output / "text_units.parquet").set_index("id")
    documents = pd.read_parquet(output / "documents.parquet").set_index("id")
    manifest = json.loads((root / "narrative-source-map.json").read_text(encoding="utf-8"))
    source_by_file = {row["input_file"]: row for row in manifest["sources"]}
    for row in manifest["sources"]:
        if hashlib.sha256((root / "input" / row["input_file"]).read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("Staged source hash changed")
    retrieval = json.loads((root / vector_report).read_text(encoding="utf-8"))
    results = []
    for row in retrieval["queries"]:
        entity = entities.loc[row["entity_id"]]
        provenance_ids = set(entity["text_unit_ids"])
        retrieved_ids = set(row["top5_text_unit_ids"])
        matching = provenance_ids & retrieved_ids
        mapped_sources = set()
        for unit_id in matching:
            unit = units.loc[unit_id]
            document = documents.loc[unit["document_id"]]
            source = source_by_file.get(document["title"])
            if source is not None:
                mapped_sources.add(source["source_id"])
        results.append({
            "entity_id": row["entity_id"],
            "entity_rank_top5": row["entity_rank_top5"],
            "entity_source_unit_count": len(provenance_ids),
            "retrieved_top5_source_unit_overlap": len(matching),
            "mapped_source_count_for_overlap": len(mapped_sources),
            "entity_title_is_ascii": str(entity["title"]).isascii(),
            "literal_title_mentions_in_top5": row["unit_top5_mention_count"],
        })
    valid_unit_ids = set(units.index)
    relation_with_provenance = sum(bool(set(item) & valid_unit_ids) for item in relationships["text_unit_ids"])
    result = {
        "schema": "microsoft-graphrag-vector-provenance/1",
        "queries": results,
        "relations_total": len(relationships),
        "relations_with_indexed_text_unit": relation_with_provenance,
        "note": "GraphRAG text-unit provenance is a chunk link, not an exact quote or a reviewed fact.",
    }
    path = root / report_name
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--vector-report", default="narrative-graphrag-vector-acceptance.json")
    parser.add_argument("--report-name", default="narrative-graphrag-vector-provenance.json")
    args = parser.parse_args()
    print(json.dumps(check(args.root, args.vector_report, args.report_name), ensure_ascii=False, indent=2))
