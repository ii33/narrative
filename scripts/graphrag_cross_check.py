"""Cross-check accepted graph nodes against a GraphRAG index (read-only).

GraphRAG is used as an adversarial recall checker, not as ground truth:

* node coverage: does GraphRAG see a compatible entity in the same text units?
* pair hints: for temporal ``before`` assertions, is there any GraphRAG
  relationship between the mapped entities? (GraphRAG has no event order.)
* claim hints: claims involving mapped entities, especially ``SUSPECTED`` ones.

Absence in GraphRAG is never treated as false. The output only surfaces items
for human review; it never writes formal graph objects.

Example:

    uv run python scripts/graphrag_cross_check.py \
        --project data/example-run/graph.sqlite3 \
        --graphrag-root data/ms-graphrag-threechapters-20261018/standard-run \
        --aliases examples/graphrag_aliases_sample.json \
        --out data/example-run/property-acceptance-20261020/cross-check
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from narrative.project_catalog import open_graph_project

TYPE_MAP = {
    "person": {"PERSON"},
    "item": {"ITEM"},
    "location": {"GEO", "LOCATION"},
    "event": {"EVENT"},
    "group": {"ORGANIZATION"},
    "scene": {"EVENT", "SCENE"},
    "storyline": {"ORGANIZATION", "EVENT"},
}


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().upper()


def _load_aliases(path: Path | None) -> dict[str, list[str]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("aliases file must be a JSON object")
    return {str(key): [str(item) for item in value] for key, value in payload.items()}


def _titles(node: dict, aliases: dict[str, list[str]]) -> set[str]:
    values = [node.get("name", ""), *(node.get("aliases") or [])]
    titles: set[str] = set()
    for value in values:
        if not value:
            continue
        titles.add(norm(value))
        titles.update(norm(item) for item in aliases.get(str(value), []))
    return {title for title in titles if title}


def _records(frame: pd.DataFrame) -> list[dict]:
    return frame.to_dict("records") if not frame.empty else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--graphrag-root", required=True, type=Path)
    parser.add_argument("--aliases", type=Path, default=None)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    ops = open_graph_project(args.project)
    snapshot = ops.graph_snapshot()
    nodes = {row["id"]: row for row in snapshot["nodes"]}
    predicates = {row["id"]: row for row in snapshot["predicates"]}
    evidences = {row["id"]: row for row in snapshot["evidences"]}
    links: dict[str, list[str]] = defaultdict(list)
    for link in snapshot["evidence_links"]:
        links[link["assertion_id"]].append(link["evidence_id"])
    assertions = snapshot["assertions"]
    aliases = _load_aliases(args.aliases)

    output = args.graphrag_root.resolve() / "output"
    units = pd.read_parquet(output / "text_units.parquet")
    entities = pd.read_parquet(output / "entities.parquet")
    relationships = pd.read_parquet(output / "relationships.parquet")
    claims = pd.read_parquet(output / "covariates.parquet")

    unit_texts = {row["id"]: str(row["text"]) for row in _records(units)}
    entity_by_id = {row["id"]: row for row in _records(entities)}

    quote_units: dict[str, list[str]] = {}
    unmapped_quotes: list[str] = []
    for quote in {row["quote"] for row in evidences.values()}:
        matched = [unit_id for unit_id, text in unit_texts.items() if quote and quote in text]
        quote_units[quote] = matched
        if not matched:
            unmapped_quotes.append(quote[:80])

    entities_by_unit: dict[str, set[str]] = defaultdict(set)
    for row in _records(entities):
        for unit_id in list(row["text_unit_ids"]):
            entities_by_unit[unit_id].add(row["id"])

    node_units: dict[str, set[str]] = defaultdict(set)
    for assertion in assertions:
        for node_id in (assertion.get("subject_id"), assertion.get("object_id")):
            if node_id not in nodes:
                continue
            for evidence_id in links.get(assertion["id"], []):
                quote = evidences[evidence_id]["quote"]
                node_units[node_id].update(quote_units.get(quote, []))

    # Accepted property records carry the evidence for persons, items and
    # locations that are not subjects of containment/temporal assertions.
    world = ops.property_world_state()
    for node_id, properties in (world.get("nodes") or {}).items():
        if node_id not in nodes:
            continue
        for property_id in properties:
            for record in ops.property_history(node_id, property_id):
                quote = (record.get("evidence") or {}).get("quote")
                if quote:
                    node_units[node_id].update(quote_units.get(quote, []))

    # Name-based units: the source text is Chinese even when GraphRAG titles
    # are romanized, so a literal Chinese name is a reliable unit locator.
    for node_id, node in nodes.items():
        names = [str(node.get("name") or ""), *(node.get("aliases") or [])]
        names = [name for name in names if name and not name.isascii()]
        for unit_id, text in unit_texts.items():
            if any(name in text for name in names):
                node_units[node_id].add(unit_id)

    node_rows = []
    for node_id, node in nodes.items():
        units_set = node_units.get(node_id, set())
        candidate_ids: set[str] = set()
        for unit_id in units_set:
            candidate_ids.update(entities_by_unit.get(unit_id, set()))
        wanted = _titles(node, aliases)
        matched = [entity_by_id[item] for item in candidate_ids if norm(entity_by_id[item]["title"]) in wanted]
        compatible = [
            entity_by_id[item]
            for item in candidate_ids
            if entity_by_id[item]["type"] in TYPE_MAP.get(node.get("node_type"), set())
        ]
        if matched:
            status = "matched"
        elif compatible:
            status = "unit_only"
        elif units_set:
            status = "missing"
        else:
            status = "no_evidence"
        node_rows.append(
            {
                "id": node_id,
                "name": node.get("name"),
                "node_type": node.get("node_type"),
                "status": status,
                "evidence_units": len(units_set),
                "matched_titles": [item["title"] for item in matched],
                "candidate_titles": [item["title"] for item in compatible[:8]],
            }
        )

    relationship_pairs = set()
    for row in _records(relationships):
        pair = (norm(row["source"]), norm(row["target"]))
        relationship_pairs.add(pair)
        relationship_pairs.add((pair[1], pair[0]))

    node_title_map = {node_id: _titles(node, aliases) for node_id, node in nodes.items()}
    pair_rows = []
    for assertion in assertions:
        semantic = predicates.get(assertion.get("predicate_id"), {}).get("semantic")
        if semantic != "temporal":
            continue
        subject = nodes.get(assertion.get("subject_id"))
        target = nodes.get(assertion.get("object_id"))
        if not subject or not target:
            continue
        subject_titles = node_title_map[subject["id"]]
        target_titles = node_title_map[target["id"]]
        present = sorted(
            {
                f"{left} -> {right}"
                for left in subject_titles
                for right in target_titles
                if (left, right) in relationship_pairs
            }
        )
        # GraphRAG has no event order, so a temporal pair is not a verdict.
        pair_rows.append(
            {
                "assertion_id": assertion["id"],
                "subject": subject["name"],
                "object": target["name"],
                "status": "not_comparable",
                "relations": present[:5],
            }
        )

    claim_rows = []
    for row in _records(claims):
        subject = norm(row.get("subject_id", ""))
        target = norm(row.get("object_id", ""))
        involved = [
            node["name"]
            for node_id, node in nodes.items()
            if subject in node_title_map[node_id] or target in node_title_map[node_id]
        ]
        if involved:
            claim_rows.append(
                {
                    "subject": row.get("subject_id"),
                    "object": row.get("object_id"),
                    "status": row.get("status"),
                    "involved_nodes": involved,
                    "description": str(row.get("description", ""))[:160],
                }
            )

    summary = {
        "nodes": len(node_rows),
        "node_status": dict(Counter(row["status"] for row in node_rows)),
        "node_status_by_type": {
            node_type: dict(Counter(row["status"] for row in node_rows if row["node_type"] == node_type))
            for node_type in sorted({row["node_type"] for row in node_rows})
        },
        "assertions": len(assertions),
        "temporal_pairs": len(pair_rows),
        "temporal_pair_status": dict(Counter(row["status"] for row in pair_rows)),
        "claims_involving_nodes": len(claim_rows),
        "claims_suspected": sum(1 for row in claim_rows if row.get("status") == "SUSPECTED"),
        "unmapped_quotes": len(unmapped_quotes),
    }
    payload = {
        "schema": "narrative-graphrag-cross-check/1",
        "created_utc": datetime.now(UTC).isoformat(),
        "project": str(args.project.resolve()),
        "graphrag_root": str(args.graphrag_root.resolve()),
        "aliases": str(args.aliases.resolve()) if args.aliases else None,
        "revision": snapshot.get("revision"),
        "summary": summary,
        "nodes": node_rows,
        "temporal_pairs": pair_rows,
        "claims": claim_rows,
        "unmapped_quotes": unmapped_quotes,
        "note": "GraphRAG absence is not false; this report only surfaces review hints and never writes formal graph objects.",
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "cross-check.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# GraphRAG cross-check (read-only)",
        "",
        f"- project: `{args.project}`",
        f"- index: `{args.graphrag_root}`",
        f"- revision: {snapshot.get('revision')}",
        f"- nodes: {summary['nodes']}  {summary['node_status']}",
        f"- temporal pairs: {summary['temporal_pairs']}  {summary['temporal_pair_status']}",
        f"- claims involving graph nodes: {summary['claims_involving_nodes']} (SUSPECTED {summary['claims_suspected']})",
        f"- unmapped evidence quotes: {summary['unmapped_quotes']}",
        "",
        "## Missing nodes",
        "",
        "| node | type | evidence units |",
        "| --- | --- | ---: |",
    ]
    missing = [row for row in node_rows if row["status"] == "missing"]
    lines += [
        f"| {row['name']} | {row['node_type']} | {row['evidence_units']} |" for row in missing[:30]
    ] or ["| — | — | — |"]
    lines += [
        "",
        "## Unit-only nodes (possible alias or omission)",
        "",
        "| node | type | GraphRAG candidates in the same units |",
        "| --- | --- | --- |",
    ]
    unit_only = [row for row in node_rows if row["status"] == "unit_only"]
    lines += [
        f"| {row['name']} | {row['node_type']} | {', '.join(row['candidate_titles'])} |"
        for row in unit_only[:30]
    ] or ["| — | — | — |"]
    lines += [
        "",
        "## Temporal pairs (not comparable)",
        "",
        "GraphRAG stores entity relationships, not event order. These pairs are listed only to show that no order verdict is possible.",
        "",
        "| subject | object | status |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {row['subject']} | {row['object']} | {row['status']} |" for row in pair_rows[:10]
    ] or ["| — | — | — |"]
    lines += [
        "",
        "## SUSPECTED claims involving graph nodes",
        "",
        "| subject | object | status | involved | description |",
        "| --- | --- | --- | --- | --- |",
    ]
    suspected = [row for row in claim_rows if row.get("status") == "SUSPECTED"]
    lines += [
        f"| {row['subject']} | {row['object']} | {row['status']} | {', '.join(row['involved_nodes'])} | {row['description']} |"
        for row in suspected[:20]
    ] or ["| — | — | — | — | — |"]
    lines += [
        "",
        "> GraphRAG absence is not false. This report is a review hint, not a verdict; no formal graph object was written.",
    ]
    (args.out / "cross-check.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"event": "finished", "summary": summary, "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
