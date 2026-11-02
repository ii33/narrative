"""Typed reference rewrites for physical identity merges."""
from typing import Any


def graph_references(value, identities: dict[str, str], field: str = "") -> Any:
    """Only typed reference fields are rewritten; names, quotes and IDs stay raw."""
    scalar = {"node_id", "holder_id", "event_id", "subject_id", "object_id", "applies_to"}
    arrays = {"participants", "predecessors", "successors", "events", "related_nodes"}
    if isinstance(value, str):
        return identities.get(value, value) if field in scalar | arrays else value
    if isinstance(value, list):
        rows = [graph_references(item, identities, field) for item in value]
        return list(dict.fromkeys(rows)) if field in arrays else rows
    if isinstance(value, dict):
        return {key: graph_references(item, identities, key) for key, item in value.items()}
    return value
