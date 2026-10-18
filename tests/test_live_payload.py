"""The real SDK receives the full frozen extraction context, without credentials."""

import json
import sqlite3

import pytest
from test_harness import model_server as model_server
from test_harness import wait_finished

from narrative.checks import digest
from narrative.operations import Operations


def request_payload(request):
    marker = "故事资料 JSON：\n"
    prompts = [
        message["content"]
        for message in request["body"]["messages"]
        if isinstance(message.get("content"), str) and marker in message["content"]
    ]
    assert len(prompts) == 1
    return json.loads(prompts[0].split(marker, 1)[1])


def test_live_v11_sends_existing_assertions_approved_properties_and_chunk_schema(tmp_path, model_server, capsys):
    url, requests, control, _ = model_server
    app = Operations(tmp_path / "complete-live.sqlite3")
    app.save_harness_settings({"base_url": url, "model": "local-test", "timeout_seconds": 15})
    source_id = app.import_text("已批准资料", "甲说门锁着。乙听见甲说门锁着。乙很紧张。")
    evidence = app.locate(source_id, "甲说门锁着。")[0].model_dump()
    change_id = app.propose_objects(
        [
            {"kind": "entity", "id": "a", "name": "甲"},
            {"kind": "entity", "id": "b", "name": "乙"},
            {"kind": "entity", "id": "door", "name": "门", "entity_type": "other"},
            {
                "kind": "assertion",
                "id": "door-locked",
                "subject_id": "door",
                "predicate": "门状态",
                "value": "锁着",
                "scope": "claim",
                "holder_id": "a",
                "evidence": [evidence],
            },
            {
                "kind": "property",
                "id": "approved-mood",
                "name": "心情",
                "value_type": "string",
                "origin": "production",
            },
        ]
    )
    app.accept_candidate(change_id, 1)
    revision = app.project_info().revision
    run_id = app.create_run(source_id)
    snapshot = app.export_run(run_id)
    assert snapshot["prompt_version"] == "evidence-extraction/11"
    control["result"] = {
        "states": [
            {
                "kind": "property",
                "subject_ref": "b",
                "property_id": "approved-mood",
                "value": {"kind": "scalar", "data": "紧张"},
                "evidence_ref": "Q2",
            }
        ],
        "knowledge": [
            {
                "holder_ref": "b",
                "assertion_ref": "door-locked",
                "attitude": "informed",
                "acquisition": "told",
                "source_character_ref": "a",
                "evidence_ref": "Q1",
            }
        ],
    }
    secret = "test-key-secret"
    app.start_extraction(run_id, api_key=secret)
    result = wait_finished(app, run_id)
    assert result["status"] == "complete", result
    assert len(requests) == 1
    payload = request_payload(requests[0])
    expected = {
        "existing_entities": snapshot["existing_entities"],
        "existing_assertions": snapshot["existing_assertions"],
        "existing_events": snapshot["existing_events"],
        "existing_records": snapshot["existing_records"],
        "result_schema": snapshot["chunk_result_schemas"][snapshot["chunks"][0]["chunk_id"]],
        "chunk": snapshot["chunks"][0],
    }
    assert payload == expected
    assert payload["existing_assertions"][0]["id"] == "door-locked"
    assert payload["existing_records"][0]["id"] == "approved-mood"
    assert payload["chunk"]["evidence_spans"] == [
        {"id": "Q0", "start": 0, "end": 6, "quote": "甲说门锁着。"},
        {"id": "Q1", "start": 6, "end": 15, "quote": "乙听见甲说门锁着。"},
        {"id": "Q2", "start": 15, "end": 20, "quote": "乙很紧张。"},
    ]
    definitions = payload["result_schema"]["$defs"]
    chunk_id = snapshot["chunks"][0]["chunk_id"]
    assert definitions["V11EvidenceRef"]["enum"] == snapshot["evidence_chunks"][chunk_id][
        "allowed_evidence_refs"
    ]
    for collection in ("assertions", "events", "states", "relations", "knowledge"):
        item = payload["result_schema"]["properties"][collection]["items"]
        refs = [item["$ref"]] if "$ref" in item else [choice["$ref"] for choice in item["oneOf"]]
        assert refs
        for ref in refs:
            fields = definitions[ref.rsplit("/", 1)[-1]]["properties"]
            assert "evidence_ref" in fields
            assert "quote" not in fields and "quote_start" not in fields and "ref" not in fields
    chunk = app.chunks(run_id)[0]
    metadata = json.loads(chunk["metadata"])
    expected_hash = digest(json.dumps(expected, ensure_ascii=False))
    assert metadata["input_hash"] == expected_hash
    for field in ("existing_assertions", "existing_records"):
        assert digest(json.dumps({**expected, field: []}, ensure_ascii=False)) != expected_hash
    assert metadata["prompt_version"] == "evidence-extraction/11"
    assert app.project_info().revision == revision
    assert len(app.records()) == 1
    candidate = app.candidates(status="pending")[0]
    review = app.review_candidate(candidate["id"])
    assert not [d for d in review.diagnostics if d.severity == "error"]
    assert {obj.kind for obj in review.change.operations} == {"state", "knowledge"}
    app.accept_candidate(candidate["id"], review.version)
    assert {record["kind"] for record in app.records()} == {"property", "state", "knowledge"}
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert secret not in json.dumps({"runs": app.runs(), "chunks": app.chunks(run_id)})
    assert secret not in json.dumps(requests[0]["body"])


@pytest.mark.parametrize("version", [1, 2])
def test_live_legacy_snapshot_defaults_new_context_to_empty(tmp_path, model_server, version):
    url, requests, control, _ = model_server
    app = Operations(tmp_path / f"legacy-{version}.sqlite3")
    app.save_harness_settings({"base_url": url, "model": "local-test", "timeout_seconds": 15})
    source_id = app.import_text("旧任务", "甲说门锁着。")
    revision = app.project_info().revision
    run_id = app.create_run(source_id)
    with sqlite3.connect(app.project_info().path) as db:
        data = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        data["prompt_version"] = f"evidence-extraction/{version}"
        for field in ("existing_assertions", "existing_records", "instruction", "result_schema"):
            data.pop(field)
        if version == 1:
            data.pop("existing_events")
        db.execute("UPDATE runs SET data=? WHERE id=?", (json.dumps(data), run_id))
    control["result"] = {"entities": [], "assertions": []}
    app.start_extraction(run_id, api_key="test-key-secret")
    result = wait_finished(app, run_id)
    assert result["status"] == "complete", result
    assert len(requests) == 1
    payload = request_payload(requests[0])
    assert payload["existing_assertions"] == [] and payload["existing_records"] == []
    assert payload["existing_events"] == []
    metadata = json.loads(app.chunks(run_id)[0]["metadata"])
    assert metadata["prompt_version"] == f"evidence-extraction/{version}"
    assert metadata["input_hash"] == digest(json.dumps(payload, ensure_ascii=False))
    assert app.project_info().revision == revision
    assert not app.candidates()
