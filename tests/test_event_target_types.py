"""Action roles stay typed while observation can point at a real non-person entity."""

import pytest

from narrative.models import Assertion, ChangeSet, Entity, Event
from narrative.service import Conflict, Service
from narrative.state import event_effects, query
from narrative.store import Store


@pytest.fixture
def project(tmp_path):
    service = Service(Store(tmp_path / "events.sqlite3"))
    source_id = service.import_text("原文", "甲读了信，看到狼和院子。")
    evidence = service.locate(source_id, "甲读了信，看到狼和院子。")
    entities = [
        Entity(id="reader", name="甲", entity_type="person"),
        Entity(id="recipient", name="乙", entity_type="person"),
        Entity(id="letter", name="信", entity_type="item"),
        Entity(id="wolf", name="狼", entity_type="other", subtype="animal"),
        Entity(id="yard", name="院子", entity_type="location"),
    ]
    change = ChangeSet(expected_revision=service.store.revision, operations=[*entities])
    service.propose(change)
    service.accept(change.id, 1)
    return service, evidence


def propose_event(service, event):
    change = ChangeSet(expected_revision=service.store.revision, operations=[event])
    service.propose(change)
    return change


@pytest.mark.parametrize("target", ["letter", "wolf", "yard", "recipient"])
def test_observe_accepts_existing_entity_targets_without_implicit_effects(project, target):
    service, evidence = project
    event = Event(
        title="甲观察目标",
        action="observe",
        actor_id="reader",
        target_id=target,
        time_kind="point",
        time_start=1,
        evidence=evidence,
    )
    change = propose_event(service, event)
    assert not [d for d in service.diagnostics(change) if d.severity == "error"]
    service.accept(change.id, 1)
    saved = service.store.objects("events")[0]
    assert saved["target_id"] == target
    assert event_effects(saved) == []

    snapshot = {
        "revision": service.store.revision,
        "entities": service.store.objects("entities"),
        "events": service.store.objects("events"),
        "assertions": [],
        "records": [],
    }
    result = query(snapshot, 2)
    assert result["holders"]["letter"] is None
    assert result["locations"]["reader"] is None
    assert result["knowledge"] == []


def test_observe_does_not_promote_observed_claim_to_world_truth(project):
    service, evidence = project
    claim = Assertion(
        id="claim",
        subject_id="wolf",
        predicate="危险",
        value=True,
        scope="claim",
        holder_id="recipient",
        evidence=evidence,
    )
    event = Event(
        title="甲看到狼",
        action="observe",
        actor_id="reader",
        target_id="wolf",
        assertion_id="claim",
        time_kind="point",
        time_start=1,
        evidence=evidence,
    )
    change = ChangeSet(expected_revision=service.store.revision, operations=[claim, event])
    service.propose(change)
    assert not [d for d in service.diagnostics(change) if d.severity == "error"]
    service.accept(change.id, 1)

    snapshot = {
        "revision": service.store.revision,
        "entities": service.store.objects("entities"),
        "events": service.store.objects("events"),
        "assertions": service.store.objects("assertions"),
        "records": [],
    }
    result = query(snapshot, 2)
    assert result["assertions"][0]["world_fact"] is False
    knowledge = next(
        item
        for item in result["knowledge"]
        if item["holder_id"] == "reader" and item["assertion_id"] == "claim"
    )
    assert knowledge["attitude"] == "informed"
    assert knowledge["acquisition"] == "observed"


@pytest.mark.parametrize(
    "action,fields",
    [
        ("transfer", {"actor_id": "reader", "target_id": "wolf", "item_id": "letter"}),
        ("tell", {"actor_id": "reader", "target_id": "letter", "assertion_id": "claim"}),
        ("dress", {"actor_id": "reader", "target_id": "letter", "item_id": "letter"}),
        ("injure", {"actor_id": "reader", "target_id": "wolf"}),
        ("treat", {"actor_id": "reader", "target_id": "wolf"}),
    ],
)
def test_non_observation_targets_keep_person_boundary(project, action, fields):
    service, evidence = project
    claim = Assertion(
        id="claim",
        subject_id="wolf",
        predicate="危险",
        value=True,
        evidence=evidence,
    )
    event = Event(title="错误目标", action=action, evidence=evidence, **fields)
    change = ChangeSet(expected_revision=service.store.revision, operations=[claim, event])
    service.propose(change)
    assert any(
        d.rule_id == "REF" and d.severity == "error" and "target_id 须为 person" in d.message
        for d in service.diagnostics(change)
    )
    with pytest.raises(Conflict, match="target_id 须为 person"):
        service.accept(change.id, 1)
    assert service.store.objects("events") == []


@pytest.mark.parametrize("field,value", [("target_id", "missing"), ("actor_id", "letter")])
def test_observe_still_requires_real_entity_and_person_actor(project, field, value):
    service, evidence = project
    kwargs = {"actor_id": "reader", "target_id": "letter"} | {field: value}
    event = Event.model_validate(
        {"title": "错误观察", "action": "observe", "evidence": evidence, **kwargs}
    )
    change = propose_event(service, event)
    assert any(d.rule_id == "REF" and d.severity == "error" for d in service.diagnostics(change))
    with pytest.raises(Conflict):
        service.accept(change.id, 1)
