"""Export official GraphRAG 3.3 standard Parquet tables for Operations preview.

Run with the isolated GraphRAG interpreter after indexing completes. This script
does not invoke a model, perform a query, or modify the GraphRAG index.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

SCHEMA = "microsoft-graphrag-standard-export/1"
FIELDS = {
    "documents": ("id", "title"),
    "text_units": ("id", "document_id"),
    "entities": ("id", "title", "type", "description", "text_unit_ids"),
    "relationships": ("id", "source", "target", "description", "text_unit_ids"),
    "communities": ("id", "community", "level", "title", "entity_ids", "relationship_ids"),
    "community_reports": ("id", "community", "level", "title", "summary", "findings"),
    "covariates": (
        "id", "subject_id", "object_id", "description", "status", "source_text", "text_unit_id"
    ),
}
REQUIRED = frozenset(FIELDS) - {"covariates"}


def export(index_root: Path) -> dict:
    output_dir = index_root / "output"
    if not output_dir.is_dir():
        raise ValueError(f"GraphRAG output directory missing: {output_dir}")
    tables = {}
    for name, fields in FIELDS.items():
        path = output_dir / f"{name}.parquet"
        if not path.is_file():
            if name in REQUIRED:
                raise ValueError(f"Incomplete standard index: {name}.parquet missing")
            tables[name] = []
            continue
        frame = pd.read_parquet(path)
        missing = set(fields) - set(frame.columns)
        if missing:
            raise ValueError(f"{name}.parquet missing fields: {sorted(missing)}")
        tables[name] = json.loads(frame.loc[:, list(fields)].to_json(orient="records", force_ascii=False))
    return {"schema": SCHEMA, "method": "standard", "tables": tables}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("index_root", type=Path)
    parser.add_argument("export_json", type=Path)
    args = parser.parse_args()
    if args.export_json.exists():
        raise FileExistsError(f"Refusing to overwrite: {args.export_json}")
    payload = export(args.index_root)
    args.export_json.parent.mkdir(parents=True, exist_ok=True)
    args.export_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"schema": SCHEMA, "table_counts": {k: len(v) for k, v in payload["tables"].items()}}))


if __name__ == "__main__":
    main()
