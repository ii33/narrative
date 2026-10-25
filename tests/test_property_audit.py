"""Rule-based property audit: overlaps, contradictions, stale values, gaps."""

from __future__ import annotations

from pathlib import Path

from narrative.project_catalog import create_graph_project
from narrative.property_audit import audit_properties


def _node(node_id: str, name: str, node_type: str = "person") -> dict:
    return {"kind": "node", "id": node_id, "name": name, "node_type": node_type}


def _definition(property_id: str, value_type: str = "string") -> dict:
    return {
        "kind": "property_definition", "id": property_id, "value_type": value_type,
        "cardinality": "one", "persistence": "until_changed",
    }


def _value(
    rid: str, node_id: str, property_id: str, value,
    *, event="1", start=0, source_id="s1", quote="原文", **extra,
) -> dict:
    return {
        "kind": "property_value", "id": rid, "node_id": node_id,
        "property_id": property_id, "value": value, "event_id": event,
        "disclosure": {"source_id": source_id, "start": start},
        "evidence": {"source_id": source_id, "quote": quote},
        **extra,
    }


def _codes(report: dict) -> list[str]:
    return [item["code"] for item in report["issues"]]


def test_identical_values_at_one_anchor_are_an_overlap() -> None:
    records = [
        _definition("height"),
        _value("v1", "1", "height", "180", start=0),
        _value("v2", "1", "height", "180", start=0),
    ]
    report = audit_properties(records, [_node("1", "林舟")])

    assert "duplicate_value" in _codes(report)
    overlap = next(item for item in report["issues"] if item["code"] == "duplicate_value")
    assert overlap["severity"] == "error"
    assert overlap["record_ids"] == ["v1", "v2"]


def test_same_value_at_different_places_is_a_restatement_not_an_error() -> None:
    records = [
        _definition("holder"),
        _value("v1", "1", "holder", "7", event="1", start=100),
        _value("v2", "1", "holder", "7", event="5", start=900),
    ]
    report = audit_properties(records, [_node("1", "火纹石", "item")])

    codes = _codes(report)
    assert "duplicate_value" not in codes
    assert "repeated_value" in codes
    assert report["counts"]["error"] == 0


def test_different_values_report_the_current_one() -> None:
    records = [
        _definition("height"),
        _value("v1", "1", "height", "矮小", event="1", start=0),
        _value("v2", "1", "height", "180 公分", event="2", start=0),
    ]
    report = audit_properties(records, [_node("1", "林舟")])

    conflict = next(item for item in report["issues"] if item["code"] == "conflicting_values")
    assert "180 公分" in conflict["message"]
    # History is kept: the earlier value is not an error, only context.
    assert conflict["severity"] == "warning"


def test_missing_definition_evidence_and_anchor_are_reported() -> None:
    records = [
        _value("v1", "1", "undeclared", "x"),
        {"kind": "property_value", "id": "v2", "node_id": "1", "property_id": "undeclared",
         "value": "y", "disclosure": {"source_id": "s1", "start": 5}},
    ]
    report = audit_properties(records, [_node("1", "林舟")])
    codes = set(_codes(report))

    assert "definition_missing" in codes
    assert "evidence_missing" in codes
    assert "anchor_missing" in codes


def test_cognition_without_holder_is_an_error() -> None:
    records = [
        _definition("belief"),
        _value("v1", "1", "belief", "老周赢了", scope="cognition"),
    ]
    report = audit_properties(records, [_node("1", "林舟")])

    issue = next(item for item in report["issues"] if item["code"] == "holder_missing")
    assert issue["severity"] == "error"


def test_synonym_property_names_are_flagged() -> None:
    records = [
        _definition("height"),
        _definition("height_cm"),
        _value("v1", "1", "height", "180", start=0),
        _value("v2", "1", "height_cm", "180", start=10),
    ]
    report = audit_properties(records, [_node("1", "林舟")])

    issue = next(item for item in report["issues"] if item["code"] == "name_variant")
    assert "height" in issue["message"] and "height_cm" in issue["message"]


def test_unused_definition_is_an_info_not_an_error() -> None:
    records = [_definition("age")]
    report = audit_properties(records, [_node("1", "林舟")])

    issue = next(item for item in report["issues"] if item["code"] == "definition_unused")
    assert issue["severity"] == "info"
    assert report["counts"]["error"] == 0


def test_audit_can_be_limited_to_the_nodes_of_one_extraction(tmp_path: Path) -> None:
    ops = create_graph_project(tmp_path / "audit.sqlite3")
    ops.graph_accept(
        ops.graph_propose(
            [
                _node("1", "林舟"),
                _node("2", "老周"),
                {"kind": "node", "id": "3", "node_type": "event", "name": "交谈"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    source_id = ops.import_source("第一章", "林舟身高一百八十公分。老周矮小。")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            [
                _definition("height"),
                _value("v1", "1", "height", "180", event="3", start=0,
                       source_id=source_id, quote="林舟身高一百八十公分。"),
                _value("v2", "1", "height", "180", event="3", start=0,
                       source_id=source_id, quote="林舟身高一百八十公分。"),
                _value("v3", "2", "height", "矮小", event="3", start=0,
                       source_id=source_id, quote="老周矮小。"),
                _value("v4", "2", "height", "矮小", event="3", start=0,
                       source_id=source_id, quote="老周矮小。"),
            ],
            revision,
            source="manual",
        ),
        1,
    )

    whole = ops.property_audit()
    limited = ops.property_audit({"2"})

    assert "duplicate_value" in _codes(whole)
    assert {item["node_id"] for item in limited["issues"]} == {"2"}
    assert limited["checked_records"] == 2
