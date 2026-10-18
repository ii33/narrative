"""v7 model wire: frozen evidence IDs, positional refs, and strict import lanes."""

import json
import sqlite3

import pytest

from narrative import extraction
from narrative.models import Event
from narrative.operations import OperationError, Operations
from narrative.service import Service
from narrative.store import Store

TEXT = "甲看见乙。甲看见乙。"


def freeze_v7(path, run_id):
    with sqlite3.connect(path) as db:
        snapshot = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        snapshot["prompt_version"] = "evidence-extraction/7"
        snapshot["instruction"] = extraction.V7_INSTRUCTION
        snapshot["result_schema"] = extraction.V7ExtractionResult.model_json_schema()
        db.execute(
            "UPDATE runs SET data=? WHERE id=?",
            (json.dumps(snapshot, ensure_ascii=False), run_id),
        )


def project(tmp_path):
    app = Operations(tmp_path / "v7.sqlite3")
    source_id = app.import_text("重复观察", TEXT)
    run_id = app.create_run(source_id)
    freeze_v7(tmp_path / "v7.sqlite3", run_id)
    pack = app.export_run(run_id)
    chunk_id = pack["chunks"][0]["chunk_id"]
    return app, source_id, run_id, chunk_id, pack


def wire(*, evidence_ref="Q1", actor_ref="E0", target_ref="E1"):
    return {
        "entities": [
            {"name": "甲", "entity_type": "person"},
            {"name": "乙", "entity_type": "person"},
        ],
        "events": [
            {
                "title": "甲观察乙",
                "action": "observe",
                "actor_ref": actor_ref,
                "target_ref": target_ref,
                "evidence_ref": evidence_ref,
            }
        ],
    }


def test_v7_freezes_exact_spans_and_schema_without_model_quote_or_ref(tmp_path):
    app, _, run_id, _, pack = project(tmp_path)
    assert pack["prompt_version"] == "evidence-extraction/7"
    assert pack["manual_input_contract"] == "evidence-extraction/6"
    assert pack["chunks"][0]["evidence_spans"] == [
        {"id": "Q0", "start": 0, "end": 5, "quote": "甲看见乙。"},
        {"id": "Q1", "start": 5, "end": 10, "quote": "甲看见乙。"},
    ]
    definitions = pack["result_schema"]["$defs"]
    for name in (
        "V7ExtractedAssertion",
        "V7ExtractedEvent",
        "V7ExtractedState",
        "V7ExtractedRelation",
        "V7ExtractedKnowledge",
    ):
        fields = definitions[name]["properties"]
        assert "evidence_ref" in definitions[name]["required"]
        assert "quote" not in fields and "quote_start" not in fields and "ref" not in fields
    assert "ref" not in definitions["V7ExtractedEntity"]["properties"]
    assert app.export_run(run_id) == pack


def test_v7_exact_second_occurrence_enters_candidate_then_formal_review(tmp_path):
    app, _, run_id, chunk_id, pack = project(tmp_path)
    raw = json.dumps(wire(), ensure_ascii=False)
    preflight = extraction.preflight_result(pack, raw, TEXT)
    assert preflight["valid"] and preflight["importable"]
    assert preflight["evidence_resolution"] == [
        {
            "path": "$.events[0]",
            "evidence_ref": "Q1",
            "quote": "甲看见乙。",
            "quote_start": 5,
            "quote_end": 10,
        }
    ]
    change_id = app.ingest_result(chunk_id, raw)
    assert change_id is not None
    chunk = app.chunks(run_id)[0]
    assert chunk["raw"] == raw
    metadata = json.loads(chunk["metadata"])
    assert metadata["input_contract"] == "model-wire/7"
    assert metadata["evidence_resolution"] == preflight["evidence_resolution"]
    review = app.review_candidate(change_id)
    assert not [diagnostic for diagnostic in review.diagnostics if diagnostic.severity == "error"]
    event = next(op for op in review.change.operations if isinstance(op, Event))
    assert event.evidence[0].quote == "甲看见乙。"
    assert event.evidence[0].start == 5
    assert not app.events()
    app.accept_candidate(change_id, review.version)
    assert len(app.entities()) == 2 and len(app.events()) == 1
    assert app.events()[0]["evidence"][0]["start"] == 5


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"evidence_ref": "Q9"}, "evidence_ref_unknown"),
        ({"actor_ref": "E9"}, "reference_unknown"),
        ({"target_ref": "A0"}, "reference_unknown"),
        ({"assertion_ref": "V0"}, "reference_unknown"),
        ({"quote": "甲看见乙。"}, "schema_extra_forbidden"),
        ({"ref": "V0"}, "schema_extra_forbidden"),
    ],
)
def test_v7_unknown_or_model_supplied_evidence_and_refs_block_whole_chunk(
    tmp_path, mutation, expected
):
    app, _, run_id, chunk_id, pack = project(tmp_path)
    payload = wire()
    payload["events"][0].update(mutation)
    raw = json.dumps(payload, ensure_ascii=False)
    preflight = extraction.preflight_result(pack, raw, TEXT)
    assert not preflight["importable"]
    assert expected in {item["code"] for item in preflight["diagnostics"]}
    with pytest.raises(OperationError):
        app.ingest_result(chunk_id, raw)
    assert app.chunks(run_id)[0]["raw"] == raw
    assert not app.candidates() and not app.entities() and not app.events()


def test_v7_requires_value_null_and_never_invents_missing_entity(tmp_path):
    app, _, _, _, pack = project(tmp_path)
    payload = wire()
    payload["assertions"] = [{"subject_ref": "E2", "predicate": "在场", "evidence_ref": "Q0"}]
    preflight = extraction.preflight_result(pack, json.dumps(payload), TEXT)
    assert not preflight["importable"]
    assert {item["code"] for item in preflight["diagnostics"]} >= {
        "schema_missing",
        "reference_unknown",
    }
    assert not app.entities()


def test_v7_only_resolves_declared_reference_fields_not_literal_values(tmp_path):
    app, _, run_id, chunk_id, pack = project(tmp_path)
    payload = {
        "entities": [{"name": "甲", "entity_type": "person"}],
        "assertions": [
            {
                "subject_ref": "E0",
                "predicate": "原样字符串",
                "value": "E0、A0 和 Q0 都是文字",
                "evidence_ref": "Q0",
            }
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False)
    preflight = extraction.preflight_result(pack, raw, TEXT)
    assert preflight["valid"] and preflight["importable"]
    change_id = app.ingest_result(chunk_id, raw)
    assert change_id
    review = app.review_candidate(change_id)
    assertion = next(op for op in review.change.operations if op.kind == "assertion")
    assert assertion.value == "E0、A0 和 Q0 都是文字"
    assert assertion.evidence[0].start == 0
    assert (
        json.loads(app.chunks(run_id)[0]["metadata"])["evidence_resolution"][0]["evidence_ref"]
        == "Q0"
    )


def test_v7_refuses_changed_frozen_registry_even_if_model_id_exists(tmp_path):
    _, _, _, chunk_id, pack = project(tmp_path)
    pack["evidence_chunks"][chunk_id]["spans"][1]["quote"] = "非原文"
    preflight = extraction.preflight_result(pack, json.dumps(wire(), ensure_ascii=False), TEXT)
    assert not preflight["importable"]
    assert "evidence_registry_invalid" in {item["code"] for item in preflight["diagnostics"]}


def test_backend_model_lane_never_falls_back_to_old_wire(tmp_path):
    service = Service(Store(tmp_path / "model-lane.sqlite3"))
    source_id = service.import_text("旧载荷", "甲看见乙。")
    run_id = extraction.create_run(service, source_id)
    freeze_v7(service.store.path, run_id)
    chunk_id = extraction.export_run(service, run_id)["chunks"][0]["chunk_id"]
    old_wire = json.dumps(
        {
            "entities": [{"ref": "a", "name": "甲"}, {"ref": "b", "name": "乙"}],
            "events": [
                {
                    "ref": "watch",
                    "title": "甲观察乙",
                    "action": "observe",
                    "actor_ref": "a",
                    "target_ref": "b",
                    "quote": "甲看见乙。",
                }
            ],
        },
        ensure_ascii=False,
    )
    with service.store.connect(write=True) as db:
        db.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
        db.execute("UPDATE chunks SET status='running' WHERE id=?", (chunk_id,))
    with pytest.raises(ValueError, match="提取预检未通过"):
        extraction.ingest_result(
            service,
            chunk_id,
            old_wire,
            metadata={"finish_reason": "completed"},
            active_run_only=True,
        )
    chunk = service.store.rows("chunks")[0]
    assert chunk["raw"] == old_wire and chunk["status"] == "failed"
    assert json.loads(chunk["metadata"])["input_contract"] == "model-wire/7"
    assert not service.store.rows("changes")


def test_manual_legacy_wire_keeps_strict_quote_and_ref_validation(tmp_path):
    app, _, run_id, chunk_id, _ = project(tmp_path)
    old_wire = json.dumps(
        {
            "entities": [{"ref": "a", "name": "甲"}, {"ref": "b", "name": "乙"}],
            "events": [
                {
                    "ref": "watch",
                    "title": "甲观察乙",
                    "action": "observe",
                    "actor_ref": "a",
                    "target_ref": "b",
                    "quote": "甲看见乙。",
                    "quote_start": 5,
                }
            ],
        },
        ensure_ascii=False,
    )
    preview = app.extraction_review(chunk_id, old_wire)
    assert preview["valid"] and preview["importable"]
    change_id = app.ingest_result(chunk_id, old_wire)
    assert change_id
    metadata = json.loads(app.chunks(run_id)[0]["metadata"])
    assert metadata["input_contract"] == "manual-legacy/6"
    assert app.review_candidate(change_id).change.operations[-1].evidence[0].start == 5
