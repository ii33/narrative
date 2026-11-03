"""Writing input is the effective timeline projection, never raw source prose."""
from __future__ import annotations

import json

import pytest

from narrative.operations import Operations

TEXT = "甲走入城门，纸上印着废稿哨兵。乙在密室点燃旧地图。甲继续等待。"
FIRST, HIDDEN, LAST = "甲走入城门，纸上印着废稿哨兵。", "乙在密室点燃旧地图。", "甲继续等待。"


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "input-boundary.nproj")
    sid = ops.import_source("投影边界合成", TEXT)
    draft = {"nodes": [
        {"type": "person", "name": "甲", "quotes": [FIRST]},
        {"type": "person", "name": "乙", "quotes": [HIDDEN]},
        {"type": "location", "name": "城门", "quotes": ["城门"]},
        {"type": "location", "name": "密室", "quotes": ["密室"]},
        {"type": "big_event", "name": "等候", "quotes": [TEXT]},
        {"type": "scene", "name": "相隔两地的等候", "big_event": "等候", "quotes": [TEXT]},
        {"type": "event", "name": "入城", "scene": "相隔两地的等候", "participants": ["甲"], "quotes": [FIRST]},
        {"type": "event", "name": "焚图", "scene": "相隔两地的等候", "participants": ["乙"], "quotes": [HIDDEN]},
        {"type": "event", "name": "继续等候", "scene": "相隔两地的等候", "participants": ["甲"], "quotes": [LAST]}],
        "properties": [
            {"node": "入城", "property": "地点", "value": "城门", "value_type": "node", "scope": "world", "at_event": "入城", "quotes": [FIRST]},
            {"node": "焚图", "property": "地点", "value": "密室", "value_type": "node", "scope": "world", "at_event": "焚图", "quotes": [HIDDEN]},
            {"node": "甲", "property": "身份", "value": "来客", "value_type": "string", "scope": "world", "at_event": "入城", "quotes": [FIRST]}]}
    result = ops.submit_extraction(sid, draft)
    assert result["status"] == "accepted", result
    source = ops.sync_adaptation()
    ids = {node["name"]: node["id"] for node in source["nodes"]}
    script = ops.create_screenplay("输入边界", reason="隔离投影测试")["screenplay"]
    local = ops.import_screenplay_timeline(script["id"], [ids["等候"]], 0, source["revision"], "独立导入")
    ops.suppress_screenplay_timeline_node(script["id"], ids["焚图"], expected_timeline_revision=local["revision"], reason="屏蔽旧剧情")
    local = ops.read_screenplay_timeline(script["id"])
    span = ops.create_adaptation_span(script["id"], "只写有效入城等候", start_node_id=ids["入城"], end_node_id=ids["继续等候"],
        expected_timeline_revision=local["revision"], reason="选有效范围")["span"]
    segment = ops.create_script_segment(script["id"], span["id"], "等候段", script["structure_revision"], "建测试段")
    return ops, segment["id"], ids


def _keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


def test_generation_input_excludes_original_quotes_and_suppressed_plot(context):
    ops, segment_id, ids = context
    projected = ops.read_scene_table_context(segment_id)["span"]["input"]
    text = json.dumps(projected, ensure_ascii=False)
    assert "废稿哨兵" not in text and "旧地图" not in text
    assert "evidence" not in set(_keys(projected)) and "evidence_spans" not in set(_keys(projected))
    assert not {"source_valid_from", "source_valid_to", "disclosure", "identity_note", "source_marks"} & set(_keys(projected))
    assert {node["id"] for node in projected["nodes"]} == {ids["入城"], ids["继续等候"]}
    assert all(node["node_type"] in {"event", "action"} for node in projected["nodes"])
    assert all(node["node_type"] not in {"scene", "big_event", "storyline"} for node in projected["entities"])
    assert all("events" not in node for node in projected["entities"])


def test_event_grounding_never_inherits_another_events_location(context):
    ops, segment_id, ids = context
    projected = ops.read_scene_table_context(segment_id)["span"]["input"]
    grounding = {row["event_id"]: row for row in projected["event_grounding"]}
    assert projected["source_event_ids"] == [ids["入城"], ids["继续等候"]]
    assert ids["焚图"] not in grounding
    assert grounding[ids["入城"]]["location_ids"] == [ids["城门"]]
    assert grounding[ids["入城"]]["location_status"] == "known"
    assert grounding[ids["继续等候"]]["location_ids"] == []
    assert grounding[ids["继续等候"]]["location_status"] == "unrecorded"
    assert grounding[ids["继续等候"]]["participant_ids"] == [ids["甲"]]
