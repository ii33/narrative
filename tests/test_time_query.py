"""Operation-boundary acceptance cases for querying a numeric story time."""

import json

import pytest

from narrative.harness import HarnessRunner
from narrative.operations import OperationError, Operations


@pytest.fixture
def scene(tmp_path):
    operations = Operations(tmp_path / "time-query.sqlite3")
    quotes = {
        "first": "甲把铜钥匙交给乙。",
        "second": "乙把铜钥匙交给丙。",
        "hall": "甲走进大厅。",
        "garden": "甲走进花园。",
        "anchor": "乙看了看手表。",
    }
    source_id = operations.import_text("时间查询样本", "\n".join(quotes.values()))
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


def propose(operations, evidence, **fields):
    return operations.propose_event(
        **({"title": evidence.quote, "action": "observe", "evidence": evidence} | fields)
    )


def adopt(operations, evidence, *, status="accepted", **fields):
    candidate = propose(operations, evidence, **fields)
    review = operations.review_candidate(candidate)
    if status != "accepted":
        change = review.change.model_dump()
        change["operations"][0]["status"] = status
        operations.edit_candidate(candidate, json.dumps(change), review.version)
        review = operations.review_candidate(candidate)
    event_id = review.change.operations[0].id
    operations.accept_candidate(candidate, review.version)
    return event_id


def move(operations, evidence, location="hall", **fields):
    return adopt(operations, evidence, action="move", actor_id="a", location_id=location, **fields)


def transfer(operations, evidence, actor="a", target="b", **fields):
    return adopt(
        operations,
        evidence,
        action="transfer",
        actor_id=actor,
        target_id=target,
        item_id="key",
        **fields,
    )


def test_before_first_transfer_remains_unknown_without_reverse_inference(scene):
    operations, evidence = scene
    transfer(operations, evidence["first"], time_kind="point", time_start=10)
    result = operations.state_at_time(5)
    assert result["holders"]["key"] is None
    assert result["locations"] == {"a": None, "b": None, "c": None}
    assert result["reasons"]["holders"]["key"]
    assert result["evidence"]["holders"]["key"] == []


def test_future_transfer_diagnostics_appear_only_when_the_event_has_happened(scene):
    operations, evidence = scene
    event_id = transfer(operations, evidence["first"], time_kind="point", time_start=10)
    before = operations.state_at_time(10, boundary="before")
    assert not [d for d in before["diagnostics"] if d["object_id"] == event_id]
    after = operations.state_at_time(10, boundary="after")
    assert any(
        d["object_id"] == event_id and d["rule_id"] == "STATE_UNKNOWN" for d in after["diagnostics"]
    )


@pytest.mark.parametrize(
    ("minute", "boundary", "holder"),
    [
        (9, "after", None),
        (10, "before", None),
        (10, "after", "b"),
        (15, "after", "b"),
        (20, "before", "b"),
        (20, "after", "c"),
        (50, "after", "c"),
    ],
)
def test_transfer_chain_and_exact_time_boundaries(scene, minute, boundary, holder):
    operations, evidence = scene
    transfer(operations, evidence["first"], time_kind="point", time_start=10)
    transfer(
        operations, evidence["second"], actor="b", target="c", time_kind="point", time_start=20
    )
    assert operations.state_at_time(minute, boundary=boundary)["holders"]["key"] == holder


@pytest.mark.parametrize(
    ("minute", "boundary", "location"),
    [
        (5, "before", "garden"),
        (5, "after", None),
        (10, "after", None),
        (15, "before", None),
        (15, "after", "hall"),
        (16, "before", "hall"),
    ],
)
def test_move_interval_is_uncertain_until_complete(scene, minute, boundary, location):
    operations, evidence = scene
    move(operations, evidence["garden"], "garden", time_kind="point", time_start=0)
    move(operations, evidence["hall"], time_kind="interval", time_start=5, time_end=15)
    result = operations.state_at_time(minute, boundary=boundary)
    assert result["locations"]["a"] == location
    assert result["reasons"]["locations"]["a"]
    if location is None:
        assert any(d["verdict"] == "insufficient" for d in result["diagnostics"])


@pytest.mark.parametrize(
    "first_time",
    [
        {"time_kind": "point", "time_start": 10},
        {"time_kind": "interval", "time_start": 5, "time_end": 15},
    ],
)
def test_same_time_and_overlapping_moves_do_not_choose_a_display_order(scene, first_time):
    operations, evidence = scene
    move(operations, evidence["hall"], **first_time)
    move(operations, evidence["garden"], "garden", time_kind="point", time_start=10)
    result = operations.state_at_time(30)
    assert result["locations"]["a"] is None
    assert any(d["verdict"] == "insufficient" for d in result["diagnostics"])


def test_later_determined_move_resolves_prior_overlap_and_has_its_evidence(scene):
    operations, evidence = scene
    move(operations, evidence["hall"], time_kind="interval", time_start=5, time_end=15)
    move(operations, evidence["garden"], "garden", time_kind="point", time_start=10)
    move(operations, evidence["hall"], time_kind="point", time_start=20)
    result = operations.state_at_time(25)
    assert result["locations"]["a"] == "hall"
    assert result["evidence"]["locations"]["a"] == [evidence["hall"].model_dump()]


def test_unknown_time_only_obscures_the_resource_it_may_change(scene):
    operations, evidence = scene
    transfer(operations, evidence["first"], time_kind="point", time_start=10)
    move(operations, evidence["hall"], time_kind="point", time_start=5)
    move(operations, evidence["garden"], "garden")
    result = operations.state_at_time(20)
    assert result["locations"]["a"] is None
    assert result["holders"]["key"] == "b"
    assert result["reasons"]["locations"]["a"]


def test_relative_event_anchored_in_future_does_not_pollute_past(scene):
    operations, evidence = scene
    move(operations, evidence["hall"], time_kind="point", time_start=0)
    anchor = adopt(operations, evidence["anchor"], time_kind="point", time_start=50)
    move(operations, evidence["garden"], "garden", time_kind="relative", after_event_id=anchor)
    assert operations.state_at_time(20)["locations"]["a"] == "hall"
    assert operations.state_at_time(60)["locations"]["a"] is None


def test_unknown_event_constrained_before_completed_point_is_overridden(scene):
    operations, evidence = scene
    earlier = move(operations, evidence["garden"], "garden")
    move(operations, evidence["hall"], time_kind="point", time_start=10, after_event_id=earlier)
    result = operations.state_at_time(20)
    assert result["locations"]["a"] == "hall"
    assert result["evidence"]["locations"]["a"] == [evidence["hall"].model_dump()]


@pytest.mark.parametrize("status", ["disputed", "superseded"])
def test_nonactive_event_does_not_change_or_obscure_state(scene, status):
    operations, evidence = scene
    move(operations, evidence["hall"], time_kind="point", time_start=0)
    move(operations, evidence["garden"], "garden", status=status)
    assert operations.state_at_time(5)["locations"]["a"] == "hall"


def test_pending_candidate_does_not_change_query_and_rejection_preserves_it(scene):
    operations, evidence = scene
    move(operations, evidence["hall"], time_kind="point", time_start=0)
    before = operations.state_at_time(20)
    candidate = propose(
        operations,
        evidence["garden"],
        action="move",
        actor_id="a",
        location_id="garden",
        time_kind="point",
        time_start=10,
    )
    assert operations.state_at_time(20) == before
    operations.reject_candidate(candidate, operations.review_candidate(candidate).version)
    assert operations.state_at_time(20) == before


def test_evidence_is_source_backed_and_query_is_deterministic_without_model(scene, monkeypatch):
    operations, evidence = scene
    transfer(operations, evidence["first"], time_kind="point", time_start=10)
    move(operations, evidence["hall"], time_kind="point", time_start=5)

    def forbid_model(*args, **kwargs):
        pytest.fail("状态查询不应调用模型")

    monkeypatch.setattr(HarnessRunner, "extract", forbid_model)
    revision = operations.project_info().revision
    history = operations.revisions()
    result = operations.state_at_time(20)
    assert operations.state_at_time(20) == result
    assert Operations(operations.project_info().path).state_at_time(20) == result
    assert operations.project_info().revision == revision
    assert operations.revisions() == history
    for kind, ref, expected in [("holders", "key", "first"), ("locations", "a", "hall")]:
        assert result["evidence"][kind][ref] == [evidence[expected].model_dump()]
        saved = result["evidence"][kind][ref][0]
        source = operations.source(saved["source_id"])
        assert source["text"][saved["start"] : saved["end"]] == saved["quote"]
        assert result["reasons"][kind][ref]


def test_time_correction_recomputes_query_only_after_acceptance(scene):
    operations, evidence = scene
    event_id = move(operations, evidence["hall"], time_kind="point", time_start=10)
    assert operations.state_at_time(15)["locations"]["a"] == "hall"
    correction = operations.propose_event_correction(event_id)
    review = operations.review_candidate(correction)
    change = review.change.model_dump()
    change["operations"][0]["time_start"] = 20
    operations.edit_candidate(correction, json.dumps(change), review.version)
    assert operations.state_at_time(15)["locations"]["a"] == "hall"
    operations.accept_candidate(correction, operations.review_candidate(correction).version)
    assert operations.state_at_time(15)["locations"]["a"] is None
    assert operations.state_at_time(20)["locations"]["a"] == "hall"


def test_backup_restore_preserves_time_queries_with_uncertain_intervals(scene, tmp_path):
    operations, evidence = scene
    transfer(operations, evidence["first"], time_kind="point", time_start=10)
    move(operations, evidence["hall"], time_kind="interval", time_start=5, time_end=15)
    backup = operations.backup(tmp_path / "backup.sqlite3")
    restored_path = Operations.restore_file(backup, tmp_path / "restored.sqlite3")
    restored = Operations(restored_path)
    for minute, boundary in [(0, "after"), (10, "after"), (15, "before"), (20, "after")]:
        assert restored.state_at_time(minute, boundary=boundary) == operations.state_at_time(
            minute, boundary=boundary
        )


@pytest.mark.parametrize("minute", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_query_time_is_rejected(scene, minute):
    operations, _ = scene
    with pytest.raises(OperationError):
        operations.state_at_time(minute)


@pytest.mark.parametrize("boundary", ["during", "", "BEFORE"])
def test_invalid_query_boundary_is_rejected(scene, boundary):
    operations, _ = scene
    with pytest.raises(OperationError):
        operations.state_at_time(10, boundary=boundary)
