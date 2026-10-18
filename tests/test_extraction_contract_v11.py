"""v11 freezes bounded evidence choices in the schema sent for each chunk."""

import json
import sqlite3

import pytest

from narrative import extraction, jobs
from narrative.harness import HarnessReply, HarnessSettings
from narrative.operations import Operations

TEXT = "甲走了。乙看见。甲停下。乙靠近。甲开口。乙回答。甲离开。乙坐下。"


def scene(tmp_path, text=TEXT):
    app = Operations(tmp_path / "v11.sqlite3")
    source_id = app.import_text("有界证据", text)
    run_id = app.create_run(source_id)
    pack = app.export_run(run_id)
    chunk_id = pack["chunks"][0]["chunk_id"]
    return app, run_id, chunk_id, pack


def wire(ref):
    return {
        "entities": [{"name": "甲", "entity_type": "person"}],
        "assertions": [
            {"subject_ref": "E0", "predicate": "经历", "value": {"kind": "scalar", "data": True}, "evidence_ref": ref}
        ],
    }


def test_v11_actual_chunk_schema_and_snapshot_share_exact_allowed_refs(tmp_path):
    app, run_id, chunk_id, pack = scene(tmp_path)
    assert pack["prompt_version"] == extraction.PROMPT_VERSION == "evidence-extraction/11"
    assert pack["instruction"] == extraction.V11_INSTRUCTION
    schema = pack["chunk_result_schemas"][chunk_id]
    refs = schema["$defs"]["V11EvidenceRef"]["enum"]
    assert "Q0" in refs and "Q0-Q5" in refs
    assert "Q0-Q6" not in refs
    assert schema["$defs"]["V10ExtractedEvent"]["properties"]["evidence_ref"] == {
        "$ref": "#/$defs/V11EvidenceRef"
    }
    with sqlite3.connect(tmp_path / "v11.sqlite3") as db:
        snapshot = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()[0])
    assert snapshot["evidence_chunks"][chunk_id]["allowed_evidence_refs"] == refs
    assert snapshot["evidence_chunks"][chunk_id]["result_schema_sha256"] == extraction._schema_hash(schema)
    assert not app.candidates()


def test_v11_job_sends_the_hashed_chunk_schema_not_the_base_schema(tmp_path, monkeypatch):
    app, run_id, chunk_id, pack = scene(tmp_path)
    sent = []

    class FakeRunner:
        def __init__(self, *_args):
            pass

        def extract(self, _instruction, payload, _label):
            sent.append(payload)
            return HarnessReply(raw="{}", metadata={"finish_reason": "completed"})

        def close(self):
            pass

        def redact(self, message):
            return message

        def usage_metadata(self):
            return {"usage": None}

    monkeypatch.setattr(jobs, "HarnessRunner", FakeRunner)
    with app._store.connect(write=True) as db:
        db.execute("UPDATE runs SET status='queued' WHERE id=?", (run_id,))
    jobs.Job(
        app._store, run_id, HarnessSettings(), "", jobs.ProjectLease(tmp_path / "test.lock")
    ).execute()
    assert len(sent) == 1
    assert sent[0]["result_schema"] == pack["chunk_result_schemas"][chunk_id]
    assert sent[0]["result_schema"] != pack["result_schema"]
    with sqlite3.connect(tmp_path / "v11.sqlite3") as db:
        snapshot = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()[0])
    assert extraction._schema_hash(sent[0]["result_schema"]) == snapshot["evidence_chunks"][chunk_id][
        "result_schema_sha256"
    ]


def test_v11_six_span_range_resolves_one_exact_contiguous_slice(tmp_path):
    _, _, _, pack = scene(tmp_path)
    review = extraction.preflight_result(pack, json.dumps(wire("Q0-Q5"), ensure_ascii=False), TEXT)
    assert review["valid"] and review["importable"]
    evidence = review["evidence_resolution"][0]
    assert evidence["evidence_ref"] == "Q0-Q5"
    assert evidence["quote"] == TEXT[: evidence["quote_end"]]
    assert evidence["quote_start"] == 0


@pytest.mark.parametrize(
    ("ref", "code"),
    [
        ("Q0-Q6", "evidence_range_span_limit"),
        ("Q5-Q1", "evidence_range_reversed"),
        ("Q0-Q99", "evidence_ref_unknown"),
        ("Q0-Q0", "evidence_range_invalid"),
        ("Q0Q2", "evidence_ref_invalid"),
    ],
)
def test_v11_distinguishes_invalid_evidence_ranges(tmp_path, ref, code):
    _, _, _, pack = scene(tmp_path)
    review = extraction.preflight_result(pack, json.dumps(wire(ref), ensure_ascii=False), TEXT)
    assert not review["importable"]
    assert any(
        item["path"] == "$.assertions[0].evidence_ref"
        and item["code"] == code
        and item["blocks_import"]
        for item in review["diagnostics"]
    )


def test_v11_limits_multispan_characters_but_keeps_single_span_whole(tmp_path):
    text = "甲" * 140 + "。" + "乙" * 140 + "。"
    _, _, chunk_id, pack = scene(tmp_path, text)
    refs = pack["chunk_result_schemas"][chunk_id]["$defs"]["V11EvidenceRef"]["enum"]
    assert "Q0" in refs and "Q1" in refs and "Q0-Q1" not in refs
    review = extraction.preflight_result(pack, json.dumps(wire("Q0-Q1"), ensure_ascii=False), text)
    assert any(item["code"] == "evidence_range_char_limit" for item in review["diagnostics"])


def test_v10_frozen_range_policy_does_not_use_v11_enum(tmp_path):
    _, run_id, chunk_id, _ = scene(tmp_path)
    with sqlite3.connect(tmp_path / "v11.sqlite3") as db:
        snapshot = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        snapshot["prompt_version"] = "evidence-extraction/10"
        snapshot["instruction"] = extraction.V10_INSTRUCTION
        snapshot["result_schema"] = extraction.V10ExtractionResult.model_json_schema()
        db.execute("UPDATE runs SET data=? WHERE id=?", (json.dumps(snapshot, ensure_ascii=False), run_id))
    app = Operations(tmp_path / "v11.sqlite3")
    pack = app.export_run(run_id)
    assert "chunk_result_schemas" not in pack
    assert chunk_id == pack["chunks"][0]["chunk_id"]
    review = extraction.preflight_result(pack, json.dumps(wire("Q0-Q3"), ensure_ascii=False), TEXT)
    assert any(item["code"] == "evidence_ref_unknown" for item in review["diagnostics"])


@pytest.mark.parametrize("tamper", ["enum", "schema_hash"])
def test_v11_rejects_tampered_frozen_enum_or_schema_hash(tmp_path, tamper):
    _, run_id, chunk_id, _ = scene(tmp_path)
    with sqlite3.connect(tmp_path / "v11.sqlite3") as db:
        snapshot = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        if tamper == "enum":
            snapshot["evidence_chunks"][chunk_id]["allowed_evidence_refs"].append("Q0-Q99")
        else:
            snapshot["evidence_chunks"][chunk_id]["result_schema_sha256"] = "0" * 64
        db.execute("UPDATE runs SET data=? WHERE id=?", (json.dumps(snapshot, ensure_ascii=False), run_id))
    with pytest.raises(ValueError, match="枚举|哈希"):
        Operations(tmp_path / "v11.sqlite3").export_run(run_id)
