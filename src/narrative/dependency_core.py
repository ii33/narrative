"""Typed cross-family dependencies and support-group eligibility.

A dependency ref is either a legacy assertion id string or:

    {"kind": "assertion", "id": "a17"}
    {"kind": "property_record", "id": "p42"}

``support_groups`` is a list of groups: all refs in one group must be valid,
and any one complete group is enough. A record without groups is valid unless
explicitly retracted. This module only computes eligibility; it never writes.
"""

from __future__ import annotations

from collections.abc import Iterable

REF_KINDS = {"assertion", "property_record"}


def normalize_ref(ref: object) -> dict:
    if isinstance(ref, str):
        if not ref:
            raise ValueError("dependency id must not be empty")
        return {"kind": "assertion", "id": ref}
    if (
        isinstance(ref, dict)
        and set(ref) == {"kind", "id"}
        and ref.get("kind") in REF_KINDS
        and isinstance(ref.get("id"), str)
        and ref["id"]
    ):
        return {"kind": ref["kind"], "id": ref["id"]}
    raise ValueError("dependency ref must be an id string or {kind, id}")


def support_groups(record: dict) -> list[list[dict]]:
    groups = record.get("support_groups")
    if not groups:
        derived = record.get("derived_from") or []
        if not isinstance(derived, list):
            raise ValueError("derived_from must be a list")
        return [[normalize_ref(ref) for ref in derived]] if derived else []
    if not isinstance(groups, list):
        raise ValueError("support_groups must be a non-empty list of groups")
    normalized: list[list[dict]] = []
    for group in groups:
        if not isinstance(group, list) or not group:
            raise ValueError("each support group must be a non-empty list")
        normalized.append([normalize_ref(ref) for ref in group])
    return normalized


def _records(graph_snapshot: dict, property_records: Iterable[dict]) -> dict[tuple[str, str], dict]:
    records: dict[tuple[str, str], dict] = {}
    for item in graph_snapshot.get("assertions", []) or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            records[("assertion", item["id"])] = item
    for item in property_records:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            records[("property_record", item["id"])] = item
    return records


def _retracted(graph_snapshot: dict, property_records: Iterable[dict]) -> set[tuple[str, str]]:
    retracted: set[tuple[str, str]] = set()
    for item in graph_snapshot.get("assertions", []) or []:
        if isinstance(item, dict) and item.get("assessment") == "retracted":
            identifier = item.get("id")
            if isinstance(identifier, str):
                retracted.add(("assertion", identifier))
    for item in property_records:
        if isinstance(item, dict) and item.get("kind") == "property_retraction":
            target = item.get("target_id")
            if isinstance(target, str):
                retracted.add(("property_record", target))
    return retracted


def validate_support(
    graph_snapshot: dict,
    property_records: list[dict],
    *,
    new_ids: set[tuple[str, str]] | None = None,
) -> list[dict]:
    """Validate refs and cycles for all records that declare support groups."""
    records = _records(graph_snapshot, property_records)
    issues: list[dict] = []
    dependencies: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key, record in records.items():
        if record.get("kind") in {"property_definition", "property_retraction"}:
            continue
        try:
            groups = support_groups(record)
        except ValueError as exc:
            issues.append(
                {
                    "severity": "error",
                    "code": "DEPENDENCY_FORMAT",
                    "message": str(exc),
                    "object_id": key[1],
                }
            )
            continue
        if not groups:
            continue
        deps: list[tuple[str, str]] = []
        for group in groups:
            for ref in group:
                ref_key = (ref["kind"], ref["id"])
                deps.append(ref_key)
                if ref_key not in records:
                    issues.append(
                        {
                            "severity": "error",
                            "code": "DEPENDENCY_REF",
                            "message": f"dependency ref not found: {ref['kind']} {ref['id']}",
                            "object_id": key[1],
                        }
                    )
        dependencies[key] = deps

    visiting: set[tuple[str, str]] = set()
    visited: set[tuple[str, str]] = set()

    def visit(key: tuple[str, str]) -> bool:
        if key in visiting:
            return True
        if key in visited:
            return False
        visiting.add(key)
        for child in dependencies.get(key, []):
            if visit(child):
                issues.append(
                    {
                        "severity": "error",
                        "code": "DEPENDENCY_CYCLE",
                        "message": "derived dependency cycle is not allowed",
                        "object_id": key[1],
                    }
                )
                visiting.remove(key)
                return True
        visiting.remove(key)
        visited.add(key)
        return False

    for key in dependencies:
        visit(key)
    return issues


def eligibility(graph_snapshot: dict, property_records: list[dict]) -> dict[tuple[str, str], str]:
    """Return valid / retracted / unsupported / missing / cycle for every record."""
    records = _records(graph_snapshot, property_records)
    retracted = _retracted(graph_snapshot, property_records)
    cache: dict[tuple[str, str], str] = {}
    visiting: set[tuple[str, str]] = set()

    def status(key: tuple[str, str]) -> str:
        if key in cache:
            return cache[key]
        if key in visiting:
            return "cycle"
        record = records.get(key)
        if record is None:
            return "missing"
        if key in retracted:
            return "retracted"
        try:
            groups = support_groups(record)
        except ValueError:
            return "unsupported"
        if not groups:
            return "valid"
        visiting.add(key)
        result = "unsupported"
        for group in groups:
            if all(status((ref["kind"], ref["id"])) == "valid" for ref in group):
                result = "valid"
                break
        visiting.remove(key)
        cache[key] = result
        return result

    return {key: status(key) for key in records}


def assertion_status(eligibility_map: dict[tuple[str, str], str]) -> dict[str, str]:
    return {
        key[1]: value
        for key, value in eligibility_map.items()
        if key[0] == "assertion"
    }
