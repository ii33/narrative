"""Read-only boundary for Microsoft GraphRAG standard index exports.

GraphRAG output is a search hint. It never writes formal graph objects or
interprets GraphRAG's claim status as a reviewed project assertion.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

EXPORT_SCHEMA = "microsoft-graphrag-standard-export/1"


def preview_export(
    export_path: str | Path,
    source_map: dict[str, str],
    locate: Callable[[str, str], list[dict]],
) -> dict:
    """Preview an exported index with exact-quote eligibility checked by Operations.

    ``source_map`` maps GraphRAG document ids or titles to project source ids.
    Missing mappings and model-generated paraphrases remain search hints only.
    """
    data = json.loads(Path(export_path).read_text(encoding="utf-8"))
    if data.get("schema") != EXPORT_SCHEMA or data.get("method") != "standard":
        raise ValueError("Unsupported GraphRAG export schema or indexing method")
    tables = data.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("Missing GraphRAG tables")
    documents = {row["id"]: row for row in tables.get("documents", [])}
    text_units = {row["id"]: row for row in tables.get("text_units", [])}
    claims = []
    for row in tables.get("covariates", []):
        text_unit_id = row.get("text_unit_id")
        unit = text_units.get(text_unit_id, {})
        document = documents.get(unit.get("document_id"), {})
        source_id = source_map.get(str(document.get("id", ""))) or source_map.get(
            str(document.get("title", ""))
        )
        quote = row.get("source_text")
        matches = locate(source_id, quote) if source_id and isinstance(quote, str) and quote.strip() else []
        claims.append(
            {
                "id": row.get("id"),
                "subject": row.get("subject_id"),
                "object": row.get("object_id"),
                "description": row.get("description"),
                "model_status": row.get("status"),
                "text_unit_id": text_unit_id,
                "source_id": source_id,
                "source_quote": quote,
                "exact_quote_eligible": bool(matches),
                "locate_matches": matches,
            }
        )
    return {
        "schema": EXPORT_SCHEMA,
        "method": "standard",
        "graph_revision_changed": False,
        "formal_candidates_created": 0,
        "table_counts": {name: len(rows) for name, rows in tables.items()},
        "entities": tables.get("entities", []),
        "relationships": tables.get("relationships", []),
        "communities": tables.get("communities", []),
        "community_reports": tables.get("community_reports", []),
        "claims": claims,
    }
