"""Scene standards guide the model; program checks only structural references."""
from __future__ import annotations

import json

import pytest
from test_screenplay_input_boundaries import HIDDEN
from test_screenplay_input_boundaries import context as context


def submit(ops, segment_id, plans):
    current = ops.read_scene_table_context(segment_id)
    return ops.submit_scene_table(segment_id, [{"plan": plan} for plan in plans],
        current["expected_outline_revision"], current["span_fingerprint"], current["context_fingerprint"], reason="隔离场级拆场测试")


def snapshot(ops):
    # Include counters, source rows and audits, rather than checking only the
    # target object and accidentally overlooking a partial rejected commit.
    with ops._store.connect() as db:
        return tuple(db.iterdump())


def test_one_continuous_scene_binds_all_effective_events_internally(context):
    ops, segment_id, ids = context
    result = submit(ops, segment_id, [{"title": "入城等候", "summary": "甲在城门等候。", "location_id": ids["城门"], "time_label": "日"}])
    assert len(result["units"]) == 1
    plan = result["units"][0]["plan"]
    assert plan["source_event_ids"] == [ids["入城"], ids["继续等候"]]
    assert "boundary_from_previous" not in plan


@pytest.mark.parametrize("scene_count", [2, 3])
def test_model_can_submit_same_location_continuous_scenes_without_explicit_sources(context, scene_count):
    ops, segment_id, ids = context
    current = ops.read_scene_table_context(segment_id)
    assert current["scene_table_policy"]["rules"]
    assert "suggested_scene_groups" not in current["scene_table_policy"]
    result = submit(ops, segment_id, [{"summary": f"甲等候的第{index + 1}个场级表达。",
        "location_id": ids["城门"], "time_label": "日" if index == 0 else "紧接上场"}
        for index in range(scene_count)])
    assert len(result["units"]) == scene_count
    assert all(unit["plan"]["source_event_ids"] == [] for unit in result["units"])
    assert all("boundary_from_previous" not in unit["plan"] for unit in result["units"])


def test_optional_source_references_do_not_require_full_or_nonoverlapping_partition(context):
    ops, segment_id, ids = context
    result = submit(ops, segment_id, [
        {"summary": "由模型判断的第一场。", "source_event_ids": [ids["入城"]], "time_label": "日"},
        {"summary": "同一事件的另一场表达。", "source_event_ids": [ids["入城"]], "time_label": "紧接上场"}])
    assert [unit["plan"]["source_event_ids"] for unit in result["units"]] == [[ids["入城"]], [ids["入城"]]]


def test_scene_level_production_notes_are_saved_without_keyword_rejection(context):
    ops, segment_id, _ids = context
    notes = "保持环境风声连续；特写、慢镜与体积烟光效只作讨论词，不在场表规定镜头。"
    result = submit(ops, segment_id, [{"summary": "甲在城门等候。", "production_notes": notes}])
    assert result["units"][0]["plan"]["production_notes"] == notes


@pytest.mark.parametrize("which", ["hidden", "outside"])
def test_generated_sources_cannot_reference_hidden_or_actual_outside_events(context, which):
    ops, segment_id, ids = context
    if which == "hidden":
        invalid = ids["焚图"]
    else:
        current = ops.read_scene_table_context(segment_id)
        span = current["span"]["span"]
        screenplay_id = ops.read_script_segment(segment_id)["screenplay_id"]
        local = ops.read_screenplay_timeline(screenplay_id)
        ops.update_adaptation_span(span["id"], {"start_node_id": ids["继续等候"], "end_node_id": ids["继续等候"]},
            expected_revision=span["revision"], expected_timeline_revision=local["revision"], reason="缩窄测试绑定范围")
        invalid = ids["入城"]
    before = snapshot(ops)
    with pytest.raises(ValueError, match="屏蔽|范围外"):
        submit(ops, segment_id, [{"summary": "不能从隐藏或范围外事件补剧情。", "source_event_ids": [invalid]}])
    assert snapshot(ops) == before


def test_recorded_location_changes_remain_context_for_model_not_automatic_partition(context):
    ops, segment_id, ids = context
    screenplay_id = ops.read_script_segment(segment_id)["screenplay_id"]
    local = ops.read_screenplay_timeline(screenplay_id)
    ops.suppress_screenplay_timeline_node(screenplay_id, ids["焚图"], suppressed=False,
        expected_timeline_revision=local["revision"], reason="恢复第二地点的明确事件")
    current = ops.read_scene_table_context(segment_id)
    assert "suggested_scene_groups" not in current["scene_table_policy"]
    grounding = {row["event_id"]: row for row in current["span"]["input"]["event_grounding"]}
    assert grounding[ids["入城"]]["location_ids"] == [ids["城门"]]
    assert grounding[ids["焚图"]]["location_ids"] == [ids["密室"]]
    result = submit(ops, segment_id, [
        {"summary": "甲在城门等候。", "location_id": ids["城门"], "time_label": "日"},
        {"summary": "乙在密室焚图。", "location_id": ids["密室"], "time_label": "紧接上场"}])
    assert [unit["plan"]["source_event_ids"] for unit in result["units"]] == [[], []]


def test_old_prose_is_not_generation_input_and_hidden_source_does_not_erase_body(context):
    ops, segment_id, ids = context
    screenplay_id = ops.read_script_segment(segment_id)["screenplay_id"]
    local = ops.read_screenplay_timeline(screenplay_id)
    ops.suppress_screenplay_timeline_node(screenplay_id, ids["焚图"], suppressed=False,
        expected_timeline_revision=local["revision"], reason="准备已有旧稿")
    segment = ops.read_script_segment(segment_id)
    saved = ops.apply_scene_table_changes(segment_id, [{"op": "insert", "unit": {"plan": {
        "title": "旧稿不作事实", "summary": HIDDEN, "outcome": "旧地图已销毁", "production_notes": "特写和体积烟方案",
        "source_event_ids": [ids["焚图"]]}}}], segment["outline_revision"], reason="显式手工旧稿fixture")
    unit = saved["units"][0]
    written = ops.apply_script_block_changes(unit["id"], [{"op": "insert", "block": {"type": "action", "payload": {"text": "旧稿正文必须保留。"}}}],
        [], unit["body_revision"], reason="显式手工旧正文fixture")
    local = ops.read_screenplay_timeline(screenplay_id)
    ops.suppress_screenplay_timeline_node(screenplay_id, ids["焚图"], expected_timeline_revision=local["revision"], reason="屏蔽旧稿来源")
    current = ops.read_scene_table_context(segment_id)
    encoded = json.dumps(current, ensure_ascii=False)
    assert HIDDEN not in encoded and "旧地图已销毁" not in encoded and "特写和体积烟方案" not in encoded
    assert "plan" not in current["segment"]["units"][0]
    before = snapshot(ops)
    explicit = ops.read_scene_table_context(segment_id, dependency_refs=[
        {"kind": "unit_body", "id": unit["id"]}, {"kind": "unit_plan", "id": unit["id"]}])
    assert "旧稿正文必须保留。" in json.dumps(explicit, ensure_ascii=False)
    assert HIDDEN in json.dumps(explicit, ensure_ascii=False)
    assert "参考" in explicit["reference_policy"]["guidance"]
    assert explicit["span"]["effective_node_ids"] == current["span"]["effective_node_ids"]
    assert snapshot(ops) == before
    after = ops.read_script_segment(segment_id)
    assert after["units"][0]["blocks"] == written["blocks"]
    assert any(issue["code"] == "SCENE_SOURCE_UNAVAILABLE" for issue in after["scene_partition_diagnostics"])
    check = ops.check_screenplay(screenplay_id)
    assert any(issue["code"] == "SCENE_SOURCE_UNAVAILABLE" for issue in check["issues"])
