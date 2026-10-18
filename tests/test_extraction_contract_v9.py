"""v9 keeps v8 evidence rules while allowing conservative semantic records.

These contract tests do not measure whether a live model follows the guidance.
"""

import hashlib
import json
import sqlite3

from narrative import extraction
from narrative.operations import Operations

TEXT = "我有一个孩子。我准备爬上月台，双手攀着边缘。"
V8_INSTRUCTION_SHA256 = "c13d508befe381b8bf6f4eda20baa095e5c6a6fae7c4250e5a67ed7a6fc0654b"
V8_SCHEMA_SHA256 = "5f56c919068e2c9f6063da0a129a4f39f30915b049d5847312e58011378ac4a0"


def scene(tmp_path):
    app = Operations(tmp_path / "v9.sqlite3")
    source_id = app.import_text("进行中的攀爬", TEXT)
    run_id = app.create_run(source_id)
    with sqlite3.connect(tmp_path / "v9.sqlite3") as db:
        snapshot = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        snapshot["prompt_version"] = "evidence-extraction/9"
        snapshot["instruction"] = extraction.V9_INSTRUCTION
        snapshot["result_schema"] = extraction.V8ExtractionResult.model_json_schema()
        db.execute(
            "UPDATE runs SET data=? WHERE id=?",
            (json.dumps(snapshot, ensure_ascii=False), run_id),
        )
    pack = app.export_run(run_id)
    return app, run_id, pack


def test_v9_new_run_and_v8_historical_contract_are_distinct(tmp_path):
    _, _, pack = scene(tmp_path)
    assert pack["prompt_version"] == "evidence-extraction/9"
    assert pack["instruction"] == extraction.V9_INSTRUCTION
    assert pack["result_schema"] == extraction.V8ExtractionResult.model_json_schema()
    assert extraction._contract({"prompt_version": "evidence-extraction/8"}) == (
        extraction.V8ExtractionResult,
        extraction.V8_INSTRUCTION,
    )
    assert hashlib.sha256(extraction.V8_INSTRUCTION.encode("utf-8")).hexdigest() == (
        V8_INSTRUCTION_SHA256
    )
    assert hashlib.sha256(
        json.dumps(extraction.V8ExtractionResult.model_json_schema(), sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest() == V8_SCHEMA_SHA256
    assert "不证明叙述者是父亲或母亲" in extraction.V9_INSTRUCTION
    assert "尝试、攀爬进行中" in extraction.V9_INSTRUCTION


def test_v9_conservative_parentage_and_climbing_process_enter_original_candidate(tmp_path):
    app, run_id, pack = scene(tmp_path)
    chunk_id = pack["chunks"][0]["chunk_id"]
    raw = json.dumps(
        {
            "entities": [
                {"name": "我", "entity_type": "person"},
                {"name": "孩子", "entity_type": "person"},
                {"name": "月台", "entity_type": "location"},
            ],
            "relations": [
                {
                    "subject_ref": "E0",
                    "object_ref": "E1",
                    "predicate": "家长",
                    "evidence_ref": "Q0",
                }
            ],
            "assertions": [
                {
                    "subject_ref": "E0",
                    "predicate": "攀爬月台",
                    "value": "准备并正在攀爬，未证已到达",
                    "evidence_ref": "Q1",
                }
            ],
        },
        ensure_ascii=False,
    )
    preflight = extraction.preflight_result(pack, raw, TEXT)
    assert preflight["valid"] and preflight["importable"]
    change_id = app.ingest_result(chunk_id, raw)
    assert change_id
    candidate = app.review_candidate(change_id).change
    assert not any(op.__class__.__name__ == "Event" for op in candidate.operations)
    assert len(candidate.operations) == 5
    assert app.chunks(run_id)[0]["raw"] == raw
    assert json.loads(app.chunks(run_id)[0]["metadata"])["input_contract"] == "model-wire/9"
    assert not app.entities() and not app.assertions() and not app.events()
