"""v8 evidence ranges stay bounded, exact, and isolated from frozen v7 runs."""

import hashlib
import json
import sqlite3

import pytest

from narrative import extraction
from narrative.operations import OperationError, Operations

TEXT = "甲看见乙。乙转身。甲读到此处。甲离开。"


def freeze_v8(path, run_id):
    with sqlite3.connect(path) as db:
        snapshot = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        snapshot["prompt_version"] = "evidence-extraction/8"
        snapshot["instruction"] = extraction.V8_INSTRUCTION
        snapshot["result_schema"] = extraction.V8ExtractionResult.model_json_schema()
        db.execute(
            "UPDATE runs SET data=? WHERE id=?",
            (json.dumps(snapshot, ensure_ascii=False), run_id),
        )


def scene(tmp_path):
    app = Operations(tmp_path / "v8.sqlite3")
    source_id = app.import_text("连续片段", TEXT)
    run_id = app.create_run(source_id)
    freeze_v8(tmp_path / "v8.sqlite3", run_id)
    pack = app.export_run(run_id)
    return app, run_id, pack["chunks"][0]["chunk_id"], pack


def wire(evidence_ref):
    return {
        "entities": [
            {"name": "甲", "entity_type": "person"},
            {"name": "乙", "entity_type": "person"},
        ],
        "events": [
            {
                "title": "甲看见乙",
                "action": "observe",
                "actor_ref": "E0",
                "target_ref": "E1",
                "evidence_ref": evidence_ref,
            }
        ],
    }


def test_v8_default_schema_and_frozen_v7_instruction():
    assert extraction._contract({"prompt_version": "evidence-extraction/8"}) == (
        extraction.V8ExtractionResult,
        extraction.V8_INSTRUCTION,
    )
    assert hashlib.sha256(extraction.V7_INSTRUCTION.encode("utf-8")).hexdigest() == (
        "ddee5b9b9a3fb0034b9273c9daf58b67c17183f5a1eff660c2b1f27bcb6cf6e0"
    )
    assert hashlib.sha256(
        json.dumps(extraction.V7ExtractionResult.model_json_schema(), sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest() == "1dde7df3efe60545207197897b5907a845e30721961b6c174884ab5da10e3a7a"
    assert extraction._contract({"prompt_version": "evidence-extraction/7"}) == (
        extraction.V7ExtractionResult,
        extraction.V7_INSTRUCTION,
    )


@pytest.mark.parametrize(
    ("reference", "expected_start", "expected_end"),
    [("Q0", 0, 5), ("Q0-Q1", 0, 9), ("Q0-Q2", 0, 15)],
)
def test_v8_single_or_two_three_contiguous_spans_resolve_one_original_slice(
    tmp_path, reference, expected_start, expected_end
):
    app, run_id, chunk_id, pack = scene(tmp_path)
    assert pack["prompt_version"] == "evidence-extraction/8"
    assert [span["id"] for span in pack["chunks"][0]["evidence_spans"]] == [
        "Q0",
        "Q1",
        "Q2",
        "Q3",
    ]
    raw = json.dumps(wire(reference), ensure_ascii=False)
    review = app.extraction_review(chunk_id, raw)
    assert review["valid"] and review["importable"]
    assert review["evidence_resolution"] == [
        {
            "path": "$.events[0]",
            "evidence_ref": reference,
            "quote": TEXT[expected_start:expected_end],
            "quote_start": expected_start,
            "quote_end": expected_end,
        }
    ]
    change_id = app.ingest_result(chunk_id, raw)
    assert change_id
    candidate = app.review_candidate(change_id).change
    event = candidate.operations[-1]
    assert event.evidence[0].quote == TEXT[expected_start:expected_end]
    assert event.evidence[0].start == expected_start
    assert event.evidence[0].end == expected_end
    assert app.chunks(run_id)[0]["raw"] == raw
    assert json.loads(app.chunks(run_id)[0]["metadata"])["input_contract"] == "model-wire/8"


@pytest.mark.parametrize(
    "reference",
    ["Q1-Q0", "Q0-Q3", "Q0-Q99", "Q99-Q100", "Q0-Q1-Q2", "Q0-Q1 garbage"],
)
def test_v8_rejects_reversed_overlong_unknown_or_malformed_ranges_without_candidate(
    tmp_path, reference
):
    app, run_id, chunk_id, pack = scene(tmp_path)
    raw = json.dumps(wire(reference), ensure_ascii=False)
    preflight = extraction.preflight_result(pack, raw, TEXT)
    assert not preflight["importable"]
    with pytest.raises(OperationError):
        app.ingest_result(chunk_id, raw)
    assert app.chunks(run_id)[0]["raw"] == raw
    assert not app.candidates() and not app.entities() and not app.events()


def test_v7_historical_snapshot_still_rejects_v8_range(tmp_path):
    _, _, _, pack = scene(tmp_path)
    historical = {
        **pack,
        "prompt_version": "evidence-extraction/7",
        "instruction": extraction.V7_INSTRUCTION,
        "result_schema": extraction.V7ExtractionResult.model_json_schema(),
    }
    raw = json.dumps(wire("Q0-Q1"), ensure_ascii=False)
    preflight = extraction.preflight_result(historical, raw, TEXT)
    assert not preflight["importable"]
    assert "schema_string_pattern_mismatch" in {
        diagnostic["code"] for diagnostic in preflight["diagnostics"]
    }
