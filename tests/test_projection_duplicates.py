"""Equivalent event/record representations preserve truth and provenance."""

from copy import deepcopy

import pytest

from narrative.models import Assertion, Entity, Event, KnowledgeRecord, StateFact
from narrative.state import query, query_event

EVENT_PROOF = {
    "source_id": "chapter",
    "source_hash": "hash",
    "start": 0,
    "end": 5,
    "quote": "甲穿上外套",
}
RECORD_PROOF = EVENT_PROOF | {"start": 6, "end": 11, "quote": "甲穿着外套"}


@pytest.fixture
def scene():
    return {
        "entities": [
            Entity(id=identity, name=identity, entity_type=kind).model_dump()
            for identity, kind in [
                ("a", "person"),
                ("b", "person"),
                ("c", "person"),
                ("coat", "item"),
                ("other-coat", "item"),
                ("hall", "location"),
            ]
        ],
        "assertions": [
            Assertion(
                id="claim",
                subject_id="coat",
                predicate="red",
                value=True,
                scope="claim",
                holder_id="b",
                evidence=[EVENT_PROOF],
            ).model_dump()
        ],
        "events": [],
        "records": [],
    }


def add_event(scene, identity="event", action="dress", **fields):
    obj = Event(
        **(
            {
                "id": identity,
                "title": identity,
                "action": action,
                "actor_id": "a",
                "item_id": "coat",
                "time_kind": "point",
                "time_start": 10,
                "evidence": [EVENT_PROOF],
            }
            | fields
        )
    ).model_dump()
    scene["events"].append(obj)
    return obj


def add_state(scene, **fields):
    obj = StateFact(
        **(
            {
                "id": "state",
                "subject_id": "a",
                "property_id": "outfit",
                "value_entity_id": "coat",
                "persistence": "unknown",
                "validity": {"start_event_id": "event"},
                "evidence": [RECORD_PROOF],
            }
            | fields
        )
    ).model_dump()
    scene["records"].append(obj)
    return obj


def add_knowledge(scene, **fields):
    obj = KnowledgeRecord(
        **(
            {
                "id": "knowledge",
                "holder_id": "a",
                "assertion_id": "claim",
                "attitude": "informed",
                "acquisition": "observed",
                "validity": {"start_event_id": "event"},
                "event_id": "event",
                "evidence": [RECORD_PROOF],
            }
            | fields
        )
    ).model_dump()
    scene["records"].append(obj)
    return obj


def outfit(result):
    return next(
        value
        for value in result["states"]
        if value["subject_id"] == "a" and value["property_id"] == "outfit"
    )


def knowledge(result):
    return next(
        value
        for value in result["knowledge"]
        if value["holder_id"] == "a" and value["assertion_id"] == "claim"
    )


def test_same_outfit_event_and_state_share_projection_not_storage(scene):
    add_event(scene)
    add_state(scene)
    original = deepcopy(scene)
    result = outfit(query_event(scene, "event")["after"])
    assert result["status"] == "known" and result["value_entity_id"] == "coat"
    assert set(result["record_ids"]) == {"event", "state"}
    assert result["evidence"] == [RECORD_PROOF, EVENT_PROOF]
    assert scene == original
    assert outfit(query_event(scene, "event")["before"])["status"] == "unknown"


@pytest.mark.parametrize("persistence", ["unknown", "instant"])
def test_same_transient_outfit_does_not_extend_after_completion(scene, persistence):
    add_event(scene, persistence=persistence)
    add_state(scene, persistence=persistence)
    assert outfit(query(scene, 10))["status"] == "known"
    later = outfit(query(scene, 11))
    assert later["status"] == "unknown" and later["value_entity_id"] is None
    assert set(later["record_ids"]) == {"event", "state"}


def test_same_relative_anchor_known_only_at_its_observed_completion(scene):
    add_event(scene, identity="previous", action="observe", time_start=1)
    add_event(scene, time_kind="relative", time_start=None, after_event_id="previous")
    add_state(scene)
    add_event(
        scene,
        identity="later",
        action="observe",
        time_kind="relative",
        time_start=None,
        after_event_id="event",
    )
    assert outfit(query_event(scene, "event")["after"])["status"] == "known"
    assert outfit(query_event(scene, "later")["after"])["status"] == "unknown"


def test_same_bounded_outfit_expires_without_resurrecting_previous_value(scene):
    add_event(
        scene, identity="old", item_id="other-coat", time_start=1, persistence="until_changed"
    )
    add_event(scene, valid_until=20)
    add_state(scene, persistence="bounded", validity={"start_event_id": "event", "end": 20})
    assert outfit(query(scene, 20, "before"))["value_entity_id"] == "coat"
    expired = outfit(query(scene, 20))
    assert expired["status"] == "unknown" and expired["temporal_status"] == "expired"
    assert expired["value_entity_id"] is None
    assert set(expired["record_ids"]) == {"event", "state"}


def test_same_until_changed_outfit_keeps_its_declared_persistence(scene):
    add_event(scene, persistence="until_changed")
    add_state(scene, persistence="until_changed")
    assert outfit(query(scene, 100))["value_entity_id"] == "coat"


@pytest.mark.parametrize(
    "fields",
    [
        {"value_entity_id": "other-coat"},
        {"persistence": "until_changed"},
        {"persistence": "bounded", "validity": {"start_event_id": "event", "end": 20}},
    ],
)
def test_different_value_or_persistence_is_not_merged(scene, fields):
    add_event(scene)
    add_state(scene, **fields)
    result = outfit(query(scene, 10))
    assert result["status"] == "conflict"
    assert set(result["record_ids"]) == {"event", "state"}
    assert len(result["evidence"]) == 2


def test_different_bounded_ends_are_not_combined_into_longer_validity(scene):
    add_event(scene, valid_until=20)
    add_state(scene, persistence="bounded", validity={"start_event_id": "event", "end": 30})
    result = outfit(query(scene, 25))
    assert result["status"] == "conflict" and result["value_entity_id"] is None


def test_same_value_at_two_unordered_events_is_not_one_anchor(scene):
    add_event(scene)
    add_event(scene, identity="another")
    assert outfit(query(scene, 10))["status"] == "conflict"


def test_repeated_implicit_explicit_effect_has_unique_record_ids(scene):
    add_event(
        scene,
        effects=[
            {
                "subject_id": "a",
                "property_id": "outfit",
                "value_entity_id": "coat",
                "persistence": "unknown",
            }
        ],
    )
    result = outfit(query(scene, 10))
    assert result["status"] == "known" and result["record_ids"] == ["event"]


def test_same_knowledge_acquisition_keeps_all_evidence_and_claim_scope(scene):
    add_event(scene, action="observe", assertion_id="claim")
    add_knowledge(scene)
    result = query_event(scene, "event")["after"]
    acquired = knowledge(result)
    assert (acquired["status"], acquired["attitude"], acquired["acquisition"]) == (
        "known",
        "informed",
        "observed",
    )
    assert set(acquired["record_ids"]) == {"$event:event", "knowledge"}
    assert acquired["evidence"] == [RECORD_PROOF, EVENT_PROOF]
    assert not result["assertions"][0]["world_fact"]
    assert knowledge(query_event(scene, "event")["before"])["status"] == "unknown"


def test_knowledge_event_anchor_can_be_expressed_by_validity_alone(scene):
    add_event(scene, action="observe", assertion_id="claim")
    add_knowledge(scene, event_id=None)
    assert knowledge(query(scene, 11))["status"] == "known"


@pytest.mark.parametrize(
    "fields",
    [
        {"attitude": "denies"},
        {"attitude": "knows"},
        {"acquisition": "read"},
        {"source_character_id": "b"},
        {"validity": {"start_event_id": "event", "end": 20}},
    ],
)
def test_incompatible_knowledge_is_not_merged(scene, fields):
    add_event(scene, action="observe", assertion_id="claim")
    add_knowledge(scene, **fields)
    result = knowledge(query(scene, 11))
    assert result["status"] == "conflict"
    assert result["attitude"] == "unknown" and result["acquisition"] == "unknown"
    assert set(result["record_ids"]) == {"$event:event", "knowledge"}


def test_tell_and_read_remain_incompatible_channels(scene):
    add_event(scene, action="tell", actor_id="b", target_id="a", assertion_id="claim")
    add_knowledge(scene, acquisition="read", source_character_id="b")
    assert knowledge(query(scene, 11))["status"] == "conflict"


def test_same_told_source_is_merged_without_promoting_informed_to_knows(scene):
    add_event(scene, action="tell", actor_id="b", target_id="a", assertion_id="claim")
    add_knowledge(scene, acquisition="told", source_character_id="b")
    result = knowledge(query(scene, 11))
    assert result["status"] == "known" and result["attitude"] == "informed"
    assert result["source_character_id"] == "b" and result["acquisition"] == "told"


def test_same_bounded_knowledge_expires_without_reviving_earlier_attitude(scene):
    add_event(
        scene,
        identity="old",
        action="observe",
        time_start=1,
        assertion_id="claim",
        knowledge_attitude="knows",
    )
    add_event(scene, action="observe", assertion_id="claim", valid_until=20)
    add_knowledge(scene, validity={"start_event_id": "event", "end": 20})
    assert knowledge(query(scene, 20, "before"))["status"] == "known"
    result = knowledge(query(scene, 20))
    assert result["status"] == "expired" and result["attitude"] == "unknown"
    assert set(result["record_ids"]) == {"$event:event", "knowledge"}


def test_same_knowledge_with_unresolved_anchor_does_not_become_known(scene):
    add_event(scene, action="observe", assertion_id="claim", time_kind="unknown", time_start=None)
    add_knowledge(scene)
    result = knowledge(query(scene, 100))
    assert result["status"] == "unknown" and result["attitude"] == "unknown"


def test_duplicate_prior_acquisition_satisfies_knowledge_used(scene):
    add_event(scene, action="observe", assertion_id="claim")
    add_knowledge(scene)
    add_event(
        scene,
        identity="uses",
        action="move",
        time_start=20,
        location_id="hall",
        knowledge_used=["claim"],
    )
    result = query(scene, 21)
    assert result["locations"]["a"] == "hall"
    assert not any(d["rule_id"] == "KNOWLEDGE_PATH" for d in result["diagnostics"])
