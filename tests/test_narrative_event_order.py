"""Narrative actions and reviewed story-time anchors have bounded semantics."""

import pytest
from pydantic import ValidationError

from narrative.models import ChangeSet, Entity, Event, StateFact
from narrative.operations import Operations
from narrative.service import Service
from narrative.state import event_effects, query_event
from narrative.store import Store


def adopt(service, *operations):
    change = ChangeSet(expected_revision=service.store.revision, operations=list(operations))
    service.propose(change)
    assert not [d for d in service.diagnostics(change) if d.severity == "error"]
    service.accept(change.id, 1)


@pytest.fixture
def project(tmp_path):
    service = Service(Store(tmp_path / "narrative-events.sqlite3"))
    source = service.import_text(
        "原文", "甲写信给乙。甲两手攀着月台，后来甲走到铁道边，再后来甲回到车上。"
    )
    adopt(
        service,
        Entity(id="a", name="甲", entity_type="person"),
        Entity(id="b", name="乙", entity_type="person"),
        Entity(id="letter", name="信", entity_type="item"),
        Entity(id="platform", name="月台", entity_type="location"),
        Entity(id="track", name="铁道边", entity_type="location"),
        Entity(id="car", name="车上", entity_type="location"),
    )
    return service, source


def snapshot(service):
    return {
        "revision": service.store.revision,
        "entities": service.store.objects("entities"),
        "assertions": service.store.objects("assertions"),
        "events": service.store.objects("events"),
        "records": service.store.objects("records"),
    }


def test_act_records_writing_and_climbing_without_physical_or_knowledge_effects(project):
    service, source = project
    adopt(
        service,
        Event(
            id="write",
            title="甲写信给乙",
            action="act",
            actor_id="a",
            target_id="b",
            item_id="letter",
            evidence=service.locate(source, "甲写信给乙。"),
        ),
        Event(
            id="climb",
            title="甲攀着月台",
            action="act",
            actor_id="a",
            target_id="platform",
            evidence=service.locate(source, "甲两手攀着月台"),
        ),
    )
    result = query_event(snapshot(service), "climb")
    assert all(event_effects(event) == [] for event in service.store.objects("events"))
    assert result["after"]["locations"]["a"] is None
    assert result["after"]["holders"]["letter"] is None
    assert result["after"]["knowledge"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"assertion_id": "claim"},
        {"effects": [{"subject_id": "a", "property_id": "location", "value_entity_id": "car"}]},
        {"preconditions": [{"subject_id": "a", "property_id": "location"}]},
        {"knowledge_used": ["claim"]},
        {"state_value": "完成"},
        {"persistence": "until_changed"},
        {"valid_until": 3},
        {"slot": "injury"},
        {"knowledge_attitude": "knows"},
    ],
)
def test_act_rejects_state_and_epistemic_payload(payload):
    with pytest.raises(ValidationError, match="不能附带状态或认知效果"):
        Event.model_validate({"title": "甲写信", "action": "act", "actor_id": "a", **payload})


def test_act_still_requires_person_actor_and_existing_typed_roles(project):
    service, source = project
    evidence = service.locate(source, "甲写信给乙。")
    with pytest.raises(ValidationError, match="须指定行动人物"):
        Event(title="写信", action="act", evidence=evidence)
    for fields in (
        {"actor_id": "letter"},
        {"actor_id": "a", "target_id": "missing"},
        {"actor_id": "a", "item_id": "b"},
        {"actor_id": "a", "location_id": "letter"},
    ):
        event = Event.model_validate(
            {"title": "非法角色", "action": "act", "evidence": evidence, **fields}
        )
        change = ChangeSet(expected_revision=service.store.revision, operations=[event])
        assert any(d.rule_id == "REF" and d.severity == "error" for d in service.diagnostics(change))


def test_evidenced_order_and_state_anchor_resolve_only_supported_event_cuts(project):
    service, source = project
    adopt(
        service,
        Event(
            id="edge",
            title="甲走到铁道边",
            action="move",
            actor_id="a",
            location_id="track",
            evidence=service.locate(source, "甲走到铁道边"),
        ),
        Event(
            id="board",
            title="甲回到车上",
            action="move",
            actor_id="a",
            location_id="car",
            evidence=service.locate(source, "甲回到车上"),
        ),
        StateFact(
            id="track_state",
            subject_id="a",
            property_id="location",
            value_entity_id="track",
            persistence="until_changed",
            evidence=service.locate(source, "甲走到铁道边"),
        ),
        StateFact(
            id="car_state",
            subject_id="a",
            property_id="location",
            value_entity_id="car",
            persistence="until_changed",
            evidence=service.locate(source, "甲回到车上"),
        ),
    )
    app = Operations(service.store.path)
    assert app.full_event_state("board")["before"]["locations"]["a"] is None
    order = app.propose_event_order(
        "board", "edge", service.locate(source, "后来甲走到铁道边，再后来甲回到车上。")[0]
    )
    assert service.candidate(order)[0].source == "correction"
    assert app.review_candidate(order).version == 1
    assert next(event for event in app.events() if event["id"] == "board")["after_event_id"] is None
    app.accept_candidate(order, 1)
    for state_id, anchor, quote in (
        ("track_state", "edge", "甲走到铁道边"),
        ("car_state", "board", "甲回到车上"),
    ):
        change_id = app.propose_state_window(
            state_id, start_event_id=anchor, evidence=service.locate(source, quote)[0]
        )
        assert next(record for record in app.records("state") if record["id"] == state_id)[
            "validity"
        ]["start_event_id"] is None
        app.accept_candidate(change_id, 1)
    result = app.full_event_state("board")
    assert result["before"]["locations"]["a"] == "track"
    assert result["after"]["locations"]["a"] == "car"
    assert app.state_at_time(0)["locations"]["a"] is None


def test_order_revision_rejects_bad_evidence_missing_refs_and_cycles(project):
    service, source = project
    adopt(
        service,
        Event(
            id="first", title="甲写信", action="act", actor_id="a",
            evidence=service.locate(source, "甲写信给乙。"),
        ),
        Event(
            id="second", title="甲攀着月台", action="act", actor_id="a",
            evidence=service.locate(source, "甲两手攀着月台"),
        ),
    )
    evidence = service.locate(source, "甲写信给乙。")[0]
    with pytest.raises(ValueError, match="精确对应原文"):
        service.propose_event_order("second", "first", evidence.model_copy(update={"quote": "误引"}))
    with pytest.raises(ValueError, match="先行事件不存在"):
        service.propose_event_order("second", "missing", evidence)
    with pytest.raises(ValueError, match="不能先于自身"):
        service.propose_event_order("first", "first", evidence)
    first = service.propose_event_order("second", "first", evidence)
    service.accept(first, 1)
    change_count = len(service.store.rows("changes"))
    revision = service.store.revision
    with pytest.raises(ValueError, match="成环"):
        service.propose_event_order("first", "second", evidence)
    assert len(service.store.rows("changes")) == change_count
    assert service.store.revision == revision
    assert next(event for event in service.store.objects("events") if event["id"] == "first")[
        "after_event_id"
    ] is None
    assert len(service.store.objects("events")) == 2


def test_state_window_rejects_unordered_bounds_without_invented_sequence(project):
    service, source = project
    adopt(
        service,
        Event(
            id="first", title="甲写信", action="act", actor_id="a",
            evidence=service.locate(source, "甲写信给乙。"),
        ),
        Event(
            id="second", title="甲攀着月台", action="act", actor_id="a",
            evidence=service.locate(source, "甲两手攀着月台"),
        ),
        StateFact(
            id="place",
            subject_id="a",
            property_id="location",
            value_entity_id="platform",
            evidence=service.locate(source, "甲两手攀着月台"),
        ),
    )
    evidence = service.locate(source, "甲两手攀着月台")[0]
    change_count = len(service.store.rows("changes"))
    revision = service.store.revision
    with pytest.raises(ValueError, match="至少一个事件锚点"):
        service.propose_state_window("place", evidence=evidence)
    with pytest.raises(ValueError, match="已采用事件"):
        service.propose_state_window("place", start_event_id="missing", evidence=evidence)
    with pytest.raises(ValueError, match="明确先后关系"):
        service.propose_state_window(
            "place", start_event_id="first", end_event_id="second", evidence=evidence
        )
    assert len(service.store.rows("changes")) == change_count
    assert service.store.revision == revision
    assert service.store.objects("records")[0]["validity"]["start_event_id"] is None
