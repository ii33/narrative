"""Check the completed sidecar through read-only GraphOperations preview."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from narrative.graph_operations import GraphOperations


def check(project: Path, root: Path, export_name: str, report_name: str) -> dict:
    ops = GraphOperations(project)
    before_revision = ops.graph_project_info()["revision"]
    before_candidates = len(ops.graph_candidates())
    export_path = root / export_name
    preview = ops.graphrag_preview_staged(root, export_path)
    claims = preview["claims"]
    mapped = sum(bool(row["source_id"]) for row in claims)
    exact = sum(row["exact_quote_eligible"] for row in claims)
    result = {
        "schema": "microsoft-graphrag-operations-acceptance/1",
        "table_counts": preview["table_counts"],
        "claims_total": len(claims),
        "claims_source_mapped": mapped,
        "claims_exact_quote_eligible": exact,
        "claims_unmapped": len(claims) - mapped,
        "claims_paraphrase_or_unlocated": mapped - exact,
        "formal_candidates_created": preview["formal_candidates_created"],
        "revision_before": before_revision,
        "revision_after": ops.graph_project_info()["revision"],
        "candidate_count_before": before_candidates,
        "candidate_count_after": len(ops.graph_candidates()),
    }
    if result["revision_before"] != result["revision_after"] or result["candidate_count_before"] != result["candidate_count_after"]:
        raise ValueError("Read-only GraphRAG preview changed reviewed project state")
    path = root / report_name
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("root", type=Path)
    parser.add_argument("--export-name", default="narrative-standard-export.json")
    parser.add_argument("--report-name", default="narrative-graphrag-operations-acceptance.json")
    args = parser.parse_args()
    print(json.dumps(check(args.project, args.root, args.export_name, args.report_name), ensure_ascii=False, indent=2))
