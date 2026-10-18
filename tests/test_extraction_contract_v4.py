"""Offline contract checks using small excerpts from the independent retest.

These examples show safe, schema-valid representations. They do not claim that
the validator can judge literary meaning or that a model follows the prompt.
"""

import hashlib
import json

import pytest

from narrative import extraction
from narrative.operations import OperationError, Operations
from narrative.service import Service
from narrative.store import Store

SNAPSHOT = {"prompt_version": "evidence-extraction/6"}
TEXT = {
    "lang": ("時狼革價昂，直十餘金。遙望樹上懸巨物，似人縊死狀。方縛以帶。"),
    "rulin": "屠戶橫披了衣服，腆著肚子去了。",
    "beiying": "將橘子一股腦兒放在我的皮大衣上。",
}


def check(source: str, payload: dict) -> dict:
    original = json.dumps(payload, ensure_ascii=False)
    return extraction.preflight_result(SNAPSHOT, original, TEXT[source])


def entity(ref: str, name: str, kind: str = "person") -> dict:
    return {"ref": ref, "name": name, "entity_type": kind}


def assertion(subject: str, predicate: str, quote: str, **fields) -> dict:
    return {
        "ref": "fact",
        "subject_ref": subject,
        "predicate": predicate,
        "value": True,
        "quote": quote,
        **fields,
    }


def test_new_run_uses_v11_with_frozen_v3_through_v10_available(tmp_path):
    service = Service(Store(tmp_path / "v4.sqlite3"))
    source_id = service.import_text("短文", "甲走了。")
    exported = extraction.export_run(service, extraction.create_run(service, source_id))
    assert exported["prompt_version"] == "evidence-extraction/11"
    assert exported["instruction"] == extraction.V11_INSTRUCTION
    assert exported["result_schema"] == extraction.V10ExtractionResult.model_json_schema()
    assert extraction.V2_INSTRUCTION not in exported["instruction"]
    assert extraction._contract({"prompt_version": "evidence-extraction/3"}) == (
        extraction.ExtractionResult,
        extraction.INSTRUCTION,
    )
    assert extraction._contract({"prompt_version": "evidence-extraction/4"}) == (
        extraction.ExtractionResult,
        extraction.V4_INSTRUCTION,
    )
    assert extraction._contract({"prompt_version": "evidence-extraction/5"}) == (
        extraction.ExtractionResult,
        extraction.V5_INSTRUCTION,
    )
    assert extraction._contract({"prompt_version": "evidence-extraction/6"}) == (
        extraction.ExtractionResult,
        extraction.V6_INSTRUCTION,
    )
    assert extraction._contract({"prompt_version": "evidence-extraction/7"}) == (
        extraction.V7ExtractionResult,
        extraction.V7_INSTRUCTION,
    )
    assert extraction._contract({"prompt_version": "evidence-extraction/10"}) == (
        extraction.V10ExtractionResult,
        extraction.V10_INSTRUCTION,
    )
    assert hashlib.sha256(extraction.V4_INSTRUCTION.encode("utf-8")).hexdigest() == (
        "d601ab7f8bde74362fcfdd0b9ebf3ee66a10a8de6d68998f820523c9f3a569d9"
    )
    assert hashlib.sha256(extraction.V5_INSTRUCTION.encode("utf-8")).hexdigest() == (
        "953f27aaddb61ff21eaba36c3e132340fdc492e1e8195e7f9a520c1ff5d691e1"
    )
    assert len(exported["instruction"]) < len(extraction.INSTRUCTION)


@pytest.mark.parametrize(
    ("source", "payload"),
    [
        (
            "lang",
            {
                "entities": [entity("pelt", "狼革", "item"), entity("meat", "肉", "item")],
                "assertions": [
                    assertion("pelt", "价值十余金", "時狼革價昂，直十餘金", value="十餘金")
                ],
            },
        ),
        (
            "lang",
            {
                "entities": [entity("butcher", "屠人"), entity("shape", "巨物", "other")],
                "assertions": [
                    assertion(
                        "shape",
                        "远望似人缢死状",
                        "遙望樹上懸巨物，似人縊死狀",
                        scope="knowledge",
                        holder_ref="butcher",
                        certainty="suspects",
                    )
                ],
                "events": [
                    {
                        "ref": "look",
                        "title": "屠人远望巨物",
                        "action": "observe",
                        "actor_ref": "butcher",
                        "quote": "遙望樹上懸巨物，似人縊死狀",
                    }
                ],
            },
        ),
        (
            "lang",
            {
                "entities": [entity("butcher", "屠人"), entity("wolf", "狼", "other")],
                "assertions": [assertion("wolf", "被屠人以带缚住", "方縛以帶")],
            },
        ),
        (
            "rulin",
            {
                "entities": [entity("butcher", "胡屠戶"), entity("home", "范進家", "location")],
                "assertions": [assertion("butcher", "离开", "腆著肚子去了")],
            },
        ),
        (
            "beiying",
            {
                "entities": [entity("orange", "橘子", "item"), entity("coat", "皮大衣", "item")],
                "assertions": [
                    assertion(
                        "orange",
                        "放在皮大衣上",
                        "將橘子一股腦兒放在我的皮大衣上",
                        value=None,
                        value_entity_ref="coat",
                    )
                ],
            },
        ),
    ],
)
def test_report_counterexamples_have_precise_safe_representations(source, payload):
    result = check(source, payload)
    assert result["valid"] and result["importable"], result["diagnostics"]
    assert not payload.get("states") and not any(
        event["action"] in {"move", "transfer"} for event in payload.get("events", [])
    )


def test_invalid_structural_guesses_are_rejected_without_mutating_payload():
    text = "甲说狼死了。甲离开家。"
    payload = {
        "entities": [
            entity("a", "甲"),
            entity("wolf", "狼", "other"),
            entity("home", "家", "location"),
        ],
        "assertions": [assertion("wolf", "死亡", "甲说狼死了。", scope="claim", holder_ref=None)],
        "events": [
            {
                "ref": "depart",
                "title": "甲离开家",
                "action": "move",
                "actor_ref": "a",
                "location_ref": None,
                "time_kind": "point",
                "time_start": None,
                "quote": "甲离开家。",
            },
            {
                "ref": "hand",
                "title": "甲给狼物品",
                "action": "transfer",
                "actor_ref": "a",
                "target_ref": "wolf",
                "item_ref": "home",
                "quote": "甲离开家。",
            },
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False)
    result = extraction.preflight_result(SNAPSHOT, raw, text)
    assert not result["valid"]
    assert {
        "holder_required",
        "reference_required",
        "time_point",
        "reference_type",
    } <= {diagnostic["code"] for diagnostic in result["diagnostics"]}
    assert raw == json.dumps(payload, ensure_ascii=False)


def test_v5_time_combinations_and_unknown_validity_are_consistent():
    text = "甲进门。乙随后进门。"
    entities = [entity("a", "甲"), entity("b", "乙"), entity("door", "门", "location")]
    events = [
        {
            "ref": "first",
            "title": "甲进门",
            "action": "move",
            "actor_ref": "a",
            "location_ref": "door",
            "quote": "甲进门。",
        },
        {
            "ref": "second",
            "title": "乙随后进门",
            "action": "move",
            "actor_ref": "b",
            "location_ref": "door",
            "time_kind": "relative",
            "after_event_ref": "first",
            "quote": "乙随后进门。",
        },
    ]
    payload = {
        "entities": entities,
        "assertions": [assertion("door", "被甲进入", "甲进门。", validity={})],
        "events": events,
    }
    result = extraction.preflight_result(SNAPSHOT, json.dumps(payload), text)
    assert result["valid"], result["diagnostics"]
    events[0]["time_kind"] = "point"
    result = extraction.preflight_result(SNAPSHOT, json.dumps(payload), text)
    assert any(d["code"] == "time_point" for d in result["diagnostics"])


def test_v5_prompt_covers_all_five_semantic_boundaries_without_changing_validator():
    prompt = extraction.V5_INSTRUCTION
    for detail in ("狼革", "遙望樹上懸巨物", "方縛以帶", "腆著肚子去了", "皮大衣上"):
        assert detail in prompt
    # A coherent but wrong attribution still needs model retest and human review.
    wrong = {
        "entities": [entity("meat", "肉", "item")],
        "assertions": [assertion("meat", "价值十余金", "時狼革價昂，直十餘金")],
    }
    assert check("lang", wrong)["valid"]


def test_v5_restores_executable_field_rules_missing_from_v4():
    prompt = extraction.V5_INSTRUCTION
    for rule in (
        "value 与 value_entity_ref 不得同时非 null",
        "kind=property 且 status=accepted",
        "location、held_by、outfit、injury:",
        "assertion_ref 和 knowledge_used",
        "after_event_ref、event_ref",
        "unknown/certain/suspects/denies",
    ):
        assert rule in prompt
    assert prompt.startswith(extraction.V4_INSTRUCTION)
    assert len(prompt) < len(extraction.INSTRUCTION)


def test_v6_restores_explicit_bounded_end_rule_without_inventing_an_end():
    assert extraction.V6_INSTRUCTION.startswith(extraction.V5_INSTRUCTION)
    assert "persistence=bounded 必须有原文支持的明确有效终点" in extraction.V6_INSTRUCTION
    assert "终点不明时不要选 bounded，也不得猜造终点" in extraction.V6_INSTRUCTION
    payload = {
        "entities": [entity("a", "甲")],
        "states": [
            {
                "ref": "s",
                "subject_ref": "a",
                "property_id": "injury:左臂",
                "value": "受伤",
                "persistence": "bounded",
                "quote": "甲左臂受伤。",
            }
        ],
    }
    original = json.dumps(payload, ensure_ascii=False)
    result = extraction.preflight_result(SNAPSHOT, original, "甲左臂受伤。")
    assert any(d["code"] == "state_end_required" for d in result["diagnostics"])
    assert not result["importable"]
    assert json.dumps(payload, ensure_ascii=False) == original


@pytest.mark.parametrize(
    ("payload", "text", "expected"),
    [
        (
            {
                "entities": [entity("a", "甲"), entity("door", "门", "location")],
                "assertions": [assertion("a", "看门", "甲看门。", value_entity_ref="door")],
                "events": [
                    {
                        "ref": "look",
                        "title": "甲看门",
                        "action": "observe",
                        "actor_ref": "a",
                        "assertion_ref": "door",
                        "quote": "甲看门。",
                    }
                ],
                "states": [
                    {
                        "ref": "state",
                        "subject_ref": "a",
                        "property_id": "alive",
                        "quote": "甲看门。",
                    }
                ],
            },
            "甲看门。",
            {"value_exclusive", "property_unapproved", "reference_unknown"},
        ),
        (
            {
                "entities": [entity("a", "甲"), entity("door", "门", "location")],
                "events": [
                    {
                        "ref": "first",
                        "title": "甲进门",
                        "action": "move",
                        "actor_ref": "a",
                        "location_ref": "door",
                        "quote": "甲进门。",
                    },
                    {
                        "ref": "second",
                        "title": "甲又进门",
                        "action": "move",
                        "actor_ref": "a",
                        "location_ref": "door",
                        "after_event_ref": "first",
                        "time_kind": "unknown",
                        "quote": "甲又进门。",
                    },
                ],
                "states": [
                    {
                        "ref": "state",
                        "subject_ref": "a",
                        "property_id": "intoxication_state",
                        "quote": "甲进门。",
                    }
                ],
            },
            "甲进门。甲又进门。",
            {"property_unapproved", "time_relative_required"},
        ),
        (
            {
                "entities": [entity("room", "屋", "location")],
                "states": [
                    {
                        "ref": "state",
                        "subject_ref": "room",
                        "property_id": "lighting",
                        "quote": "屋漏。",
                    }
                ],
            },
            "屋漏。",
            {"property_unapproved"},
        ),
    ],
)
def test_v4_failure_shapes_remain_rejected_under_v5(payload, text, expected):
    result = extraction.preflight_result(SNAPSHOT, json.dumps(payload), text)
    assert not result["importable"]
    assert expected <= {diagnostic["code"] for diagnostic in result["diagnostics"]}


def test_saved_truncated_response_remains_atomic_and_keeps_usage(tmp_path):
    app = Operations(tmp_path / "truncated.sqlite3")
    source_id = app.import_text("截断", "甲看门。")
    run_id = app.create_run(source_id)
    chunk_id = app.chunks(run_id)[0]["id"]
    raw = '{"entities": [{"ref": "a", "name": "甲"}], "assertions": ['
    metadata = {
        "finish_reason": "max-tokens",
        "usage": {"prompt_tokens": 5957, "completion_tokens": 10684, "total_tokens": 16641},
    }
    with pytest.raises(OperationError):
        app.ingest_result(chunk_id, raw, metadata=metadata)
    saved = app.chunks(run_id)[0]
    assert saved["raw"] == raw and saved["status"] == "failed"
    assert app.token_usage(run_id)["total_tokens"] == 16641
    assert not app.extraction_review(chunk_id)["importable"]
    with pytest.raises(OperationError, match="max-tokens"):
        app.ingest_result(chunk_id, "{}")
    assert app.chunks(run_id)[0]["raw"] == raw
    assert not app.candidates() and not app.entities() and not app.assertions() and not app.events()
