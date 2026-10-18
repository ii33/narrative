"""Evidence recovery works through Operations without calling a model or accepting facts."""

import json
import sqlite3

import pytest

from narrative.models import Assertion
from narrative.operations import OperationError, Operations


def response(*assertions):
    return json.dumps(
        {"entities": [{"ref": "a", "name": "甲"}], "assertions": list(assertions)},
        ensure_ascii=False,
    )


def assertion(quote, **overrides):
    return {
        "subject_ref": "a",
        "predicate": "动作",
        "value": "待核验",
        "quote": quote,
        **overrides,
    }


@pytest.fixture
def recovery_project(tmp_path):
    operations = Operations(tmp_path / "recovery.sqlite3")
    prefix = "前" * 80
    body = "甲敲门。\r\n甲说门锁着。🙂\r\n甲敲门。"
    source_id = operations.import_text("重复引文", prefix + body)
    run_id = operations.create_run(source_id, size=80, overlap=0)
    chunk_id = operations.export_run(run_id)["chunks"][1]["chunk_id"]
    return operations, run_id, chunk_id, prefix, body


def fail_import(operations, chunk_id, raw, metadata=None):
    with pytest.raises(OperationError):
        operations.ingest_result(chunk_id, raw, metadata=metadata)


def chunk_metadata(operations, run_id, chunk_id):
    chunk = next(c for c in operations.chunks(run_id) if c["id"] == chunk_id)
    return json.loads(chunk["metadata"])


def test_review_distinguishes_missing_ambiguous_and_wrong_offset(recovery_project):
    operations, _, chunk_id, prefix, body = recovery_project
    raw = response(
        assertion("甲敲门。"),
        assertion("甲说门锁着!"),
        assertion("甲说门锁着。", quote_start=0),
        assertion("甲说门锁着。"),
    )
    fail_import(operations, chunk_id, raw)

    review = operations.extraction_review(chunk_id)
    assert review["status"] == "failed" and review["raw"] == raw and review["error"]
    assert review["text"] == body
    assert (review["start"], review["end"]) == (len(prefix), len(prefix + body))
    assert review["parse_error"] is None
    rows = review["assertions"]
    assert [row["issue"] for row in rows] == ["ambiguous", "missing", "offset", None]
    assert [match["quote_start"] for match in rows[0]["matches"]] == [
        0,
        body.rindex("甲敲门。"),
    ]
    # Wrong model offsets must still show the real exact matches for human selection.
    match = rows[2]["matches"][0]
    assert match["quote_start"] == body.index("甲说门锁着。")
    assert match["start"] == len(prefix) + match["quote_start"]
    assert match["end"] == match["start"] + len(match["quote"])
    assert match["quote"] == "甲说门锁着。"
    assert match["before"].endswith("甲敲门。\r\n")
    assert match["after"].startswith("🙂\r\n甲敲门。")


def test_duplicate_requires_choice_and_repair_is_only_a_candidate(recovery_project):
    operations, run_id, chunk_id, prefix, body = recovery_project
    raw = response(assertion("甲敲门。"))
    service_metadata = {"model": "recorded-model", "usage": {"total_tokens": 123}}
    fail_import(operations, chunk_id, raw, service_metadata)
    revision = operations.project_info().revision

    with pytest.raises(OperationError):
        operations.repair_extraction_result(chunk_id, raw, {})
    assert not operations.candidates()

    selected = body.rindex("甲敲门。")
    change_id = operations.repair_extraction_result(chunk_id, raw, {0: selected})
    review = operations.review_candidate(change_id)
    evidence = next(op for op in review.change.operations if isinstance(op, Assertion)).evidence[0]
    assert evidence.start == len(prefix) + selected
    assert evidence.quote == "甲敲门。"
    assert not [d for d in review.diagnostics if d.severity == "error"]
    assert not operations.entities() and not operations.assertions()
    assert operations.project_info().revision == revision

    metadata = chunk_metadata(operations, run_id, chunk_id)
    assert metadata["model_response"] == {"raw": raw, "metadata": service_metadata}
    audit = metadata["manual_corrections"][-1]
    assert audit["previous_raw"] == raw and audit["edited_raw"] == raw
    assert audit["selected_quote_starts"] == {"0": selected}
    operations.accept_candidate(change_id, review.version)
    assert operations.assertions()[0].evidence[0].start == len(prefix) + selected


def test_missing_quote_can_be_edited_and_exactly_located(recovery_project):
    operations, run_id, chunk_id, prefix, body = recovery_project
    raw = response(assertion("甲说门锁着!"))
    fail_import(operations, chunk_id, raw, {"request_id": "recorded-request"})
    corrected = response(assertion("甲说门锁着。"))
    start = body.index("甲说门锁着。")

    change_id = operations.repair_extraction_result(chunk_id, corrected, {0: start})
    result = operations.extraction_review(chunk_id)
    assert result["status"] == "processed" and result["error"] is None
    assert result["assertions"][0]["quote_start"] == start
    evidence = next(
        op
        for op in operations.review_candidate(change_id).change.operations
        if isinstance(op, Assertion)
    ).evidence[0]
    assert evidence.start == len(prefix) + start
    metadata = chunk_metadata(operations, run_id, chunk_id)
    assert metadata["model_response"]["raw"] == raw
    assert metadata["model_response"]["metadata"] == {"request_id": "recorded-request"}
    assert metadata["manual_corrections"][-1]["edited_raw"] == corrected
    assert not operations.assertions()


def test_preview_edits_quote_without_persisting_or_reusing_old_offset(recovery_project):
    operations, run_id, chunk_id, _, body = recovery_project
    raw = response(assertion("甲说门锁着!", quote_start=0))
    fail_import(operations, chunk_id, raw)
    before = operations.chunks(run_id)

    preview = operations.extraction_review(chunk_id, raw=raw, quotes={0: "甲说门锁着。"})
    assert preview["raw"] == raw
    assert preview["assertions"][0]["quote"] == "甲说门锁着。"
    assert preview["assertions"][0]["quote_start"] is None
    assert preview["assertions"][0]["matches"][0]["quote_start"] == body.index("甲说门锁着。")
    assert json.loads(preview["edited_raw"])["assertions"][0]["quote"] == "甲说门锁着。"
    assert operations.chunks(run_id) == before
    assert not operations.candidates()


def test_invalid_json_is_visible_and_failed_manual_edit_can_be_repaired(recovery_project):
    operations, run_id, chunk_id, _, body = recovery_project
    raw = "the recorded model response is not JSON"
    fail_import(operations, chunk_id, raw, {"model": "recorded-model"})
    review = operations.extraction_review(chunk_id)
    assert review["parse_error"] and review["assertions"] == [] and review["raw"] == raw

    edited_raw = response(assertion("原文没有这句。"))
    with pytest.raises(OperationError):
        operations.repair_extraction_result(chunk_id, edited_raw, {})
    failed = operations.extraction_review(chunk_id)
    assert failed["status"] == "failed" and failed["raw"] == edited_raw
    assert failed["assertions"][0]["issue"] == "missing"
    assert not operations.candidates() and not operations.assertions()

    corrected = response(assertion("甲说门锁着。"))
    operations.repair_extraction_result(chunk_id, corrected, {0: body.index("甲说门锁着。")})
    metadata = chunk_metadata(operations, run_id, chunk_id)
    assert metadata["model_response"]["raw"] == raw
    assert len(metadata["manual_corrections"]) == 2
    assert metadata["manual_corrections"][0]["previous_raw"] == raw
    assert metadata["manual_corrections"][1]["previous_raw"] == edited_raw


@pytest.mark.parametrize(
    "selection", [{0: 1}, {0: -1}, {0: 999}, {1: 0}, {-1: 0}, {False: 0}, {0: False}]
)
def test_invalid_selection_cannot_create_evidence(recovery_project, selection):
    operations, _, chunk_id, _, _ = recovery_project
    raw = response(assertion("甲敲门。"))
    fail_import(operations, chunk_id, raw)
    with pytest.raises(OperationError):
        operations.repair_extraction_result(chunk_id, raw, selection)
    assert not operations.candidates() and not operations.assertions()
    assert operations.extraction_review(chunk_id)["status"] == "failed"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_running_task_cannot_be_repaired(recovery_project, status):
    operations, run_id, chunk_id, _, _ = recovery_project
    raw = response(assertion("甲敲门。"))
    fail_import(operations, chunk_id, raw)
    before = operations.chunks(run_id)
    # Seed the lifecycle state without a model call; opening a fresh Operations would recover it.
    with sqlite3.connect(operations.project_info().path) as db:
        db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
    with pytest.raises(OperationError):
        operations.repair_extraction_result(chunk_id, raw, {0: 0})
    assert operations.chunks(run_id) == before
    assert not operations.candidates() and not operations.assertions()


def test_processed_chunk_cannot_be_repaired_again(recovery_project):
    operations, run_id, chunk_id, _, _ = recovery_project
    raw = response(assertion("甲敲门。", quote_start=0))
    operations.ingest_result(chunk_id, raw)
    before = operations.chunks(run_id)
    with pytest.raises(OperationError):
        operations.repair_extraction_result(chunk_id, raw, {0: 0})
    assert operations.chunks(run_id) == before
    assert len(operations.candidates()) == 1 and not operations.assertions()


def test_stale_repair_draft_cannot_overwrite_another_sessions_edit(recovery_project):
    operations, run_id, chunk_id, _, _ = recovery_project
    raw = response(assertion("甲说门锁着!"))
    fail_import(operations, chunk_id, raw)
    opened_raw = operations.extraction_review(chunk_id)["raw"]
    others_edit = response(assertion("另一会话尚未修完的引文。"))
    with pytest.raises(OperationError):
        operations.repair_extraction_result(chunk_id, others_edit, {}, expected_raw=opened_raw)
    before = operations.chunks(run_id)

    with pytest.raises(OperationError, match="改变|刷新|重新"):
        operations.repair_extraction_result(
            chunk_id,
            response(assertion("甲说门锁着。")),
            {},
            expected_raw=opened_raw,
        )
    assert operations.chunks(run_id) == before
    assert operations.extraction_review(chunk_id)["raw"] == others_edit
    assert not operations.candidates() and not operations.assertions()


def test_repair_remains_atomic_when_another_quote_still_fails(recovery_project):
    operations, _, chunk_id, _, body = recovery_project
    raw = response(assertion("甲敲门。"), assertion("甲说门锁着!"))
    fail_import(operations, chunk_id, raw)
    with pytest.raises(OperationError):
        operations.repair_extraction_result(chunk_id, raw, {0: body.rindex("甲敲门。")})
    assert not operations.candidates() and not operations.entities()
    corrected = response(assertion("甲敲门。"), assertion("甲说门锁着。"))
    change_id = operations.repair_extraction_result(
        chunk_id,
        corrected,
        {0: body.rindex("甲敲门。"), 1: body.index("甲说门锁着。")},
    )
    candidate = operations.review_candidate(change_id)
    assert len([op for op in candidate.change.operations if isinstance(op, Assertion)]) == 2
    assert not operations.assertions()
