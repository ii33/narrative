"""Offline protocol diagnostics and the independent first-use response replays."""

import copy
import json
from pathlib import Path

import pytest

from narrative.extraction import preflight_result
from narrative.operations import OperationError, Operations

SNAPSHOT = {"prompt_version": "evidence-extraction/6"}
REPORT = Path(__file__).resolve().parents[1] / "data/independent-firstuse-20261016-b7c4"


def raw(payload):
    return json.dumps(payload, ensure_ascii=False)


def diagnostics(payload, text="甲进门。甲说门锁了。", snapshot=None):
    return preflight_result(snapshot or SNAPSHOT, raw(payload), text)


def person_payload(**entries):
    return {
        "entities": [
            {"ref": "a", "name": "甲"},
            {"ref": "door", "name": "门", "entity_type": "location"},
            {"ref": "coin", "name": "钱", "entity_type": "item"},
        ],
        **entries,
    }


def event(**fields):
    return {
        "ref": "e",
        "title": "甲进门",
        "action": "move",
        "actor_ref": "a",
        "location_ref": "door",
        "quote": "甲进门。",
        **fields,
    }


def claim(**fields):
    return {
        "ref": "p",
        "subject_ref": "a",
        "predicate": "门锁了",
        "value": True,
        "scope": "claim",
        "holder_ref": "a",
        "quote": "甲说门锁了。",
        **fields,
    }


def test_preflight_collects_errors_after_invalid_entries_without_changing_payload():
    payload = person_payload(
        assertions=[
            None,
            claim(holder_ref=None, validity=None, quote="甲说……锁了。", subject_ref="未声明"),
        ],
        events=[event(time_kind="relative", actor_ref="coin")],
    )
    original = copy.deepcopy(payload)
    result = diagnostics(payload)
    assert not result["valid"] and not result["importable"] and result["parse_error"]
    assert payload == original
    by_path = {row["path"]: row for row in result["diagnostics"]}
    assert {
        "$.assertions[0]",
        "$.assertions[1].validity",
        "$.assertions[1].holder_ref",
        "$.assertions[1].subject_ref",
        "$.assertions[1].quote",
        "$.events[0].actor_ref",
        "$.events[0].after_event_ref",
    } <= by_path.keys()
    assert {"structure", "contract", "reference", "evidence", "type", "time"} <= {
        row["category"] for row in result["diagnostics"]
    }
    assert "{}" in by_path["$.assertions[1].validity"]["suggestion"]
    assert "suggestion" not in by_path["$.assertions[1].holder_ref"]
    assert result["assertions"][0]["invalid"] and result["assertions"][1]["index"] == 1
    assert by_path["$.events[0].after_event_ref"]["matches"][0]["quote"] == "甲进门。"


@pytest.mark.parametrize(
    ("fields", "code", "path"),
    [
        ({"time_kind": "unknown", "after_event_ref": "e"}, "time_relative_required", "time_kind"),
        ({"time_kind": "relative"}, "time_anchor_required", "after_event_ref"),
        ({"time_kind": "point"}, "time_point", "time_start"),
        ({"time_kind": "interval", "time_start": 2, "time_end": 1}, "time_interval", "time_end"),
        ({"time_kind": "unknown", "time_start": 1}, "time_numeric_unanchored", "time_start"),
        ({"persistence": "bounded"}, "effect_end_required", "valid_until"),
    ],
)
def test_time_errors_identify_the_exact_field_and_never_add_time(fields, code, path):
    payload = person_payload(events=[event(**fields)])
    original = copy.deepcopy(payload)
    result = diagnostics(payload)
    assert any(
        d["code"] == code and d["path"] == "$.events[0]." + path for d in result["diagnostics"]
    )
    assert payload == original


def test_valid_forward_refs_and_separate_namespaces_do_not_become_unknown():
    result = diagnostics(
        person_payload(
            assertions=[claim(ref="a")],
            events=[
                event(
                    action="tell",
                    target_ref="a",
                    assertion_ref="a",
                    time_kind="relative",
                    after_event_ref="later",
                ),
                event(ref="later", action="observe"),
            ],
        )
    )
    assert result["valid"] and result["importable"] and not result["diagnostics"]


def test_duplicate_and_unknown_refs_include_namespaces_and_original_indices():
    result = diagnostics(
        person_payload(events=[event(), event(after_event_ref="missing", actor_ref="e")])
    )
    errors = {(d["path"], d["code"]) for d in result["diagnostics"]}
    assert ("$.events[1].ref", "duplicate_ref") in errors
    assert ("$.events[1].actor_ref", "reference_unknown") in errors
    assert ("$.events[1].after_event_ref", "reference_unknown") in errors


def test_tell_missing_assertion_is_explained_before_candidate_review():
    result = diagnostics(person_payload(events=[event(action="tell", target_ref="a")]))
    error = next(d for d in result["diagnostics"] if d["path"] == "$.events[0].assertion_ref")
    assert error["code"] == "reference_required" and not error["blocks_import"]
    # Import validation and candidate-domain validation retain their existing
    # responsibilities; the candidate cannot be accepted with this error.
    assert not result["valid"] and result["importable"]


def test_evidence_is_locatable_even_when_another_field_is_invalid():
    result = preflight_result(
        SNAPSHOT,
        raw(person_payload(assertions=[claim(validity=None)])),
        "甲进门。甲说门锁了。",
        start=80,
    )
    error = next(d for d in result["diagnostics"] if d["path"].endswith(".validity"))
    assert error["matches"][0]["start"] == 84
    assert error["matches"][0]["end"] == 90


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        3,
        {"events": [{"action": [], "time_kind": {}, "quote": []}]},
        {"assertions": "invalid", "events": [None]},
    ],
)
def test_untrusted_json_types_are_diagnosed_instead_of_crashing(payload):
    result = diagnostics(payload)
    assert not result["valid"] and result["parse_error"]
    assert result["diagnostics"] and not result["importable"]


def test_invalid_json_gets_root_path():
    result = preflight_result(SNAPSHOT, "not json", "甲进门。")
    assert result["diagnostics"][0]["path"] == "$"
    assert result["diagnostics"][0]["code"] == "json_invalid"


@pytest.mark.parametrize(
    ("payload", "code", "path", "message"),
    [
        ({"entities": [{}]}, "schema_missing", "$.entities[0].ref", "缺少必填字段"),
        ({"assertions": [None]}, "schema_model_type", "$.assertions[0]", "JSON 对象"),
        (
            {"entities": [{"ref": 3, "name": "甲"}]},
            "schema_string_type",
            "$.entities[0].ref",
            "字符串",
        ),
        ({"entities": {}}, "schema_list_type", "$.entities", "JSON 数组"),
        (
            {"entities": [{"ref": "a", "name": ""}]},
            "schema_string_too_short",
            "$.entities[0].name",
            "至少",
        ),
        (
            {"entities": [{"ref": "a", "name": "甲", "surprise": True}]},
            "schema_extra_forbidden",
            "$.entities[0].surprise",
            "额外字段",
        ),
    ],
)
def test_common_schema_diagnostics_are_readable_in_chinese(payload, code, path, message):
    error = next(
        row
        for row in diagnostics(payload)["diagnostics"]
        if row["code"] == code and row["path"] == path
    )
    assert message in error["message"]
    assert "Input should" not in error["message"]


def test_operations_failure_is_atomic_and_manual_format_repair_keeps_original(tmp_path):
    app = Operations(tmp_path / "diagnostics.sqlite3")
    source = app.import_text("诊断", "甲进门。甲说门锁了。")
    run = app.create_run(source)
    chunk = app.chunks(run)[0]["id"]
    invalid = person_payload(
        assertions=[claim(validity=None)], events=[event(time_kind="relative")]
    )
    original = raw(invalid)
    before = app.project_snapshot()
    with pytest.raises(OperationError, match=r"assertions\[0\].validity"):
        app.ingest_result(chunk, original, metadata={"model": "saved-response"})
    review = app.extraction_review(chunk)
    assert not review["valid"] and len(review["diagnostics"]) == 2
    assert review["raw"] == original and review["assertions"][0]["matches"]
    assert app.runs()[0]["status"] == "failed"
    assert app.project_snapshot() == before and not app.candidates()
    invalid["assertions"][0]["validity"] = {}
    invalid["events"][0]["time_kind"] = "unknown"
    corrected = raw(invalid)
    change = app.repair_extraction_result(chunk, corrected, {}, expected_raw=original)
    assert change and app.extraction_review(chunk)["valid"]
    assert app.runs()[0]["status"] == "complete"
    metadata = json.loads(app.chunks(run)[0]["metadata"])
    assert metadata["model_response"] == {"raw": original, "metadata": {"model": "saved-response"}}
    assert metadata["manual_corrections"][-1]["edited_raw"] == corrected
    assert app.project_snapshot() == before


def test_manual_failure_with_processed_chunk_is_partial_then_repair_completes(tmp_path):
    app = Operations(tmp_path / "partial-diagnostics.sqlite3")
    source = app.import_text("分块诊断", "甲进门。甲说门锁了。")
    run = app.create_run(source, size=4, overlap=0)
    chunks = app.chunks(run)
    assert len(chunks) == 3
    app.ingest_result(chunks[0]["id"], "{}")
    assert app.runs()[0]["status"] == "partial"
    with pytest.raises(OperationError):
        app.ingest_result(chunks[1]["id"], '{"assertions":[null]}')
    assert app.runs()[0]["status"] == "partial"
    statuses = {row["id"]: row["status"] for row in app.chunks(run)}
    assert statuses[chunks[0]["id"]] == "processed"
    assert statuses[chunks[1]["id"]] == "failed"
    app.repair_extraction_result(chunks[1]["id"], "{}", {})
    assert app.runs()[0]["status"] == "partial"
    app.ingest_result(chunks[2]["id"], "{}")
    assert app.runs()[0]["status"] == "complete"


@pytest.mark.parametrize("name", ["lang", "rulin", "beiying"])
def test_independent_frozen_responses_report_all_visible_layers_together(name):
    # These optional local report artefacts retain the unmodified full original
    # responses; the reduced protocol regressions above also run without them.
    response_file = REPORT / name / "raw-0.txt"
    if not response_file.exists():
        pytest.skip("独立真实文本报告未随代码分发")
    original = response_file.read_bytes()
    text = (REPORT / (name + ".txt")).read_text(encoding="utf-8")
    result = preflight_result(SNAPSHOT, original.decode("utf-8"), text)
    errors = result["diagnostics"]
    codes = {d["code"] for d in errors}
    assert not result["valid"] and not result["importable"]
    if name == "lang":
        assert {
            "holder_required",
            "time_relative_required",
            "reference_type",
            "property_subject_type",
        } <= codes
        assert (
            next(d for d in errors if d["code"] == "holder_required")["path"]
            == "$.assertions[14].holder_ref"
        )
    elif name == "rulin":
        validity = [d for d in errors if d["path"].endswith(".validity")]
        assert len(validity) == 20
        assert {
            "evidence_missing",
            "time_point",
            "time_relative_required",
            "reference_required",
        } <= codes
        assert all("suggestion" in d for d in validity)
    else:
        assert {
            "schema_model_type",
            "reference_unknown",
            "time_anchor_required",
            "reference_required",
        } <= codes
        assert [d["path"] for d in errors if d["code"] == "schema_model_type"] == [
            "$.assertions[0]",
            "$.assertions[1]",
            "$.assertions[2]",
        ]
        assert [r["index"] for r in result["assertions"]] == list(range(12))
    assert response_file.read_bytes() == original


def test_m11_role_contract_stays_explicit_without_claiming_semantic_validation():
    # M11 is legal JSON with wrong literary semantics: move affects its actor,
    # even when item_ref or a title names the meat. Static preflight must not
    # pretend to have verified the meaning of the quotation.
    payload = person_payload(events=[event(item_ref="coin", title="甲把钱放到门上")])
    result = diagnostics(payload)
    assert result["valid"] and result["importable"]
    assert result["events"][0]["actor_ref"] == "a"
    assert result["events"][0]["item_ref"] == "coin"
