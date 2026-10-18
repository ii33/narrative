from typing import get_args

import pytest

from narrative.extraction import FROZEN_ACTION, V10_ACTION
from narrative.operations import OperationError, Operations


def _evidence(operations, source_id, quote):
    return operations.locate(source_id, quote)[0].model_dump()


def test_cross_chapter_people_events_storyline_and_search(tmp_path):
    operations = Operations(tmp_path / "analysis.sqlite3")
    first = operations.import_text("第一章", "阿青和阿兰送别。阿青谨慎。")
    second = operations.import_text("第二章", "阿青与阿兰争执。阿兰离开。")
    farewell = _evidence(operations, first, "阿青和阿兰送别")
    dispute = _evidence(operations, second, "阿青与阿兰争执")
    objects = [
        {"kind": "entity", "id": "qing", "name": "阿青", "entity_type": "person", "evidence": [farewell]},
        {"kind": "entity", "id": "lan", "name": "阿兰", "entity_type": "person", "evidence": [farewell]},
        {"kind": "assertion", "id": "trait", "subject_id": "qing", "predicate": "性格", "value": "谨慎", "scope": "world", "evidence": [_evidence(operations, first, "阿青谨慎")]},
        {"kind": "relation", "id": "relationship", "subject_id": "qing", "object_id": "lan", "predicate": "同伴", "evidence": [farewell]},
        {"kind": "event", "id": "farewell", "title": "送别", "action": "narrative", "narrative_type": "送别", "participants": [{"entity_id": "qing", "role": "送行者"}, {"entity_id": "lan", "role": "离开者"}], "evidence": [farewell]},
        {"kind": "event", "id": "dispute", "title": "争执", "action": "narrative", "narrative_type": "争执", "participants": [{"entity_id": "qing", "role": "争执者"}, {"entity_id": "lan", "role": "争执者"}], "evidence": [dispute]},
        {"kind": "event_link", "id": "link", "source_event_id": "farewell", "target_event_id": "dispute", "link_type": "contrasts", "description": "前后关系反差", "evidence": [farewell, dispute]},
        {"kind": "storyline", "id": "line", "title": "二人关系", "line_type": "branch", "event_ids": ["farewell", "dispute"], "evidence": [farewell, dispute]},
        {"kind": "classification", "id": "tag", "target_kind": "event", "target_id": "dispute", "category": "关系转折", "evidence": [dispute]},
    ]
    revision = operations.project_info().revision
    assert not [item for item in operations.precheck_analysis_objects(objects, revision) if item["severity"] == "error"]
    candidate = operations.propose_analysis_objects(objects, revision)
    preview = operations.candidate_preview(candidate)
    cards = {card["kind"]: card for card in preview["cards"]}
    assert {"event_link", "storyline", "classification"} <= cards.keys()
    assert "未指定" not in " ".join(cards["event"]["summary"])
    assert "关系转折" in " ".join(cards["classification"]["summary"])
    assert "二人关系" in " ".join(cards["storyline"]["summary"])
    assert cards["event_link"]["state_writes"] == []
    operations.reject_candidate(candidate, operations.review_candidate(candidate).version)
    assert operations.project_info().revision == revision
    with pytest.raises(OperationError, match="人物不存在"):
        operations.character_profile("qing")
    candidate = operations.propose_analysis_objects(objects, revision)
    operations.accept_candidate(candidate, 1)

    qing = operations.character_profile("qing")
    lan = operations.character_profile("lan")
    assert [item["id"] for item in qing["traits"]] == ["trait"]
    assert [(item["counterpart_id"], item["direction"]) for item in qing["relations"]] == [("lan", "outgoing")]
    assert [(item["counterpart_id"], item["direction"]) for item in lan["relations"]] == [("qing", "incoming")]
    assert {item["event"]["id"] for item in qing["events"]} == {"farewell", "dispute"}
    detail = operations.event_profile("dispute")
    assert {item["entity"]["id"] for item in detail["participants"]} == {"qing", "lan"}
    assert detail["links"][0]["other_event_id"] == "farewell"
    assert detail["storylines"][0]["id"] == "line"
    assert detail["classifications"][0]["category"] == "关系转折"
    assert operations.state_at_time(0)["locations"]["qing"] is None

    result = operations.search_analysis("争执", source_ids=[second], entity_ids=["qing"], kinds=["event"])
    assert [item["id"] for item in result["rows"]] == ["dispute"]
    category_result = operations.search_analysis(
        source_ids=[second], entity_ids=["qing"], kinds=["event"], categories=["关系转折"]
    )
    assert [item["id"] for item in category_result["rows"]] == ["dispute"]
    assert [item["id"] for item in operations.search_analysis(
        entity_ids=["lan"], kinds=["storyline"]
    )["rows"]] == ["line"]
    result = operations.search_analysis("阿兰离开", kinds=["source"])
    assert result["rows"][0]["evidence"][0]["source_id"] == second
    table = operations.classification_table()["rows"]
    assert any(row["facet"] == "entity" and row["source_id"] == first for row in table)
    assert any(row["facet"] == "relation" and row["source_id"] == first for row in table)
    assert any(row["facet"] == "trait" and row["category"] == "谨慎" for row in table)
    assert any(row["facet"] == "event" and row["category"] == "关系转折" for row in table)
    assert any(row["facet"] == "storyline" and row["source_id"] == second for row in table)


def test_analysis_precheck_rejects_invalid_participant_without_candidate(tmp_path):
    operations = Operations(tmp_path / "invalid.sqlite3")
    source = operations.import_text("短章", "甲与乙争执。")
    objects = [{
        "kind": "event", "id": "event", "title": "争执", "action": "narrative",
        "participants": [{"entity_id": "missing", "role": "争执者"}],
        "evidence": [_evidence(operations, source, "甲与乙争执")],
    }]
    diagnostics = operations.precheck_analysis_objects(objects, operations.project_info().revision)
    assert any(item["severity"] == "error" and item["rule_id"] == "REF" for item in diagnostics)
    assert operations.candidates() == []


def test_narrative_allows_other_entity_participant_but_legacy_actions_keep_person_rule(tmp_path):
    operations = Operations(tmp_path / "animal-participant.sqlite3")
    source = operations.import_text("狼", "屠夫劈杀狼。")
    evidence = _evidence(operations, source, "屠夫劈杀狼")
    entities = [
        {"kind": "entity", "id": "butcher", "name": "屠夫", "entity_type": "person", "evidence": [evidence]},
        {"kind": "entity", "id": "wolf", "name": "狼", "entity_type": "other", "evidence": [evidence]},
    ]
    event = {
        "kind": "event", "id": "kill", "title": "屠夫劈杀狼", "action": "narrative",
        "participants": [
            {"entity_id": "butcher", "role": "行动者"},
            {"entity_id": "wolf", "role": "受击者"},
        ],
        "evidence": [evidence],
    }
    revision = operations.project_info().revision
    assert not [
        item for item in operations.precheck_analysis_objects([*entities, event], revision)
        if item["severity"] == "error"
    ]
    assert "other" in operations.analysis_object_schema(["event"])["event"]["note"]
    missing = {**event, "participants": [{"entity_id": "absent", "role": "受击者"}]}
    assert any(
        item["rule_id"] == "REF" and item["severity"] == "error"
        for item in operations.precheck_analysis_objects([*entities, missing], revision)
    )
    legacy = {**event, "action": "act", "actor_id": "butcher"}
    assert any(
        item["rule_id"] == "REF" and item["severity"] == "error"
        for item in operations.precheck_analysis_objects([*entities, legacy], revision)
    )
    legacy_actor = {**legacy, "participants": [], "actor_id": "wolf"}
    assert any(
        item["rule_id"] == "REF" and item["severity"] == "error"
        for item in operations.precheck_analysis_objects([*entities, legacy_actor], revision)
    )
    candidate = operations.propose_analysis_objects([*entities, event], revision)
    operations.accept_candidate(candidate, 1)
    assert {
        row["entity"]["id"] for row in operations.event_profile("kill")["participants"]
    } == {"butcher", "wolf"}


def test_interpretive_trait_classification_is_visible_without_world_assertion(tmp_path):
    operations = Operations(tmp_path / "trait.sqlite3")
    source = operations.import_text("短章", "甲把仅有的一颗豆送给乙。")
    evidence = _evidence(operations, source, "甲把仅有的一颗豆送给乙")
    objects = [
        {"kind": "entity", "id": "p1", "name": "甲", "entity_type": "person", "evidence": [evidence]},
        {"kind": "classification", "id": "c1", "target_kind": "entity", "target_id": "p1", "category": "人物特点/慷慨", "note": "仅依据这次赠豆判断", "evidence": [evidence]},
    ]
    candidate = operations.propose_analysis_objects(objects, operations.project_info().revision)
    operations.accept_candidate(candidate, 1)
    profile = operations.character_profile("p1")
    assert profile["traits"][0]["title"] == "人物特点/慷慨"
    assert profile["traits"][0]["origin"] == "inference"
    assert profile["assertions"] == []
    assert operations.analysis_object_schema(["classification"])["classification"]["required"] == [
        "kind", "id", "target_id", "target_kind", "category"
    ]
    with pytest.raises(ValueError, match="未知分析对象类型"):
        operations.analysis_object_schema(["unknown"])


def test_historical_extraction_actions_remain_frozen():
    assert "narrative" not in get_args(FROZEN_ACTION)
    assert "narrative" not in get_args(V10_ACTION)
