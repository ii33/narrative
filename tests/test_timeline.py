import json
import sqlite3

import pytest

from narrative.models import Evidence
from narrative.operations import OperationError, Operations


@pytest.fixture
def scene(tmp_path):
    operations = Operations(tmp_path / "timeline.sqlite3")
    quotes = {
        "second": "乙把铜钥匙交给丙。",
        "first": "甲把铜钥匙交给乙。",
        "garden": "丙走进花园。",
        "hall": "甲走进大厅。",
        "observe": "甲看见乙。",
    }
    source_id = operations.import_text("插叙", "\n".join(quotes.values()))
    for entity_id, name, entity_type in [
        ("a", "甲", "person"),
        ("b", "乙", "person"),
        ("c", "丙", "person"),
        ("key", "铜钥匙", "item"),
        ("hall", "大厅", "location"),
        ("garden", "花园", "location"),
    ]:
        candidate = operations.propose_entity(name, entity_type, [], entity_id=entity_id)
        operations.accept_candidate(candidate, 1)
    evidence = {name: operations.locate(source_id, quote)[0] for name, quote in quotes.items()}
    return operations, evidence


def propose(operations, evidence: Evidence, **fields):
    defaults = {
        "title": evidence.quote,
        "action": "observe",
        "actor_id": "a",
        "evidence": evidence,
    }
    return operations.propose_event(**(defaults | fields))


def adopt(operations, evidence, **fields):
    candidate_id = propose(operations, evidence, **fields)
    review = operations.review_candidate(candidate_id)
    event_id = review.change.operations[0].id
    operations.accept_candidate(candidate_id, review.version)
    return event_id


def rules(diagnostics):
    return {diagnostic["rule_id"] for diagnostic in diagnostics}


def test_event_is_candidate_until_reviewed_and_survives_reopen(scene):
    operations, evidence = scene
    revision = operations.project_info().revision
    candidate = propose(
        operations,
        evidence["hall"],
        action="move",
        location_id="hall",
        time_kind="point",
        time_start=5,
    )
    assert operations.events() == []
    assert operations.timeline()["rows"] == []
    review = operations.review_candidate(candidate)
    assert review.differences[0]["before"] is None
    assert review.differences[0]["after"]["kind"] == "event"
    assert not [diagnostic for diagnostic in review.diagnostics if diagnostic.severity == "error"]
    assert operations.accept_candidate(candidate, review.version) == revision + 1
    assert operations.accept_candidate(candidate, review.version) == revision + 1
    reopened = Operations(operations.project_info().path)
    assert reopened.events() == operations.events()
    assert reopened.timeline()["rows"][0]["evidence"][0]["quote"] == evidence["hall"].quote


@pytest.mark.parametrize("damage", ["hash", "offset", "quote"])
def test_event_with_forged_evidence_cannot_be_accepted(scene, damage):
    operations, evidence = scene
    broken = evidence["observe"].model_copy(deep=True)
    if damage == "hash":
        broken.source_hash = "wrong-version"
    elif damage == "offset":
        broken.end += 1
    else:
        broken.quote = "未出现在原文中。"
    revision = operations.project_info().revision
    candidate = propose(operations, broken)
    assert any(
        diagnostic.rule_id == "EVID" and diagnostic.severity == "error"
        for diagnostic in operations.review_candidate(candidate).diagnostics
    )
    with pytest.raises(OperationError, match="证据"):
        operations.accept_candidate(candidate, 1)
    assert operations.project_info().revision == revision
    assert operations.events() == []


def test_story_order_differs_from_disclosure_order_and_relative_order_is_preserved(scene):
    operations, evidence = scene
    second = adopt(
        operations,
        evidence["second"],
        time_kind="point",
        time_start=20,
        disclosure_position=evidence["second"].start,
    )
    first = adopt(
        operations,
        evidence["first"],
        time_kind="point",
        time_start=10,
        disclosure_position=evidence["first"].start,
    )
    later = adopt(
        operations,
        evidence["observe"],
        time_kind="relative",
        after_event_id=second,
        disclosure_position=evidence["observe"].start,
    )
    story = operations.timeline(order="story")["rows"]
    disclosure = operations.timeline(order="disclosure")["rows"]
    assert [row["id"] for row in story] == [first, second, later]
    assert [row["id"] for row in disclosure] == [second, first, later]
    assert second in story[-1]["predecessors"]
    assert story[-1]["time_start"] is None


def test_unknown_initial_holder_is_information_gap_then_transfer_updates_state(scene):
    operations, evidence = scene
    first = adopt(
        operations,
        evidence["first"],
        action="transfer",
        actor_id="a",
        target_id="b",
        item_id="key",
        time_kind="point",
        time_start=10,
    )
    first_state = operations.event_state(first)
    assert first_state["before"]["holders"]["key"] is None
    assert first_state["after"]["holders"]["key"] == "b"
    assert any(
        diagnostic["rule_id"] == "STATE_UNKNOWN"
        and diagnostic["verdict"] == "insufficient"
        and diagnostic["severity"] != "error"
        for diagnostic in first_state["diagnostics"]
    )
    second = adopt(
        operations,
        evidence["second"],
        action="transfer",
        actor_id="b",
        target_id="c",
        item_id="key",
        time_kind="point",
        time_start=20,
    )
    second_state = operations.event_state(second)
    assert second_state["before"]["holders"]["key"] == "b"
    assert second_state["after"]["holders"]["key"] == "c"
    assert "STATE_PRECONDITION" not in rules(second_state["diagnostics"])


def test_known_wrong_holder_reports_conflict_and_does_not_change_state(scene):
    operations, evidence = scene
    adopt(
        operations,
        evidence["first"],
        action="transfer",
        actor_id="a",
        target_id="b",
        item_id="key",
        time_kind="point",
        time_start=10,
    )
    candidate = propose(
        operations,
        evidence["first"],
        action="transfer",
        actor_id="a",
        target_id="c",
        item_id="key",
        time_kind="point",
        time_start=20,
    )
    review = operations.review_candidate(candidate)
    assert any(
        diagnostic.rule_id == "STATE_PRECONDITION"
        and diagnostic.severity == "error"
        and diagnostic.verdict == "conflict"
        for diagnostic in review.diagnostics
    )
    revision = operations.project_info().revision
    with pytest.raises(OperationError):
        operations.accept_candidate(candidate, review.version)
    assert operations.project_info().revision == revision
    assert len(operations.events()) == 1


def test_disputed_transfer_preserves_contradictory_evidence_without_applying_it(scene):
    operations, evidence = scene
    adopt(
        operations,
        evidence["first"],
        action="transfer",
        actor_id="a",
        target_id="b",
        item_id="key",
        time_kind="point",
        time_start=10,
    )
    candidate = propose(
        operations,
        evidence["first"],
        action="transfer",
        actor_id="a",
        target_id="c",
        item_id="key",
        time_kind="point",
        time_start=20,
    )
    review = operations.review_candidate(candidate)
    edited = review.change.model_dump()
    edited["operations"][0]["status"] = "disputed"
    operations.edit_candidate(candidate, json.dumps(edited), review.version)
    review = operations.review_candidate(candidate)
    assert not [diagnostic for diagnostic in review.diagnostics if diagnostic.severity == "error"]
    assert any(
        diagnostic.verdict == "insufficient" and diagnostic.severity == "warning"
        for diagnostic in review.diagnostics
    )
    operations.accept_candidate(candidate, review.version)
    disputed = next(event for event in operations.events() if event["status"] == "disputed")
    assert disputed["evidence"] == [evidence["first"].model_dump()]
    state = operations.event_state(disputed["id"])
    assert state["before"]["holders"]["key"] == "b"
    assert state["after"]["holders"]["key"] == "b"


@pytest.mark.parametrize(
    "fields",
    [
        {"action": "move", "location_id": "key"},
        {"action": "move", "actor_id": "key", "location_id": "hall"},
        {"action": "transfer", "target_id": "hall", "item_id": "key"},
        {"action": "transfer", "target_id": "b", "item_id": "a"},
        {"action": "transfer", "target_id": "b", "item_id": "missing"},
    ],
)
def test_event_role_references_require_correct_entity_types(scene, fields):
    operations, evidence = scene
    candidate = propose(operations, evidence["observe"], **fields)
    with pytest.raises(OperationError):
        operations.accept_candidate(candidate, 1)
    assert operations.events() == []


def test_move_records_location_before_and_after_without_changing_item_holder(scene):
    operations, evidence = scene
    first = adopt(
        operations,
        evidence["hall"],
        action="move",
        actor_id="a",
        location_id="hall",
        time_kind="point",
        time_start=5,
    )
    second = adopt(
        operations,
        evidence["garden"],
        action="move",
        actor_id="a",
        location_id="garden",
        time_kind="relative",
        after_event_id=first,
    )
    state = operations.event_state(second)
    assert state["before"]["locations"]["a"] == "hall"
    assert state["after"]["locations"]["a"] == "garden"
    assert state["before"]["holders"] == state["after"]["holders"]
    assert state["after"]["locations"]["b"] is None


@pytest.mark.parametrize("first_time", [("point", 10, None), ("interval", 5, 15)])
def test_simultaneous_or_overlapping_events_do_not_invent_holder_order(scene, first_time):
    operations, evidence = scene
    kind, start, end = first_time
    first = adopt(
        operations,
        evidence["first"],
        action="transfer",
        actor_id="a",
        target_id="b",
        item_id="key",
        time_kind=kind,
        time_start=start,
        time_end=end,
    )
    second = adopt(
        operations,
        evidence["second"],
        action="transfer",
        actor_id="b",
        target_id="c",
        item_id="key",
        time_kind="point",
        time_start=10,
    )
    timeline = operations.timeline()
    assert "TIME_AMBIGUOUS" in rules(timeline["diagnostics"])
    for event_id in (first, second):
        state = operations.event_state(event_id)
        assert state["after"]["holders"]["key"] is None


def test_relative_time_cycle_is_visible_and_cannot_be_accepted(scene):
    operations, evidence = scene
    first = adopt(operations, evidence["observe"])
    second = adopt(operations, evidence["observe"], time_kind="relative", after_event_id=first)
    candidate = propose(
        operations, evidence["observe"], time_kind="relative", after_event_id=second
    )
    review = operations.review_candidate(candidate)
    edited = review.change.model_dump()
    edited["operations"][0]["id"] = first
    operations.edit_candidate(candidate, json.dumps(edited), review.version)
    review = operations.review_candidate(candidate)
    assert any(
        diagnostic.rule_id == "TIME_CYCLE" and diagnostic.severity == "error"
        for diagnostic in review.diagnostics
    )
    with pytest.raises(OperationError):
        operations.accept_candidate(candidate, review.version)
    assert (
        next(event for event in operations.events() if event["id"] == first)["time_kind"]
        == "unknown"
    )


def test_correcting_time_previews_new_order_and_preserves_old_time_in_revision(scene):
    operations, evidence = scene
    first = adopt(operations, evidence["observe"], time_kind="point", time_start=5)
    second = adopt(operations, evidence["hall"], time_kind="point", time_start=15)
    correction = operations.propose_event_correction(first)
    review = operations.review_candidate(correction)
    content = review.change.model_dump()
    content["operations"][0]["time_start"] = 25
    operations.edit_candidate(correction, json.dumps(content), review.version)
    review = operations.review_candidate(correction)
    assert review.differences[0]["before"]["time_start"] == 5
    assert review.differences[0]["after"]["time_start"] == 25
    operations.accept_candidate(correction, review.version)
    assert [event["id"] for event in operations.timeline()["rows"]] == [second, first]
    audit = operations.revisions()[-1]["data"][0]
    assert audit["before"]["time_start"] == 5
    assert audit["after"]["time_start"] == 25
    assert len(operations.events()) == 2


def test_later_determined_move_resolves_earlier_overlapping_locations(scene):
    operations, evidence = scene
    adopt(
        operations,
        evidence["hall"],
        action="move",
        location_id="hall",
        time_kind="interval",
        time_start=5,
        time_end=15,
    )
    adopt(
        operations,
        evidence["garden"],
        action="move",
        location_id="garden",
        time_kind="point",
        time_start=10,
    )
    later = adopt(
        operations,
        evidence["hall"],
        action="move",
        location_id="hall",
        time_kind="point",
        time_start=20,
    )
    state = operations.event_state(later)
    assert state["before"]["locations"]["a"] is None
    assert state["after"]["locations"]["a"] == "hall"


def test_entity_correction_cannot_break_existing_event_references(scene):
    operations, evidence = scene
    adopt(
        operations,
        evidence["first"],
        action="transfer",
        target_id="b",
        item_id="key",
        time_kind="point",
        time_start=10,
    )
    correction = operations.propose_entity("铜钥匙", "person", [], entity_id="key")
    with pytest.raises(OperationError, match="事件引用"):
        operations.accept_candidate(correction, 1)
    assert (
        next(entity for entity in operations.entities() if entity["id"] == "key")["entity_type"]
        == "item"
    )


def test_event_backup_restore_and_revision_audit(scene, tmp_path):
    operations, evidence = scene
    event_id = adopt(
        operations,
        evidence["hall"],
        action="move",
        location_id="hall",
        time_kind="point",
        time_start=5,
    )
    pending = propose(operations, evidence["observe"])
    backup = operations.backup(tmp_path / "backup.sqlite3")
    target = Operations.restore_file(backup, tmp_path / "restored.sqlite3")
    restored = Operations(target)
    assert restored.events() == operations.events()
    assert restored.event_state(event_id) == operations.event_state(event_id)
    assert restored.review_candidate(pending).change == operations.review_candidate(pending).change
    assert restored.revisions()[-1]["data"][0]["after"]["kind"] == "event"


def test_v1_database_open_and_restore_migrate_without_losing_existing_work(tmp_path):
    legacy_path = tmp_path / "legacy.sqlite3"
    legacy = Operations(legacy_path)
    source_id = legacy.import_text("旧章节", "甲仍在门外。")
    entity = legacy.propose_entity("甲", "person", [])
    legacy.accept_candidate(entity, 1)
    pending = legacy.propose_entity("乙", "person", [])
    expected_status = legacy.status()
    expected_revisions = legacy.revisions()
    # Remove only the v2 addition to reproduce a project created by v0.1.
    with sqlite3.connect(legacy_path) as database:
        database.execute("DROP TABLE IF EXISTS events")
        database.execute("PRAGMA user_version=1")
    restored_path = Operations.restore_file(legacy_path, tmp_path / "v1-restored.sqlite3")
    for project_path in (legacy_path, restored_path):
        migrated = Operations(project_path)
        assert migrated.status() == expected_status
        assert migrated.revisions() == expected_revisions
        assert migrated.source(source_id)["text"] == "甲仍在门外。"
        assert migrated.review_candidate(pending).version == 1
        assert migrated.events() == []
        with sqlite3.connect(project_path) as database:
            assert database.execute("PRAGMA user_version").fetchone()[0] == 3
            assert database.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_observation_without_actor_and_demo_candidate_are_supported(tmp_path):
    app = Operations(tmp_path / "demo.sqlite3")
    source = app.import_text("无人物事件", "门锁着。")
    candidate = app.propose_event(
        title="门锁着", action="observe", evidence=app.locate(source, "门锁着。")[0]
    )
    app.accept_candidate(candidate, 1)
    demo = app.propose_timeline_demo()
    assert app.propose_timeline_demo() == demo
    assert len(app.events()) == 1
    app.accept_candidate(demo, app.review_candidate(demo).version)
    assert len(app.events()) == 4
    assert len(app.status()["events"]) == 4
    with pytest.raises(OperationError, match="已采用"):
        app.propose_timeline_demo()
