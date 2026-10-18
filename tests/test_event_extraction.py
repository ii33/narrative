"""Untrusted model events enter the same evidence and review boundary as assertions."""

import json
import sqlite3

import pytest

from narrative.models import Assertion, Entity, Event
from narrative.operations import OperationError, Operations


@pytest.fixture
def scene(tmp_path):
    operations = Operations(tmp_path / "event-extraction.sqlite3")
    quotes = {
        "first": "甲把铜钥匙交给乙。",
        "second": "后来，乙把铜钥匙交给丙。",
        "move": "片刻后，甲走进大厅。",
        "claim": "甲说铜钥匙在乙手中。",
    }
    source_id = operations.import_text("交接", "\n".join(quotes.values()))
    run_id = operations.create_run(source_id)
    chunk_id = operations.export_run(run_id)["chunks"][0]["chunk_id"]
    return operations, source_id, run_id, chunk_id, quotes


def entities():
    return [
        {"ref": ref, "name": name, "entity_type": kind}
        for ref, name, kind in [
            ("a", "甲", "person"),
            ("b", "乙", "person"),
            ("c", "丙", "person"),
            ("key", "铜钥匙", "item"),
            ("hall", "大厅", "location"),
        ]
    ]


def event(quote, **overrides):
    return {
        "ref": "first",
        "title": "甲交出钥匙",
        "action": "transfer",
        "actor_ref": "a",
        "target_ref": "b",
        "item_ref": "key",
        "quote": quote,
        **overrides,
    }


def claim(quote):
    return {
        "subject_ref": "key",
        "predicate": "持有者",
        "value": "乙",
        "scope": "claim",
        "holder_ref": "a",
        "quote": quote,
    }


def result(*events, assertions=None, entity_rows=None):
    return json.dumps(
        {
            "entities": entities() if entity_rows is None else entity_rows,
            "assertions": assertions or [],
            "events": list(events),
        },
        ensure_ascii=False,
    )


def candidate_events(operations, change_id):
    return [
        operation
        for operation in operations.review_candidate(change_id).change.operations
        if isinstance(operation, Event)
    ]


def test_mixed_result_waits_for_review_then_timeline_and_state_use_forward_refs(scene):
    operations, _, _, chunk_id, quotes = scene
    revision = operations.project_info().revision
    raw = result(
        # Disclosure in model JSON is deliberately reversed; refs must resolve in either order.
        event(
            quotes["second"],
            ref="second",
            title="乙再交给丙",
            actor_ref="b",
            target_ref="c",
            time_kind="relative",
            after_event_ref="first",
        ),
        event(quotes["first"], time_kind="point", time_start=10),
        assertions=[claim(quotes["claim"])],
    )
    change_id = operations.ingest_result(chunk_id, raw)
    assert change_id
    assert operations.ingest_result(chunk_id, raw) == change_id
    assert len(operations.candidates()) == 1
    assert not operations.entities() and not operations.assertions() and not operations.events()
    assert operations.timeline()["rows"] == []
    assert operations.project_info().revision == revision

    review = operations.review_candidate(change_id)
    assert review.change.source == "model"
    assert sum(isinstance(op, Entity) for op in review.change.operations) == 5
    assert sum(isinstance(op, Assertion) for op in review.change.operations) == 1
    extracted = candidate_events(operations, change_id)
    first = next(op for op in extracted if op.title == "甲交出钥匙")
    second = next(op for op in extracted if op.title == "乙再交给丙")
    assert second.after_event_id == first.id
    assert second.time_start is None and second.time_end is None
    assert not [d for d in review.diagnostics if d.severity == "error"]

    operations.accept_candidate(change_id, review.version)
    ids = {row["name"]: row["id"] for row in operations.entities()}
    assert [row["id"] for row in operations.timeline()["rows"]] == [first.id, second.id]
    first_state = operations.event_state(first.id)
    second_state = operations.event_state(second.id)
    assert first_state["before"]["holders"][ids["铜钥匙"]] is None
    assert first_state["after"]["holders"][ids["铜钥匙"]] == ids["乙"]
    assert second_state["before"]["holders"][ids["铜钥匙"]] == ids["乙"]
    assert second_state["after"]["holders"][ids["铜钥匙"]] == ids["丙"]
    assert operations.assertions()[0].scope == "claim"
    reopened = Operations(operations.project_info().path)
    assert reopened.events() == operations.events()
    assert reopened.event_state(second.id) == second_state


@pytest.mark.parametrize("damage", ["missing", "altered", "offset"])
def test_bad_event_evidence_fails_entire_chunk_without_partial_candidates(scene, damage):
    operations, _, run_id, chunk_id, quotes = scene
    broken = event(quotes["second"], ref="second")
    if damage == "missing":
        broken.pop("quote")
    elif damage == "altered":
        broken["quote"] = "乙把铜钥匙交给丙!"
    else:
        broken["quote_start"] = 0
    raw = result(event(quotes["first"]), broken, assertions=[claim(quotes["claim"])])
    revision = operations.project_info().revision
    with pytest.raises(OperationError):
        operations.ingest_result(chunk_id, raw)
    assert not operations.candidates()
    assert not operations.entities() and not operations.assertions() and not operations.events()
    assert operations.project_info().revision == revision
    chunk = operations.chunks(run_id)[0]
    assert chunk["status"] == "failed" and chunk["raw"] == raw and chunk["error"]


def test_character_claim_does_not_become_world_event_and_event_scope_is_rejected(scene):
    operations, source_id, _, chunk_id, quotes = scene
    change_id = operations.ingest_result(chunk_id, result(assertions=[claim(quotes["claim"])]))
    review = operations.review_candidate(change_id)
    assert not candidate_events(operations, change_id)
    operations.accept_candidate(change_id, review.version)
    assert operations.assertions()[0].scope == "claim"
    assert not operations.events() and not operations.timeline()["rows"]

    run_id = operations.create_run(source_id)
    next_chunk = operations.export_run(run_id)["chunks"][0]["chunk_id"]
    with pytest.raises(OperationError):
        operations.ingest_result(
            next_chunk,
            result(event(quotes["claim"], scope="claim", holder_ref="a")),
        )
    assert len(operations.candidates()) == 1
    assert not operations.events()


def test_unanchored_relative_phrase_keeps_unknown_time_and_rejects_guessed_minutes(scene):
    operations, source_id, _, chunk_id, quotes = scene
    move = {
        "ref": "move",
        "title": "甲走进大厅",
        "action": "move",
        "actor_ref": "a",
        "location_ref": "hall",
        "time_kind": "unknown",
        "quote": quotes["move"],
    }
    change_id = operations.ingest_result(chunk_id, result(move))
    extracted = candidate_events(operations, change_id)[0]
    assert extracted.time_kind == "unknown"
    assert extracted.time_start is None and extracted.time_end is None
    assert extracted.after_event_id is None
    operations.accept_candidate(change_id, operations.review_candidate(change_id).version)
    assert operations.timeline()["rows"][0]["time_start"] is None

    run_id = operations.create_run(source_id)
    next_chunk = operations.export_run(run_id)["chunks"][0]["chunk_id"]
    with pytest.raises(OperationError):
        operations.ingest_result(next_chunk, result(move | {"time_start": 5}))
    assert len(operations.events()) == 1


def test_existing_event_snapshot_exposes_stable_ids_and_remains_fixed(tmp_path):
    operations = Operations(tmp_path / "existing-events.sqlite3")
    source_id = operations.import_text("观察", "门关上了。\n钟响了。")
    first_change = operations.propose_event(
        title="关门", action="observe", evidence=operations.locate(source_id, "门关上了。")[0]
    )
    first_review = operations.review_candidate(first_change)
    first_id = first_review.change.operations[0].id
    operations.accept_candidate(first_change, first_review.version)
    run_id = operations.create_run(source_id)
    snapshot = operations.export_run(run_id)
    assert [row["id"] for row in snapshot["existing_events"]] == [first_id]
    assert "events" in snapshot["result_schema"]["properties"]

    second_change = operations.propose_event(
        title="钟响", action="observe", evidence=operations.locate(source_id, "钟响了。")[0]
    )
    second_review = operations.review_candidate(second_change)
    second_id = second_review.change.operations[0].id
    operations.accept_candidate(second_change, second_review.version)
    assert operations.export_run(run_id)["existing_events"] == snapshot["existing_events"]
    assert second_id not in {row["id"] for row in snapshot["existing_events"]}

    raw = result(
        {
            "ref": "after-door",
            "title": "钟响",
            "action": "observe",
            "time_kind": "relative",
            "after_event_ref": first_id,
            "quote": "钟响了。",
        },
        entity_rows=[],
    )
    chunk_id = snapshot["chunks"][0]["chunk_id"]
    late_reference = json.loads(raw)
    late_reference["events"][0]["after_event_ref"] = second_id
    with pytest.raises(OperationError):
        operations.ingest_result(chunk_id, json.dumps(late_reference))
    assert not operations.candidates(status="pending")
    change_id = operations.ingest_result(chunk_id, raw)
    assert candidate_events(operations, change_id)[0].after_event_id == first_id
    assert (
        operations.review_candidate(change_id).change.expected_revision
        == snapshot["expected_revision"]
    )


def test_entity_and_event_ref_namespaces_are_independent(scene):
    operations, _, _, chunk_id, quotes = scene
    change_id = operations.ingest_result(
        chunk_id,
        result(
            event(quotes["first"], ref="a"),
            event(
                quotes["second"],
                ref="b",
                actor_ref="b",
                target_ref="c",
                time_kind="relative",
                after_event_ref="a",
            ),
        ),
    )
    review = operations.review_candidate(change_id)
    entity_a = next(
        op for op in review.change.operations if isinstance(op, Entity) and op.name == "甲"
    )
    first, second = candidate_events(operations, change_id)
    assert first.actor_id == entity_a.id and first.id != entity_a.id
    assert second.after_event_id == first.id
    assert second.after_event_id != entity_a.id
    operations.accept_candidate(change_id, review.version)
    assert len(operations.events()) == 2


@pytest.mark.parametrize("bad_ref", ["duplicate", "unknown_entity", "unknown_event"])
def test_invalid_event_refs_cannot_create_partial_candidates(scene, bad_ref):
    operations, _, _, chunk_id, quotes = scene
    first = event(quotes["first"])
    second = event(quotes["second"], ref="second")
    if bad_ref == "duplicate":
        second["ref"] = first["ref"]
    elif bad_ref == "unknown_entity":
        second["actor_ref"] = "first"  # A valid event ref is not an entity ref.
    else:
        second.update(time_kind="relative", after_event_ref="key")
    with pytest.raises(OperationError):
        operations.ingest_result(chunk_id, result(first, second))
    assert not operations.candidates() and not operations.entities() and not operations.events()


def test_same_batch_cycle_is_a_review_error_and_cannot_be_accepted(scene):
    operations, _, _, chunk_id, quotes = scene
    change_id = operations.ingest_result(
        chunk_id,
        result(
            event(quotes["first"], time_kind="relative", after_event_ref="second"),
            event(
                quotes["second"],
                ref="second",
                time_kind="relative",
                after_event_ref="first",
            ),
        ),
    )
    review = operations.review_candidate(change_id)
    assert any(d.rule_id == "TIME_CYCLE" and d.severity == "error" for d in review.diagnostics)
    revision = operations.project_info().revision
    with pytest.raises(OperationError):
        operations.accept_candidate(change_id, review.version)
    assert operations.project_info().revision == revision
    assert not operations.entities() and not operations.events()


@pytest.fixture
def recovery_scene(tmp_path):
    operations = Operations(tmp_path / "event-recovery.sqlite3")
    prefix = "前" * 80
    body = "甲敲门。\n甲说门锁着。\n甲敲门。"
    source_id = operations.import_text("重复引文", prefix + body)
    run_id = operations.create_run(source_id, size=80, overlap=0)
    chunk_id = operations.export_run(run_id)["chunks"][1]["chunk_id"]
    return operations, run_id, chunk_id, prefix, body


def mixed_recovery_response(event_quote, **event_fields):
    return result(
        {
            "ref": "knock",
            "title": "甲敲门",
            "action": "observe",
            "actor_ref": "a",
            "quote": event_quote,
            **event_fields,
        },
        assertions=[
            {"subject_ref": "a", "predicate": "动作", "value": "敲门", "quote": "甲敲门。"}
        ],
        entity_rows=[{"ref": "a", "name": "甲"}],
    )


def test_mixed_review_keeps_separate_indices_and_event_quote_preview_does_not_persist(
    recovery_scene,
):
    operations, run_id, chunk_id, _, body = recovery_scene
    raw = mixed_recovery_response("甲说门锁着!", quote_start=0)
    with pytest.raises(OperationError):
        operations.ingest_result(chunk_id, raw)
    before = operations.chunks(run_id)
    preview = operations.extraction_review(chunk_id, raw=raw, event_quotes={0: "甲说门锁着。"})
    assert preview["parse_error"] is None
    assert [(row["kind"], row["index"]) for row in preview["entries"]] == [
        ("assertion", 0),
        ("event", 0),
    ]
    assert preview["assertions"][0]["quote"] == "甲敲门。"
    assert preview["assertions"][0]["issue"] == "ambiguous"
    assert preview["events"][0]["quote_start"] is None
    assert preview["events"][0]["matches"][0]["quote_start"] == body.index("甲说门锁着。")
    edited = json.loads(preview["edited_raw"])
    assert edited["events"][0]["quote"] == "甲说门锁着。"
    assert edited["assertions"][0]["quote"] == "甲敲门。"
    assert operations.chunks(run_id) == before
    assert not operations.candidates()


def test_mixed_quote_repair_applies_independent_offsets_and_keeps_original_model_audit(
    recovery_scene,
):
    operations, run_id, chunk_id, prefix, body = recovery_scene
    raw = mixed_recovery_response("甲敲门。")
    model_metadata = {"model": "recorded-model", "request_id": "recorded-request"}
    with pytest.raises(OperationError):
        operations.ingest_result(chunk_id, raw, metadata=model_metadata)
    selected_event = body.rindex("甲敲门。")
    change_id = operations.repair_extraction_result(
        chunk_id, raw, {0: 0}, event_quote_starts={0: selected_event}
    )
    review = operations.review_candidate(change_id)
    assertion = next(op for op in review.change.operations if isinstance(op, Assertion))
    extracted = candidate_events(operations, change_id)[0]
    assert assertion.evidence[0].start == len(prefix)
    assert extracted.evidence[0].start == len(prefix) + selected_event
    assert extracted.disclosure_position == extracted.evidence[0].start
    chunk = next(row for row in operations.chunks(run_id) if row["id"] == chunk_id)
    metadata = json.loads(chunk["metadata"])
    assert metadata["model_response"] == {"raw": raw, "metadata": model_metadata}
    audit = metadata["manual_corrections"][-1]
    assert audit["selected_quote_starts"] == {"0": 0}
    assert audit["selected_event_quote_starts"] == {"0": selected_event}
    assert audit["previous_raw"] == raw and audit["edited_raw"] == raw
    assert operations.extraction_review(chunk_id)["error"] is None
    assert not operations.events() and not operations.assertions()
    operations.accept_candidate(change_id, review.version)
    assert operations.events()[0]["evidence"][0]["start"] == len(prefix) + selected_event


@pytest.mark.parametrize("selection", [{1: 0}, {False: 0}, {0: False}, {0: 1}, {0: -1}])
def test_invalid_event_repair_selection_cannot_escape_exact_evidence(recovery_scene, selection):
    operations, _, chunk_id, _, _ = recovery_scene
    raw = mixed_recovery_response("甲敲门。")
    with pytest.raises(OperationError):
        operations.ingest_result(chunk_id, raw)
    with pytest.raises(OperationError):
        operations.repair_extraction_result(chunk_id, raw, {0: 0}, event_quote_starts=selection)
    assert not operations.candidates() and not operations.events() and not operations.assertions()


def test_new_task_exports_its_original_prompt_and_schema_after_code_changes(scene, monkeypatch):
    from narrative import extraction

    operations, _, run_id, _, _ = scene
    original = operations.export_run(run_id)
    monkeypatch.setattr(extraction, "INSTRUCTION", "a later prompt")
    monkeypatch.setattr(extraction.ExtractionResult, "model_json_schema", lambda: {"changed": True})
    current = operations.export_run(run_id)
    assert current["instruction"] == original["instruction"]
    assert current["result_schema"] == original["result_schema"]
    assert current["prompt_version"] == "evidence-extraction/11"


def test_legacy_task_exports_v1_contract_without_rewriting_history(scene):
    from narrative.extraction import LEGACY_INSTRUCTION

    operations, _, run_id, _, _ = scene
    with sqlite3.connect(operations.project_info().path) as db:
        data = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        for field in ("instruction", "result_schema", "existing_events"):
            data.pop(field)
        data["prompt_version"] = "evidence-extraction/1"
        db.execute("UPDATE runs SET data=? WHERE id=?", (json.dumps(data), run_id))
    before = operations.runs()
    exported = operations.export_run(run_id)
    assert exported["instruction"] == LEGACY_INSTRUCTION
    assert exported["prompt_version"] == "evidence-extraction/1"
    assert "events" not in exported["result_schema"]["properties"]
    assert exported["existing_events"] == []
    assert operations.runs() == before
