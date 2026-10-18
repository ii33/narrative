"""Full model acceptance cases: values require evidence and temporal support."""

from copy import deepcopy

import pytest

from narrative.models import (
    Assertion,
    Entity,
    Event,
    KnowledgeRecord,
    PropertyDefinition,
    Relation,
    StateFact,
    WorldRule,
)
from narrative.state import query, query_event

PROOF = {"source_id": "chapter", "source_hash": "hash", "start": 0, "end": 5, "quote": "原著证据。"}


@pytest.fixture
def scene():
    return {
        "revision": 7,
        "entities": [
            Entity(id=entity_id, name=entity_id, entity_type=kind).model_dump()
            for entity_id, kind in [
                ("a", "person"),
                ("b", "person"),
                ("c", "person"),
                ("key", "item"),
                ("coat", "item"),
                ("hall", "location"),
                ("garden", "location"),
            ]
        ],
        "events": [],
        "assertions": [],
        "records": [],
    }


def event(scene, event_id, action="observe", at=10, **fields):
    timing = (
        {"time_kind": "point", "time_start": at} if at is not None else {"time_kind": "unknown"}
    )
    obj = Event(
        id=event_id, title=event_id, action=action, evidence=[PROOF], **(timing | fields)
    ).model_dump()
    scene["events"].append(obj)
    return obj


def record(scene, cls, **fields):
    obj = cls(evidence=[PROOF], **fields).model_dump()
    scene["records"].append(obj)
    return obj


def prop(scene, property_id="awake", **fields):
    return record(
        scene,
        PropertyDefinition,
        id=property_id,
        name=property_id,
        value_type="boolean",
        subject_types=["person"],
        **fields,
    )


def state(result, subject="a", property_id="outfit"):
    return next(
        obj
        for obj in result["states"]
        if obj["subject_id"] == subject and obj["property_id"] == property_id
    )


def assertion(scene, assertion_id="secret", **fields):
    obj = Assertion(
        **(
            {
                "id": assertion_id,
                "subject_id": "key",
                "predicate": "hidden",
                "value": True,
                "evidence": [PROOF],
            }
            | fields
        )
    ).model_dump()
    scene["assertions"].append(obj)
    return obj


def test_legacy_values_and_revision_are_kept_without_initial_assumptions(scene):
    result = query(scene, 0)
    assert result["holders"] == {"key": None, "coat": None}
    assert result["locations"] == {"a": None, "b": None, "c": None}
    assert result["revision"] == 7
    assert all(obj["reason"] and obj["evidence"] == [] for obj in result["states"])


def test_move_transfer_persist_but_never_prove_earlier_state(scene):
    event(scene, "move", "move", actor_id="a", location_id="hall")
    event(scene, "give", "transfer", actor_id="a", target_id="b", item_id="key")
    assert query(scene, 9)["locations"]["a"] is None
    result = query(scene, 100)
    assert result["locations"]["a"] == "hall"
    assert result["holders"]["key"] == "b"
    assert result["evidence"]["holders"]["key"] == [PROOF]


@pytest.mark.parametrize(
    "action,property_id,value", [("injure", "injury:arm", "划伤"), ("treat", "injury:arm", "无伤")]
)
def test_transient_states_need_explicit_persistence(scene, action, property_id, value):
    event(scene, "change", action, actor_id="a", target_id="a", slot="arm", state_value=value)
    at = state(query(scene, 10), property_id=property_id)
    later = state(query(scene, 11), property_id=property_id)
    assert at["status"] == "known" and at["value"] == value
    assert later["status"] == "unknown" and later["evidence"] == [PROOF]
    assert "持续" in later["reason"]


def test_dress_can_reference_an_item_and_persist_explicitly(scene):
    event(scene, "dress", "dress", actor_id="a", item_id="coat", persistence="until_changed")
    result = state(query(scene, 100))
    assert result["status"] == "known" and result["value_entity_id"] == "coat"


def test_treatment_without_outcome_does_not_prove_recovery(scene):
    event(scene, "hurt", "injure", at=1, target_id="a", slot="leg", persistence="until_changed")
    event(scene, "treat", "treat", at=2, target_id="a", slot="leg", persistence="until_changed")
    result = state(query(scene, 3), property_id="injury:leg")
    assert result["status"] == "unknown"
    assert result["value"] is None and "未证明" in result["reason"]


@pytest.mark.parametrize(
    "minute,boundary,known",
    [
        (9, "after", False),
        (10, "before", False),
        (10, "after", True),
        (19, "after", True),
        (20, "before", True),
        (20, "after", False),
        (21, "after", False),
    ],
)
def test_state_validity_is_inclusive_start_exclusive_end(scene, minute, boundary, known):
    event(scene, "dress", "dress", actor_id="a", item_id="coat", valid_until=20)
    result = state(query(scene, minute, boundary))
    assert (result["status"] == "known") is known
    if minute >= 20 and boundary == "after":
        assert result.get("temporal_status") == "expired"


def test_expired_new_state_never_resurrects_old_outfit(scene):
    event(scene, "old", "dress", at=1, actor_id="a", item_id="key", persistence="until_changed")
    event(scene, "new", "dress", at=10, actor_id="a", item_id="coat", valid_until=20)
    result = state(query(scene, 21))
    assert result["status"] == "unknown" and result["value"] is None
    assert result["record_ids"] == ["new"]


def test_custom_boolean_property_keeps_false_distinct_from_unknown(scene):
    prop(scene, persistence="until_changed")
    record(
        scene,
        StateFact,
        id="asleep",
        subject_id="a",
        property_id="awake",
        value=False,
        validity={"start": 1},
    )
    known = state(query(scene, 2), property_id="awake")
    missing = state(query(scene, 2), subject="b", property_id="awake")
    assert known["status"] == "known" and known["value"] is False
    assert missing["status"] == "unknown" and missing["value"] is None


def test_state_fact_default_persistence_and_missing_time_are_conservative(scene):
    prop(scene)
    record(
        scene,
        StateFact,
        id="fact",
        subject_id="a",
        property_id="awake",
        value=True,
        validity={"start": 1},
    )
    assert state(query(scene, 1), property_id="awake")["value"] is True
    assert state(query(scene, 2), property_id="awake")["status"] == "unknown"
    record(
        scene,
        StateFact,
        id="untimed",
        subject_id="b",
        property_id="awake",
        value=True,
        persistence="until_changed",
    )
    assert state(query(scene, 100), subject="b", property_id="awake")["status"] == "unknown"


def test_fact_event_anchor_and_end_event_anchor(scene):
    prop(scene)
    event(scene, "start", at=10)
    event(scene, "end", at=20)
    record(
        scene,
        StateFact,
        id="fact",
        subject_id="a",
        property_id="awake",
        value=False,
        validity={"start_event_id": "start", "end_event_id": "end"},
    )
    assert state(query(scene, 15), property_id="awake")["value"] is False
    assert state(query(scene, 20, "before"), property_id="awake")["status"] == "known"
    assert state(query(scene, 20), property_id="awake")["status"] == "unknown"


def test_ill_typed_custom_state_does_not_apply(scene):
    prop(scene)
    event(
        scene,
        "bad",
        effects=[
            {"subject_id": "a", "property_id": "awake", "value": 1, "persistence": "until_changed"}
        ],
    )
    result = query(scene, 20)
    assert state(result, property_id="awake")["status"] == "unknown"
    assert any(
        obj["rule_id"] == "STATE_TYPE" and obj["severity"] == "error"
        for obj in result["diagnostics"]
    )


def test_multiple_effects_ambiguity_is_per_resource(scene):
    prop(scene, persistence="until_changed")
    event(
        scene,
        "both",
        actor_id="a",
        effects=[
            {"subject_id": "a", "property_id": "awake", "value": True},
            {"subject_id": "b", "property_id": "awake", "value": False},
        ],
    )
    event(scene, "other", effects=[{"subject_id": "a", "property_id": "awake", "value": False}])
    result = query(scene, 11)
    assert state(result, property_id="awake")["status"] == "conflict"
    assert state(result, subject="b", property_id="awake")["status"] == "known"
    assert state(result, subject="b", property_id="awake")["value"] is False


def test_definite_precondition_failure_blocks_all_effects(scene):
    prop(scene, persistence="until_changed")
    record(
        scene, StateFact, subject_id="a", property_id="awake", value=False, validity={"start": 0}
    )
    event(
        scene,
        "sleepwalk",
        "move",
        actor_id="a",
        location_id="hall",
        preconditions=[{"subject_id": "a", "property_id": "awake", "value": True}],
        effects=[{"subject_id": "b", "property_id": "awake", "value": True}],
    )
    result = query(scene, 20)
    assert result["locations"]["a"] is None
    assert state(result, subject="b", property_id="awake")["status"] == "unknown"
    assert any(obj["rule_id"] == "STATE_PRECONDITION" for obj in result["diagnostics"])


def test_unknown_precondition_keeps_recorded_effect_without_back_inference(scene):
    prop(scene)
    event(
        scene,
        "walk",
        "move",
        actor_id="a",
        location_id="hall",
        preconditions=[{"subject_id": "a", "property_id": "awake", "value": True}],
    )
    result = query(scene, 20)
    assert result["locations"]["a"] == "hall"
    assert state(result, property_id="awake")["status"] == "unknown"
    assert any(obj["rule_id"] == "STATE_PRECONDITION_UNKNOWN" for obj in result["diagnostics"])


def test_unknown_order_can_prevent_false_precondition_conflict(scene):
    event(scene, "initial", "transfer", at=1, actor_id="a", target_id="b", item_id="key")
    event(scene, "return", "transfer", at=None, actor_id="b", target_id="a", item_id="key")
    event(scene, "later", "transfer", at=20, actor_id="a", target_id="c", item_id="key")
    result = query(scene, 30)
    assert not any(
        obj["rule_id"] == "STATE_PRECONDITION" and obj["object_id"] == "later"
        for obj in result["diagnostics"]
    )
    assert result["holders"]["key"] is None


def test_world_claim_and_knowledge_assertions_never_become_state(scene):
    assertion(scene, "world", predicate="location", value="hall")
    assertion(scene, "claim", predicate="location", value="garden", scope="claim", holder_id="a")
    assertion(
        scene,
        "thought",
        predicate="location",
        value="garden",
        scope="knowledge",
        holder_id="a",
        certainty="certain",
        learning_method="read",
    )
    result = query(scene, 20)
    assert result["locations"]["a"] is None
    assert sum(obj["world_fact"] for obj in result["assertions"]) == 1
    assert result["knowledge"][0]["status"] == "unknown"


def test_tell_creates_informed_path_without_proving_world_truth(scene):
    assertion(scene, scope="claim", holder_id="a")
    event(scene, "tell", "tell", actor_id="a", target_id="b", assertion_id="secret")
    assert query(scene, 9)["knowledge"][0]["status"] == "unknown"
    result = query(scene, 11)
    knowledge = result["knowledge"][0]
    assert knowledge["holder_id"] == "b" and knowledge["attitude"] == "informed"
    assert knowledge["status"] == "known" and knowledge["acquisition"] == "told"
    assert knowledge["source_character_id"] == "a"
    assert not result["assertions"][0]["world_fact"]
    assert result["evidence"]["locations"]["b"] == []


def test_observe_explicit_attitude_and_forgetting(scene):
    assertion(scene)
    event(
        scene,
        "see",
        "observe",
        at=1,
        actor_id="a",
        assertion_id="secret",
        knowledge_attitude="knows",
    )
    record(
        scene,
        KnowledgeRecord,
        id="forget",
        holder_id="a",
        assertion_id="secret",
        attitude="forgotten",
        acquisition="initial",
        validity={"start": 10},
    )
    assert query(scene, 5)["knowledge"][0]["attitude"] == "knows"
    result = query(scene, 11)["knowledge"][0]
    assert result["status"] == "forgotten" and result["attitude"] == "forgotten"


def test_conflicting_knowledge_attitudes_at_same_time_stay_uncertain(scene):
    assertion(scene)
    for identity, attitude in [("yes", "believes"), ("no", "denies")]:
        record(
            scene,
            KnowledgeRecord,
            id=identity,
            holder_id="a",
            assertion_id="secret",
            attitude=attitude,
            acquisition="initial",
            validity={"start": 1},
        )
    assert query(scene, 2)["knowledge"][0]["status"] == "conflict"


def test_knowledge_used_checks_prior_paths_not_future_or_world_assertion(scene):
    assertion(scene)
    event(scene, "uses", "move", actor_id="b", location_id="hall", knowledge_used=["secret"])
    event(scene, "tell", "tell", at=20, actor_id="a", target_id="b", assertion_id="secret")
    result = query(scene, 30)
    diagnostic = next(obj for obj in result["diagnostics"] if obj["rule_id"] == "KNOWLEDGE_PATH")
    assert diagnostic["verdict"] == "insufficient" and diagnostic["severity"] == "warning"
    assert result["locations"]["b"] == "hall"


def test_closed_knowledge_rule_blocks_event_with_missing_path(scene):
    assertion(scene)
    record(
        scene,
        WorldRule,
        id="closed",
        title="仅限已记录来源",
        rule_type="knowledge_required",
        assertion_id="secret",
        entity_ids=["b"],
        closed_world=True,
        severity="error",
    )
    event(scene, "uses", "move", actor_id="b", location_id="hall", knowledge_used=["secret"])
    result = query(scene, 11)
    assert result["locations"]["b"] is None
    assert any(
        obj["rule_id"] == "KNOWLEDGE_PATH"
        and obj["verdict"] == "conflict"
        and obj["severity"] == "error"
        for obj in result["diagnostics"]
    )


def test_prior_information_path_allows_use_without_warning(scene):
    assertion(scene)
    event(scene, "tell", "tell", at=1, actor_id="a", target_id="b", assertion_id="secret")
    event(scene, "uses", "move", actor_id="b", location_id="hall", knowledge_used=["secret"])
    result = query(scene, 11)
    assert not any(obj["rule_id"] == "KNOWLEDGE_PATH" for obj in result["diagnostics"])


def test_relation_preserves_validity_certainty_and_evidence(scene):
    record(
        scene,
        Relation,
        id="allies",
        subject_id="a",
        predicate="ally",
        object_id="b",
        certainty="certain",
        validity={"start": 1, "end": 5},
    )
    assert query(scene, 3)["relations"][0]["status"] == "known"
    result = query(scene, 6)["relations"][0]
    assert result["status"] == "expired" and result["validity"]["end"] == 5
    assert result["evidence"] == [PROOF]


@pytest.mark.parametrize(
    "kind,value,verdict",
    [
        ("require", False, "conflict"),
        ("require", True, "consistent"),
        ("forbid", True, "conflict"),
        ("forbid", False, "consistent"),
    ],
)
def test_executable_world_rules_distinguish_false_and_unknown(scene, kind, value, verdict):
    prop(scene, persistence="until_changed")
    record(
        scene, StateFact, subject_id="a", property_id="awake", value=value, validity={"start": 1}
    )
    record(
        scene,
        WorldRule,
        id="rule",
        title="规则",
        rule_type=kind,
        conditions=[{"subject_id": "a", "property_id": "awake", "value": True}],
        severity="error",
    )
    diagnostic = next(
        obj for obj in query(scene, 2)["diagnostics"] if obj["rule_id"] == "WORLD_RULE"
    )
    assert diagnostic["verdict"] == verdict


def test_missing_rule_inputs_are_insufficient_and_empty_rule_unsupported(scene):
    prop(scene)
    record(
        scene,
        WorldRule,
        id="missing",
        title="规则",
        rule_type="require",
        conditions=[{"subject_id": "a", "property_id": "awake", "value": True}],
    )
    record(scene, WorldRule, id="empty", title="规则", rule_type="forbid")
    rules = {
        obj["object_id"]: obj
        for obj in query(scene, 2)["diagnostics"]
        if obj["rule_id"] == "WORLD_RULE"
    }
    assert rules["missing"]["verdict"] == "insufficient"
    assert rules["empty"]["verdict"] == "unsupported"


def test_rule_validity_does_not_leak_into_earlier_query(scene):
    prop(scene)
    record(
        scene,
        WorldRule,
        title="future",
        rule_type="require",
        conditions=[{"subject_id": "a", "property_id": "awake", "value": True}],
        validity={"start": 10, "end": 20},
    )
    assert not any(obj["rule_id"] == "WORLD_RULE" for obj in query(scene, 5)["diagnostics"])
    assert not any(obj["rule_id"] == "WORLD_RULE" for obj in query(scene, 30)["diagnostics"])


def test_relative_event_queries_work_without_invented_minutes(scene):
    event(scene, "initial", "transfer", at=None, actor_id="a", target_id="b", item_id="key")
    event(
        scene,
        "return",
        "transfer",
        at=None,
        time_kind="relative",
        after_event_id="initial",
        actor_id="b",
        target_id="a",
        item_id="key",
    )
    result = query_event(scene, "return")
    assert result["before"]["holders"]["key"] == "b"
    assert result["after"]["holders"]["key"] == "a"
    assert result["after"]["minute"] is None
    assert query(scene, 5)["holders"]["key"] is None


def test_disputed_events_and_records_do_not_change_projection(scene):
    event(scene, "move", "move", actor_id="a", location_id="hall", status="disputed")
    prop(scene)
    record(
        scene,
        StateFact,
        subject_id="a",
        property_id="awake",
        value=True,
        validity={"start": 1},
        persistence="until_changed",
        status="disputed",
    )
    assert query(scene, 20)["locations"]["a"] is None
    assert state(query(scene, 20), property_id="awake")["status"] == "unknown"
    assert query_event(scene, "move")["after"]["locations"]["a"] is None


def test_projection_is_pure_deterministic_and_ignores_production_records(scene):
    event(scene, "move", "move", actor_id="a", location_id="hall")
    scene["records"].append({"kind": "scene", "id": "scene", "status": "accepted"})
    original = deepcopy(scene)
    assert query(scene, 20) == query(scene, 20)
    assert scene == original


def test_duration_conflict_keeps_event_effect_unknown(scene):
    event(scene, "impossible-speed", "move", actor_id="a", location_id="hall", min_duration=5)
    result = query(scene, 20)
    assert result["locations"]["a"] is None
    assert any(
        obj["rule_id"] == "TIME_DURATION" and obj["severity"] == "error"
        for obj in result["diagnostics"]
    )


@pytest.mark.parametrize("minute", [True, float("inf"), float("nan"), None, "missing"])
def test_invalid_cut_is_rejected(scene, minute):
    with pytest.raises(ValueError):
        query(scene, minute)
