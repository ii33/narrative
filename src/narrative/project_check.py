"""Read-only static checks of one revision, without staging a write."""
from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime

from . import property_categories, property_core
from .property_audit import audit_properties


def _locate(issue: dict, objects: dict, records: dict, sources: dict) -> dict:
    result = dict(issue)
    identifier = issue.get("object_id")
    record_ids = list(issue.get("record_ids") or [])
    if identifier in records and identifier not in record_ids:
        record_ids.insert(0, identifier)
    record = records.get(record_ids[0], {}) if record_ids else {}
    node_id = issue.get("node_id") or record.get("node_id")
    if not node_id and objects.get(identifier, {}).get("kind") == "node":
        node_id = identifier
    node = objects.get(node_id, {})
    obj = objects.get(identifier, node)
    evidence = record.get("evidence") or node.get("evidence") or {}
    disclosure = record.get("disclosure") or node.get("disclosure") or {}
    source_id = evidence.get("source_id") or disclosure.get("source_id")
    start = evidence.get("start", disclosure.get("start"))
    quote = evidence.get("quote")
    end = evidence.get("end")
    if end is None and isinstance(start, int) and isinstance(quote, str):
        end = start + len(quote)
    result.update(object_id=identifier or node_id, node_id=node_id, node_name=node.get("name"),
                  object_name=obj.get("name") or obj.get("label") or sources.get(source_id, {}).get("title"),
                  property_id=issue.get("property_id") or record.get("property_id")
                              or (record.get("id") if record.get("kind") == "property_definition" else None),
                  record_ids=record_ids, record_id=record_ids[0] if record_ids else None,
                  source_id=source_id, start=start, end=end, quote=quote)
    result["target"] = {"kind": "node" if node_id else "source" if source_id else "project",
                        "node_id": node_id, "record_id": result["record_id"],
                        "source_id": source_id, "start": start, "end": end}
    return result


def check(ops) -> dict:
    # Capture objects, event changes and immutable sources in one read transaction.
    graph = ops._store.snapshot(validation_data=True)
    revision = graph["revision"]
    validation = graph.pop("_validation")
    graph["sources"] = [row | {"text": validation["sources"][row["id"]]["text"]} for row in graph["sources"]]
    raw = validation["records"]
    records = raw
    indexed = {row["id"]: row for row in records}
    issues = ops._service.validate_snapshot(graph, records)
    from .temporal_state import change_issues, projected_changes
    issues.extend(change_issues(graph, records))
    current = records
    historical = []
    enriched = graph
    issues.extend(property_core.validate_records(current, enriched, historical))
    audit_rows = [row for row in records if row.get("kind") == "property_definition"] + projected_changes(graph, records)
    active_nodes = [row for row in graph["nodes"] if not row.get("retracted")]
    active_ids = {row["id"] for row in active_nodes}
    audit_rows = [row for row in audit_rows if not row.get("node_id") or row["node_id"] in active_ids]
    category_config = validation["meta"].get(property_categories.CONFIG_KEY)
    try:
        categories = property_categories.normalize_vocabulary(json.loads(category_config)) if category_config else None
    except json.JSONDecodeError:
        categories = None
    issues.extend(audit_properties(audit_rows, active_nodes, categories=categories, usage_records=records)["issues"])
    objects = {row["id"]: row for row in graph["objects"]}
    sources = {row["id"]: row for row in graph["sources"]}
    located = [_locate(issue, objects, indexed, sources) for issue in issues]
    unique = {}
    for issue in located:
        key = (issue["code"], issue.get("object_id"), issue.get("node_id"),
               tuple(issue["record_ids"]), issue["message"])
        unique.setdefault(key, issue)
    output = sorted(unique.values(), key=lambda row: (
        {"error": 0, "warning": 1, "info": 2}.get(row.get("severity"), 3),
        str(row.get("node_id") or ""), row["code"], json.dumps(row["record_ids"])))
    counts = {severity: 0 for severity in ("error", "warning", "info")}
    counts.update(Counter(row["severity"] for row in output))
    return {"revision": revision, "checked_revision": revision,
            "current_revision": ops.graph_project_info()["revision"], "issues": output, "counts": counts,
            "checked_at": datetime.now(UTC).isoformat(),
            "checked_objects": len(active_nodes), "checked_records": len(current),
            "historical_records": len(historical)}
