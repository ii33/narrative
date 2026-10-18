from copy import deepcopy
from hashlib import sha256

import pytest

from narrative.models import (
    Assertion,
    ChangeSet,
    Diagnostic,
    Entity,
    Event,
    Evidence,
    KnowledgeRecord,
    StateEffect,
    StateFact,
    TemporalScope,
)
from narrative.review import build_candidate_preview
from narrative.state import event_effects


@pytest.fixture
def snapshot():
    text = "他和我走到车上。他把橘子放在皮大衣上。他在信中说，膀子疼痛。"
    return {
        "revision": 1,
        "entities": [
            Entity(id="father", name="父亲").model_dump(),
            Entity(id="narrator", name="我").model_dump(),
            Entity(id="train", name="车上", entity_type="location").model_dump(),
            Entity(id="orange", name="橘子", entity_type="item").model_dump(),
            Entity(id="coat", name="皮大衣", entity_type="item").model_dump(),
            Entity(id="wolf", name="狼", entity_type="other").model_dump(),
        ],
        "assertions": [],
        "events": [],
        "records": [],
        "sources": [
            {
                "id": "source",
                "title": "测试原文",
                "hash": sha256(text.encode()).hexdigest(),
                "text": text,
            }
        ],
    }


def preview(snapshot, *objects):
    return build_candidate_preview(
        snapshot, ChangeSet(expected_revision=1, operations=list(objects))
    )


def codes(card):
    return {warning["code"] for warning in card["warnings"]}


def test_preview_uses_candidate_names_preserves_quotes_and_does_not_mutate(snapshot):
    source = snapshot["sources"][0]
    quote = "他把橘子放在皮大衣上。"
    start = source["text"].index(quote)
    evidence = Evidence(
        source_id="source",
        source_hash=source["hash"],
        start=start,
        end=start + len(quote),
        quote=quote,
    )
    event = Event(
        id="transfer",
        title="放下橘子",
        action="transfer",
        actor_id="father",
        target_id="narrator",
        item_id="orange",
        evidence=[evidence],
    )
    renamed = Entity.model_validate(snapshot["entities"][0]).model_copy(update={"name": "父亲新名"})
    change = ChangeSet(expected_revision=1, operations=[renamed, event])
    old_snapshot, old_change = deepcopy(snapshot), change.model_dump()
    result = build_candidate_preview(snapshot, change)
    assert snapshot == old_snapshot and change.model_dump() == old_change
    card = result["cards"][1]
    assert card["operation_id"] == "transfer"
    assert card["evidence"][0]["quote"] == quote
    assert card["evidence"][0]["exact"] is True
    assert "父亲新名" in card["summary"][0]
    assert card["state_writes"][0]["property_name"] == "持有人"
    assert card["state_writes"][0]["value_name"] == "我"
    assert "TRANSFER_CUSTODY" in codes(card)
    assert card["after"]["action"] == "transfer"  # No silent semantic correction.
    assert "语义正确" in result["coverage"]
    assert "类型：人物" in result["cards"][0]["summary"][0]


def test_move_destination_and_companion_are_explicit(snapshot):
    move = Event(
        id="move",
        title="父亲和我离开车上",
        action="move",
        actor_id="father",
        target_id="narrator",
        location_id="train",
    )
    card = preview(snapshot, move)["cards"][0]
    assert {"MOVE_DESTINATION", "MOVE_TARGET_IGNORED"} <= codes(card)
    assert [row["subject_id"] for row in card["state_writes"]] == ["father"]
    assert card["state_writes"][0]["value_entity_id"] == "train"
    move.effects = [
        StateEffect(subject_id="narrator", property_id="location", value_entity_id="train")
    ]
    card = preview(snapshot, move)["cards"][0]
    assert [row["subject_id"] for row in card["state_writes"]] == ["father", "narrator"]


def test_world_injury_preview_distinguishes_letter_claim_and_state(snapshot):
    claim = Assertion(
        id="pain-claim",
        subject_id="father",
        predicate="膀子疼痛",
        value=True,
        scope="claim",
        holder_id="father",
    )
    state = StateFact(id="pain-state", subject_id="father", property_id="injury:arm", value="疼痛")
    claim_card, state_card = preview(snapshot, claim, state)["cards"]
    assert claim_card["ownership"] == "父亲的主张"
    assert "确定性：未知" in claim_card["summary"][1]
    assert claim_card["state_writes"] == []
    assert "CLAIM_NOT_WORLD" in codes(claim_card)
    assert state_card["ownership"] == "世界状态"
    assert {"WORLD_INJURY", "STATE_UNANCHORED"} <= codes(state_card)
    assert state_card["state_writes"][0]["value_name"] == "疼痛"


def test_observe_title_does_not_invent_kill_effect(snapshot):
    card = preview(
        snapshot,
        Event(
            id="kill", title="屠户杀死了狼", action="observe", actor_id="father", target_id="wolf"
        ),
    )["cards"][0]
    assert card["state_writes"] == []
    assert "EVENT_NO_EFFECT" in codes(card)


def test_tell_warns_about_read_channel_and_renders_actual_knowledge(snapshot):
    claim = Assertion(id="claim", subject_id="father", predicate="膀子疼痛", value=True)
    tell = Event(
        id="letter",
        title="父亲写信给我",
        action="tell",
        actor_id="father",
        target_id="narrator",
        assertion_id="claim",
    )
    read = KnowledgeRecord(
        id="read",
        holder_id="narrator",
        assertion_id="claim",
        attitude="informed",
        acquisition="read",
        event_id="letter",
    )
    card = preview(snapshot, claim, tell, read)["cards"][1]
    assert {"TELL_CHANNEL", "KNOWLEDGE_CHANNEL_MISMATCH", "KNOWLEDGE_NOT_TRUTH"} <= codes(card)
    assert card["knowledge_writes"][0]["acquisition"] == "told"
    assert card["knowledge_writes"][0]["attitude"] == "informed"
    assert "narrator" in card["dependencies"] and "claim" in card["dependencies"]


def test_supported_read_encoding_does_not_claim_hearsay(snapshot):
    claim = Assertion(id="claim", subject_id="father", predicate="膀子疼痛", value=True)
    anchor = Event(id="read-anchor", title="我读到信", action="observe", actor_id="narrator")
    record = KnowledgeRecord(
        id="read",
        holder_id="narrator",
        assertion_id="claim",
        attitude="informed",
        acquisition="read",
        event_id="read-anchor",
    )
    cards = preview(snapshot, claim, anchor, record)["cards"]
    assert not any("TELL_CHANNEL" in codes(card) for card in cards)
    assert cards[1]["knowledge_writes"] == []
    assert "阅读" in cards[2]["summary"][0]
    assert "父亲 · 膀子疼痛：是" in cards[2]["summary"][0]


def test_preview_handles_missing_reference_and_bad_quote_without_writing(snapshot):
    event = Event(
        id="move",
        title="未知人物移动",
        action="move",
        actor_id="missing",
        location_id="train",
        evidence=[Evidence(source_id="source", source_hash="wrong", start=0, end=1, quote="错")],
    )
    card = preview(snapshot, event)["cards"][0]
    assert {"MISSING_REFERENCE", "EVIDENCE_MISMATCH"} <= codes(card)
    assert "未解析对象 missing" == card["state_writes"][0]["subject_name"]


def test_diagnostics_and_before_after_are_visible(snapshot):
    changed = Entity(id="father", name="新父亲")
    diagnostic = Diagnostic(
        rule_id="REF",
        severity="error",
        verdict="conflict",
        object_id="father",
        message="测试错误",
        coverage="测试范围",
    )
    result = build_candidate_preview(
        snapshot, ChangeSet(expected_revision=1, operations=[changed]), [diagnostic]
    )
    card = result["cards"][0]
    assert result["has_errors"] is True
    assert card["before"]["name"] == "父亲" and card["after"]["name"] == "新父亲"
    assert card["diagnostics"][0]["rule_id"] == "REF"


@pytest.mark.parametrize("action", ["move", "transfer", "dress", "injure", "treat", "observe"])
def test_state_write_preview_reuses_query_engine_effect_contract(snapshot, action):
    event = Event(
        id="event",
        title="动作",
        action=action,
        actor_id="father",
        target_id="narrator",
        item_id="coat",
        location_id="train",
        effects=[StateEffect(subject_id="father", property_id="injury:arm", value="疼痛")],
    )
    writes = preview(snapshot, event)["cards"][0]["state_writes"]
    actual_effects = event_effects(event.model_dump())
    assert len(writes) == len(actual_effects)
    for rendered, effect in zip(writes, actual_effects, strict=True):
        for key, value in effect.items():
            if key != "persistence":
                assert rendered[key] == value
    if action == "treat":
        assert writes[0]["value_name"] == "未知（清除）"


def test_domain_and_single_value_boundaries_remain_explicit(snapshot):
    move = Event(
        id="wolf-move", title="狼移动", action="move", actor_id="wolf", location_id="train"
    )
    outfit = StateFact(
        id="outfit",
        subject_id="father",
        property_id="outfit",
        value_entity_id="coat",
        validity=TemporalScope(start=0),
        persistence="until_changed",
    )
    cards = preview(snapshot, move, outfit)["cards"]
    assert "BUILTIN_DOMAIN_LIMIT" in codes(cards[0])
    assert {"OUTFIT_SINGLE_VALUE", "PERSISTENCE_UNTIL_CHANGED"} <= codes(cards[1])
    assert cards[0]["after"]["actor_id"] == "wolf"


def test_false_values_and_disputed_effects_are_not_reported_as_certified(snapshot):
    state = StateFact(
        id="false",
        subject_id="father",
        property_id="custom",
        value=False,
        status="disputed",
    )
    card = preview(snapshot, state)["cards"][0]
    assert card["state_writes"][0]["value_name"] == "否"
    assert "NON_ACCEPTED" in codes(card)
    assert "意图" in card["state_writes"][0]["effect_status"]
