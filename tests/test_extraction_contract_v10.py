"""v10 model wire has one tagged value and explicit state roles.

These tests check protocol boundaries, not a live model's semantic quality.
"""

import hashlib
import json
import sqlite3

import pytest

from narrative import extraction
from narrative.operations import OperationError, Operations

TEXT = "甲拿着果子。甲走到屋里。甲戴着帽子。乙手受伤。甲写了一封信。"
V9_INSTRUCTION_SHA256 = "6123197e8d4864bbe7544d827b8f73c5dd4cdf41564598e907b3dfcdf81888aa"
V9_SCHEMA_SHA256 = "5f56c919068e2c9f6063da0a129a4f39f30915b049d5847312e58011378ac4a0"


def scene(tmp_path):
    app = Operations(tmp_path / "v10.sqlite3")
    source_id = app.import_text("值与角色", TEXT)
    run_id = app.create_run(source_id)
    with sqlite3.connect(tmp_path / "v10.sqlite3") as db:
        snapshot = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        snapshot["prompt_version"] = "evidence-extraction/10"
        snapshot["instruction"] = extraction.V10_INSTRUCTION
        snapshot["result_schema"] = extraction.V10ExtractionResult.model_json_schema()
        db.execute(
            "UPDATE runs SET data=? WHERE id=?",
            (json.dumps(snapshot, ensure_ascii=False), run_id),
        )
    pack = app.export_run(run_id)
    return app, run_id, pack["chunks"][0]["chunk_id"], pack


def wire():
    return {
        "entities": [
            {"name": "甲", "entity_type": "person"},
            {"name": "果子", "entity_type": "item"},
            {"name": "屋", "entity_type": "location"},
            {"name": "帽子", "entity_type": "item"},
            {"name": "乙", "entity_type": "person"},
            {"name": "信", "entity_type": "item"},
        ],
        "assertions": [
            {
                "subject_ref": "E0",
                "predicate": "拿着",
                "value": {"kind": "entity", "ref": "E1"},
                "evidence_ref": "Q0",
            },
            {
                "subject_ref": "E4",
                "predicate": "手部情况",
                "value": {"kind": "scalar", "data": "受伤"},
                "evidence_ref": "Q3",
            },
        ],
        "states": [
            {
                "kind": "held_by",
                "item_ref": "E1",
                "holder_ref": "E0",
                "evidence_ref": "Q0",
            },
            {
                "kind": "location",
                "person_ref": "E0",
                "location_ref": "E2",
                "evidence_ref": "Q1",
            },
            {
                "kind": "outfit",
                "person_ref": "E0",
                "item_ref": "E3",
                "evidence_ref": "Q2",
            },
            {
                "kind": "property",
                "subject_ref": "E4",
                "property_id": "injury:手",
                "value": {"kind": "scalar", "data": "受伤"},
                "evidence_ref": "Q3",
            },
        ],
        "events": [
            {
                "title": "甲写了一封信",
                "action": "act",
                "actor_ref": "E0",
                "item_ref": "E5",
                "evidence_ref": "Q4",
            }
        ],
    }


def test_v10_schema_closes_value_channels_and_preserves_v9_snapshot(tmp_path):
    _, _, _, pack = scene(tmp_path)
    assert pack["prompt_version"] == "evidence-extraction/10"
    assert extraction.PROMPT_VERSION == "evidence-extraction/11"
    assert pack["instruction"] == extraction.V10_INSTRUCTION
    assert pack["result_schema"] == extraction.V10ExtractionResult.model_json_schema()
    assert extraction._contract({"prompt_version": "evidence-extraction/9"}) == (
        extraction.V8ExtractionResult,
        extraction.V9_INSTRUCTION,
    )
    assert hashlib.sha256(extraction.V9_INSTRUCTION.encode()).hexdigest() == V9_INSTRUCTION_SHA256
    assert hashlib.sha256(
        json.dumps(extraction.V8ExtractionResult.model_json_schema(), sort_keys=True).encode()
    ).hexdigest() == V9_SCHEMA_SHA256
    schema = pack["result_schema"]
    assert "value_entity_ref" not in schema["$defs"]["V10ExtractedAssertion"]["properties"]
    assert "value_entity_ref" not in schema["$defs"]["V10HeldByState"]["properties"]
    assert "act" in schema["$defs"]["V10ExtractedEvent"]["properties"]["action"]["enum"]
    assert "act" not in extraction.V8ExtractionResult.model_json_schema()["$defs"][
        "V8ExtractedEvent"
    ]["properties"]["action"]["enum"]


def test_v10_tagged_values_and_roles_map_exactly_into_candidate(tmp_path):
    app, run_id, chunk_id, pack = scene(tmp_path)
    raw = json.dumps(wire(), ensure_ascii=False)
    preflight = extraction.preflight_result(pack, raw, TEXT)
    assert preflight["valid"] and preflight["importable"]
    assert len(preflight["wire_resolution"]) == 7
    assert len(preflight["evidence_resolution"]) == 7
    change_id = app.ingest_result(chunk_id, raw)
    assert change_id
    change = app.review_candidate(change_id).change
    assert len(change.operations) == 13
    entities = {item.name: item.id for item in change.operations if item.kind == "entity"}
    assertions = [item for item in change.operations if item.kind == "assertion"]
    assert assertions[0].value is None and assertions[0].value_entity_id == entities["果子"]
    states = [item for item in change.operations if item.kind == "state"]
    held = next(item for item in states if item.property_id == "held_by")
    assert held.subject_id == entities["果子"] and held.value_entity_id == entities["甲"]
    assert next(item for item in states if item.property_id == "location").value_entity_id == entities["屋"]
    event = next(item for item in change.operations if item.kind == "event")
    assert event.action == "act" and event.effects == []
    chunk = app.chunks(run_id)[0]
    assert chunk["raw"] == raw
    metadata = json.loads(chunk["metadata"])
    assert metadata["input_contract"] == "model-wire/10"
    assert metadata["wire_resolution"] == preflight["wire_resolution"]
    assert not app.entities() and not app.events()


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda row: row["assertions"][0].update(value={"kind": "entity", "ref": "E1", "data": True}), "schema_extra_forbidden"),
        (lambda row: row["assertions"][0].update(value_entity_ref="E1"), "schema_extra_forbidden"),
        (lambda row: row["states"][0].update(item_ref="E0", holder_ref="E1"), "property_subject_type"),
        (lambda row: row["states"][0].update(kind="property", subject_ref="E1", property_id="held_by", value={"kind": "entity", "ref": "E0"}), "state_role_required"),
        (lambda row: row["assertions"][0].update(value={"kind": "entity", "ref": "E99"}), "reference_unknown"),
        (lambda row: row["events"][0].update(effects=[{"subject_ref": "E0", "property_id": "location", "value": {"kind": "entity", "ref": "E2"}}]), "act_state_payload"),
    ],
)
def test_v10_rejects_dual_values_wrong_roles_unknown_refs_and_act_effects(
    tmp_path, change, code
):
    app, run_id, chunk_id, pack = scene(tmp_path)
    payload = wire()
    change(payload)
    raw = json.dumps(payload, ensure_ascii=False)
    preflight = extraction.preflight_result(pack, raw, TEXT)
    assert not preflight["importable"]
    assert code in {item["code"] for item in preflight["diagnostics"] if item["blocks_import"]}
    with pytest.raises(OperationError):
        app.ingest_result(chunk_id, raw)
    assert app.chunks(run_id)[0]["raw"] == raw
    assert not app.candidates() and not app.entities()


def test_v10_keeps_explicit_manual_v6_lane_without_model_wire_fallback(tmp_path):
    app, run_id, chunk_id, _ = scene(tmp_path)
    manual = json.dumps(
        {
            "entities": [{"ref": "person", "name": "甲", "entity_type": "person"}],
            "assertions": [
                {
                    "ref": "fact",
                    "subject_ref": "person",
                    "predicate": "拿着果子",
                    "value": True,
                    "quote": "甲拿着果子。",
                }
            ],
        },
        ensure_ascii=False,
    )
    review = app.extraction_review(chunk_id, manual)
    assert review["valid"] and review["importable"]
    change_id = app.ingest_result(chunk_id, manual)
    assert change_id
    metadata = json.loads(app.chunks(run_id)[0]["metadata"])
    assert metadata["input_contract"] == "manual-legacy/6"
    assert metadata["input_route"] == "manual"
