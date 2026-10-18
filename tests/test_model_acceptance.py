"""Fixed release gate: 20 state questions, 20 alarms, 20 clean examples.

These are explicit authored situations, not model-generated paraphrases or
accuracy claims. Every alarm has an encoded rule and an evidential trigger.
"""

from test_complete_state import assertion, event, prop, record, state
from test_complete_state import scene as scene

from narrative.models import KnowledgeRecord, PropertyDefinition, Relation, StateFact, WorldRule
from narrative.state import diagnose, query, query_event


def alarms(result, rule=None):
    return [
        item
        for item in result["diagnostics"]
        if item["severity"] in {"warning", "error"} and (rule is None or item["rule_id"] == rule)
    ]


def requirement(
    scene, identity="awake-required", *, kind="require", subject="a", value=True, **fields
):
    return record(
        scene,
        WorldRule,
        id=identity,
        title=identity,
        rule_type=kind,
        conditions=[{"subject_id": subject, "property_id": "awake", "value": value}],
        **fields,
    )


def test_state_01_absent_location_is_unknown_not_an_initial_place(scene):
    result = query(scene, 0)
    assert state(result, property_id="location")["status"] == "unknown"
    assert result["locations"]["a"] is None


def test_state_02_completed_move_establishes_location(scene):
    event(scene, "arrive", "move", actor_id="a", location_id="hall")
    assert query(scene, 12)["locations"]["a"] == "hall"


def test_state_03_before_same_minute_does_not_include_arrival(scene):
    event(scene, "arrive", "move", actor_id="a", location_id="hall")
    assert query(scene, 10, "before")["locations"]["a"] is None
    assert query(scene, 10, "after")["locations"]["a"] == "hall"


def test_state_04_an_in_progress_journey_has_no_completed_location(scene):
    event(scene, "old", "move", at=1, actor_id="a", location_id="hall")
    event(
        scene,
        "journey",
        "move",
        at=5,
        actor_id="a",
        location_id="garden",
        time_kind="interval",
        time_end=15,
    )
    assert query(scene, 10)["locations"]["a"] is None


def test_state_05_interval_effect_applies_at_completion(scene):
    event(
        scene,
        "journey",
        "move",
        at=5,
        actor_id="a",
        location_id="garden",
        time_kind="interval",
        time_end=15,
    )
    assert query(scene, 15)["locations"]["a"] == "garden"


def test_state_06_holder_follows_a_valid_chain(scene):
    event(scene, "give", "transfer", at=1, actor_id="a", target_id="b", item_id="key")
    event(scene, "pass", "transfer", actor_id="b", target_id="c", item_id="key")
    assert query(scene, 11)["holders"]["key"] == "c"


def test_state_07_impossible_giver_does_not_overwrite_holder(scene):
    event(scene, "give", "transfer", at=1, actor_id="a", target_id="b", item_id="key")
    event(scene, "wrong", "transfer", actor_id="a", target_id="c", item_id="key")
    assert query(scene, 11)["holders"]["key"] == "b"


def test_state_08_outfit_is_an_item_reference_inside_explicit_validity(scene):
    event(scene, "wear", "dress", actor_id="a", item_id="coat", valid_until=20)
    outfit = state(query(scene, 15))
    assert outfit["status"] == "known" and outfit["value_entity_id"] == "coat"


def test_state_09_expired_outfit_does_not_imply_nakedness(scene):
    event(scene, "wear", "dress", actor_id="a", item_id="coat", valid_until=20)
    outfit = state(query(scene, 21))
    assert outfit["status"] == "unknown" and outfit["value_entity_id"] is None
    assert outfit["temporal_status"] == "expired"


def test_state_10_injury_slots_do_not_overwrite_each_other(scene):
    event(
        scene,
        "arm",
        "injure",
        at=1,
        target_id="a",
        slot="arm",
        state_value="划伤",
        persistence="until_changed",
    )
    event(
        scene,
        "leg",
        "injure",
        target_id="a",
        slot="leg",
        state_value="扭伤",
        persistence="until_changed",
    )
    result = query(scene, 11)
    assert state(result, property_id="injury:arm")["value"] == "划伤"
    assert state(result, property_id="injury:leg")["value"] == "扭伤"


def test_state_11_explicit_treatment_outcome_establishes_no_injury(scene):
    event(
        scene,
        "treat",
        "treat",
        target_id="a",
        slot="arm",
        state_value="无伤",
        persistence="until_changed",
    )
    assert state(query(scene, 11), property_id="injury:arm")["value"] == "无伤"


def test_state_12_false_boolean_is_a_known_value(scene):
    prop(scene, persistence="until_changed")
    record(
        scene, StateFact, subject_id="a", property_id="awake", value=False, validity={"start": 1}
    )
    result = state(query(scene, 11), property_id="awake")
    assert result["status"] == "known" and result["value"] is False


def test_state_13_custom_entity_property_retains_referential_identity(scene):
    record(
        scene,
        PropertyDefinition,
        id="guardian",
        name="监护人",
        value_type="entity",
        subject_types=["person"],
        reference_type="person",
        persistence="until_changed",
    )
    record(
        scene,
        StateFact,
        subject_id="b",
        property_id="guardian",
        value_entity_id="a",
        validity={"start": 1},
    )
    assert state(query(scene, 11), subject="b", property_id="guardian")["value_entity_id"] == "a"


def test_state_14_fact_can_begin_at_an_explicit_event_anchor(scene):
    prop(scene, persistence="until_changed")
    event(scene, "wake", at=5)
    record(
        scene,
        StateFact,
        subject_id="a",
        property_id="awake",
        value=True,
        validity={"start_event_id": "wake"},
    )
    assert state(query(scene, 6), property_id="awake")["value"] is True


def test_state_15_bounded_fact_keeps_its_negative_value_until_end(scene):
    prop(scene)
    record(
        scene,
        StateFact,
        subject_id="a",
        property_id="awake",
        value=False,
        validity={"start": 1, "end": 10},
    )
    assert state(query(scene, 10, "before"), property_id="awake")["value"] is False
    assert state(query(scene, 10), property_id="awake")["status"] == "unknown"


def test_state_16_simultaneous_effects_do_not_get_an_arbitrary_winner(scene):
    event(scene, "hall", "move", actor_id="a", location_id="hall")
    event(scene, "garden", "move", actor_id="a", location_id="garden")
    assert state(query(scene, 11), property_id="location")["status"] == "conflict"


def test_state_17_relative_event_cut_needs_no_numeric_timestamp(scene):
    event(scene, "give", "transfer", at=None, actor_id="a", target_id="b", item_id="key")
    event(
        scene,
        "pass",
        "transfer",
        at=None,
        time_kind="relative",
        after_event_id="give",
        actor_id="b",
        target_id="c",
        item_id="key",
    )
    result = query_event(scene, "pass")
    assert result["before"]["holders"]["key"] == "b"
    assert result["after"]["holders"]["key"] == "c"


def test_state_18_numeric_query_cannot_place_an_unanchored_relative_chain(scene):
    event(scene, "first", at=None)
    event(
        scene,
        "move",
        "move",
        at=None,
        time_kind="relative",
        after_event_id="first",
        actor_id="a",
        location_id="hall",
    )
    assert query(scene, 100)["locations"]["a"] is None


def test_state_19_told_claim_becomes_information_not_world_truth(scene):
    assertion(scene, scope="claim", holder_id="a")
    event(scene, "tell", "tell", actor_id="a", target_id="b", assertion_id="secret")
    result = query(scene, 11)
    assert result["knowledge"][0]["attitude"] == "informed"
    assert result["assertions"][0]["world_fact"] is False


def test_state_20_relationship_preserves_its_scope_and_certainty(scene):
    record(
        scene,
        Relation,
        subject_id="a",
        predicate="ally",
        object_id="b",
        certainty="certain",
        validity={"start": 1, "end": 20},
    )
    relation = query(scene, 11)["relations"][0]
    assert relation["status"] == "known" and relation["certainty"] == "certain"
    assert relation["validity"]["start"] == 1


def test_alarm_01_wrong_giver_violates_the_holder_precondition(scene):
    event(scene, "give", "transfer", at=1, actor_id="a", target_id="b", item_id="key")
    event(scene, "wrong", "transfer", actor_id="a", target_id="c", item_id="key")
    assert alarms(query(scene, 11), "STATE_PRECONDITION")


def test_alarm_02_explicit_false_condition_blocks_an_event(scene):
    prop(scene, persistence="until_changed")
    record(
        scene, StateFact, subject_id="a", property_id="awake", value=False, validity={"start": 1}
    )
    event(
        scene,
        "walk",
        "move",
        actor_id="a",
        location_id="hall",
        preconditions=[{"subject_id": "a", "property_id": "awake", "value": True}],
    )
    assert alarms(query(scene, 11), "STATE_PRECONDITION")


def test_alarm_03_world_assertion_alone_is_not_a_character_learning_path(scene):
    assertion(scene)
    event(scene, "uses", actor_id="a", knowledge_used=["secret"])
    assert alarms(query(scene, 11), "KNOWLEDGE_PATH")


def test_alarm_04_future_telling_cannot_authorize_earlier_information_use(scene):
    assertion(scene)
    event(scene, "uses", actor_id="b", knowledge_used=["secret"])
    event(scene, "later-tell", "tell", at=20, actor_id="a", target_id="b", assertion_id="secret")
    assert alarms(query(scene, 30), "KNOWLEDGE_PATH")


def test_alarm_05_same_minute_telling_has_no_proven_priority_over_use(scene):
    assertion(scene)
    event(scene, "uses", actor_id="b", knowledge_used=["secret"])
    event(scene, "same-tell", "tell", actor_id="a", target_id="b", assertion_id="secret")
    assert alarms(query(scene, 11), "KNOWLEDGE_PATH")


def test_alarm_06_forgotten_information_is_no_longer_a_usable_path(scene):
    assertion(scene)
    record(
        scene,
        KnowledgeRecord,
        holder_id="a",
        assertion_id="secret",
        attitude="forgotten",
        acquisition="initial",
        validity={"start": 1},
    )
    event(scene, "uses", actor_id="a", knowledge_used=["secret"])
    assert alarms(query(scene, 11), "KNOWLEDGE_PATH")


def test_alarm_07_denial_cannot_be_treated_as_affirmative_knowledge(scene):
    assertion(scene)
    record(
        scene,
        KnowledgeRecord,
        holder_id="a",
        assertion_id="secret",
        attitude="denies",
        acquisition="told",
        validity={"start": 1},
    )
    event(scene, "uses", actor_id="a", knowledge_used=["secret"])
    assert alarms(query(scene, 11), "KNOWLEDGE_PATH")


def test_alarm_08_high_confidence_without_acquisition_is_not_a_proven_path(scene):
    assertion(scene)
    record(
        scene,
        KnowledgeRecord,
        holder_id="a",
        assertion_id="secret",
        attitude="knows",
        acquisition="unknown",
        validity={"start": 1},
    )
    event(scene, "uses", actor_id="a", knowledge_used=["secret"])
    assert alarms(query(scene, 11), "KNOWLEDGE_PATH")


def test_alarm_09_closed_knowledge_scope_can_raise_a_hard_conflict(scene):
    assertion(scene)
    record(
        scene,
        WorldRule,
        title="封闭来源",
        rule_type="knowledge_required",
        assertion_id="secret",
        entity_ids=["a"],
        closed_world=True,
        severity="error",
    )
    event(scene, "uses", actor_id="a", knowledge_used=["secret"])
    assert any(
        item["severity"] == "error" and item["verdict"] == "conflict"
        for item in alarms(query(scene, 11), "KNOWLEDGE_PATH")
    )


def test_alarm_10_false_world_requirement_is_a_conflict(scene):
    prop(scene, persistence="until_changed")
    record(
        scene, StateFact, subject_id="a", property_id="awake", value=False, validity={"start": 1}
    )
    requirement(scene, severity="error")
    assert alarms(query(scene, 11), "WORLD_RULE")[0]["verdict"] == "conflict"


def test_alarm_11_satisfied_forbidden_condition_is_a_conflict(scene):
    prop(scene, persistence="until_changed")
    record(scene, StateFact, subject_id="a", property_id="awake", value=True, validity={"start": 1})
    requirement(scene, kind="forbid")
    assert alarms(query(scene, 11), "WORLD_RULE")[0]["verdict"] == "conflict"


def test_alarm_12_missing_world_rule_input_is_insufficient_not_false(scene):
    prop(scene)
    requirement(scene)
    assert alarms(query(scene, 11), "WORLD_RULE")[0]["verdict"] == "insufficient"


def test_alarm_13_empty_rule_is_explicitly_unsupported(scene):
    record(scene, WorldRule, title="未编码", rule_type="forbid")
    assert alarms(query(scene, 11), "WORLD_RULE")[0]["verdict"] == "unsupported"


def test_alarm_14_competing_custom_effects_raise_resource_ambiguity(scene):
    prop(scene, persistence="until_changed")
    event(scene, "yes", effects=[{"subject_id": "a", "property_id": "awake", "value": True}])
    event(scene, "no", effects=[{"subject_id": "a", "property_id": "awake", "value": False}])
    assert alarms(query(scene, 11), "STATE_TIME_UNKNOWN")


def test_alarm_15_overlapping_journeys_have_no_invented_order(scene):
    event(
        scene,
        "hall",
        "move",
        at=1,
        time_kind="interval",
        time_end=10,
        actor_id="a",
        location_id="hall",
    )
    event(
        scene,
        "garden",
        "move",
        at=5,
        time_kind="interval",
        time_end=15,
        actor_id="a",
        location_id="garden",
    )
    assert alarms(query(scene, 20), "TIME_AMBIGUOUS")


def test_alarm_16_unknown_time_write_can_conflict_with_a_numeric_write(scene):
    event(scene, "known", "move", actor_id="a", location_id="hall")
    event(scene, "unplaced", "move", at=None, actor_id="a", location_id="garden")
    assert alarms(query(scene, 20), "STATE_TIME_UNKNOWN")


def test_alarm_17_temporal_cycles_are_hard_conflicts(scene):
    event(scene, "left", at=None, time_kind="relative", after_event_id="right")
    event(scene, "right", at=None, time_kind="relative", after_event_id="left")
    assert any(item["severity"] == "error" for item in alarms(query(scene, 11), "TIME_CYCLE"))


def test_alarm_18_missing_temporal_anchor_is_not_silently_ignored(scene):
    event(scene, "relative", at=None, time_kind="relative", after_event_id="missing")
    assert alarms(query(scene, 11), "TIME_REF")


def test_alarm_19_numeric_order_must_agree_with_explicit_predecessor(scene):
    event(scene, "later", at=10)
    event(scene, "earlier", at=5, after_event_id="later")
    assert alarms(query(scene, 20), "TIME_ORDER")


def test_alarm_20_string_property_does_not_accept_an_entity_reference(scene):
    record(
        scene,
        PropertyDefinition,
        id="mood",
        name="心境",
        value_type="string",
        subject_types=["person"],
        persistence="until_changed",
    )
    event(
        scene,
        "invalid",
        effects=[{"subject_id": "a", "property_id": "mood", "value_entity_id": "b"}],
    )
    assert alarms(query(scene, 11), "STATE_TYPE")


def test_clean_01_plain_completed_move_needs_no_alarm(scene):
    event(scene, "move", "move", actor_id="a", location_id="hall")
    assert not alarms(query(scene, 11))


def test_clean_02_missing_initial_holder_does_not_falsely_reject_a_transfer(scene):
    event(scene, "give", "transfer", actor_id="a", target_id="b", item_id="key")
    assert not alarms(query(scene, 11))


def test_clean_03_explicit_persistent_outfit_has_no_false_alarm(scene):
    event(scene, "wear", "dress", actor_id="a", item_id="coat", persistence="until_changed")
    assert not alarms(query(scene, 11))


def test_clean_04_injury_with_a_text_outcome_is_supported(scene):
    event(
        scene,
        "injure",
        "injure",
        target_id="a",
        slot="arm",
        state_value="划伤",
        persistence="until_changed",
    )
    assert not alarms(query(scene, 11))


def test_clean_05_treatment_without_outcome_is_unknown_without_inventing_conflict(scene):
    event(scene, "treat", "treat", target_id="a", slot="arm")
    assert not alarms(query(scene, 11))


def test_clean_06_explicit_bounded_boolean_fact_is_consistent(scene):
    prop(scene)
    record(
        scene,
        StateFact,
        subject_id="a",
        property_id="awake",
        value=False,
        validity={"start": 1, "end": 20},
    )
    assert not alarms(query(scene, 11))


def test_clean_07_future_precondition_conflicts_do_not_leak_into_current_query(scene):
    event(scene, "initial", "transfer", at=20, actor_id="a", target_id="b", item_id="key")
    event(scene, "wrong", "transfer", at=30, actor_id="a", target_id="c", item_id="key")
    assert not alarms(query(scene, 10))
    assert any(item["rule_id"] == "STATE_PRECONDITION" for item in diagnose(scene))


def test_clean_08_a_false_claim_does_not_conflict_with_world_state(scene):
    event(scene, "actual", "move", actor_id="a", location_id="hall")
    assertion(scene, scope="claim", holder_id="b", predicate="location", value="garden")
    assert not alarms(query(scene, 11))


def test_clean_09_legacy_character_belief_does_not_change_world_state(scene):
    assertion(
        scene,
        scope="knowledge",
        holder_id="a",
        certainty="certain",
        learning_method="read",
        predicate="location",
        value="garden",
    )
    assert not alarms(query(scene, 11))


def test_clean_10_information_can_be_received_without_being_true(scene):
    assertion(scene, scope="claim", holder_id="a")
    event(scene, "tell", "tell", actor_id="a", target_id="b", assertion_id="secret")
    assert not alarms(query(scene, 11))


def test_clean_11_explicit_observation_records_an_acquisition_path(scene):
    assertion(scene)
    event(scene, "see", "observe", actor_id="a", assertion_id="secret", knowledge_attitude="knows")
    assert not alarms(query(scene, 11))


def test_clean_12_information_use_after_telling_has_a_supported_path(scene):
    assertion(scene)
    event(scene, "tell", "tell", at=1, actor_id="a", target_id="b", assertion_id="secret")
    event(scene, "uses", actor_id="b", knowledge_used=["secret"])
    assert not alarms(query(scene, 11))


def test_clean_13_forgetting_is_not_itself_a_continuity_error(scene):
    assertion(scene)
    record(
        scene,
        KnowledgeRecord,
        holder_id="a",
        assertion_id="secret",
        attitude="forgotten",
        acquisition="initial",
        validity={"start": 1},
    )
    assert not alarms(query(scene, 11))


def test_clean_14_satisfied_requirement_is_consistent(scene):
    prop(scene, persistence="until_changed")
    record(scene, StateFact, subject_id="a", property_id="awake", value=True, validity={"start": 1})
    requirement(scene)
    assert not alarms(query(scene, 11))


def test_clean_15_false_forbidden_condition_is_consistent(scene):
    prop(scene, persistence="until_changed")
    record(
        scene, StateFact, subject_id="a", property_id="awake", value=False, validity={"start": 1}
    )
    requirement(scene, kind="forbid")
    assert not alarms(query(scene, 11))


def test_clean_16_rule_does_not_apply_before_its_validity_start(scene):
    prop(scene)
    requirement(scene, validity={"start": 20})
    assert not alarms(query(scene, 11))


def test_clean_17_rule_does_not_apply_after_its_validity_end(scene):
    prop(scene)
    requirement(scene, validity={"start": 1, "end": 10})
    assert not alarms(query(scene, 11))


def test_clean_18_explicit_clear_preserves_unknown_without_implying_a_negative_value(scene):
    prop(scene)
    event(
        scene, "clear", effects=[{"subject_id": "a", "property_id": "awake", "operation": "clear"}]
    )
    assert not alarms(query(scene, 11))


def test_clean_19_simultaneous_events_on_different_resources_are_independent(scene):
    event(scene, "a", "move", actor_id="a", location_id="hall")
    event(scene, "b", "move", actor_id="b", location_id="garden")
    assert not alarms(query(scene, 11))


def test_clean_20_disputed_contradiction_remains_evidence_without_changing_state(scene):
    event(scene, "actual", "move", actor_id="a", location_id="hall")
    event(scene, "disputed", "move", actor_id="a", location_id="garden", status="disputed")
    assert not alarms(query(scene, 11))
