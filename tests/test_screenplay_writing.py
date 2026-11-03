"""Scene identity, atomic bounded writing, actual dependencies and task scopes."""
from __future__ import annotations

import copy

import pytest
from agent_fixtures import TEXT, draft_result

from narrative.operations import Operations
from narrative.screenplay_store import read_context
from narrative.screenplay_writing import validate_script_writing_tool_call


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "writing.nproj")
    source = ops.import_source("合成", TEXT)
    assert ops.submit_extraction(source, draft_result(ended=False))["status"] == "accepted"
    adaptation = ops.sync_adaptation()
    events = [node for node in adaptation["nodes"] if node["node_type"] == "event"]
    screenplay = ops.create_screenplay("合成剧本", reason="测试创建")["screenplay"]
    ops.import_screenplay_timeline(screenplay["id"], [node["id"] for node in adaptation["nodes"] if node["node_type"] == "big_event"],
        expected_timeline_revision=0, expected_adaptation_revision=adaptation["revision"], reason="导入独立剧本时间线")
    timeline = ops.read_screenplay_timeline(screenplay["id"])
    span = ops.create_adaptation_span(screenplay["id"], "选区", start_node_id=events[0]["id"], end_node_id=events[-1]["id"],
        expected_timeline_revision=timeline["revision"], reason="测试选区")
    segment = ops.create_script_segment(screenplay["id"], span["span"]["id"], "接人", screenplay["structure_revision"], reason="测试建段")
    return ops, screenplay["id"], segment["id"]


def table(ops, segment_id, units=None):
    context = ops.read_scene_table_context(segment_id)
    if units is None:
        units = [{"plan": {"title": "赴约", "summary": "甲等待乙。"}},
                 {"plan": {"title": "会面", "summary": "甲乙会合后离去。"}}]
    return ops.submit_scene_table(segment_id, units,
        context["expected_outline_revision"], context["span_fingerprint"], context["context_fingerprint"], reason="测试拆场")


def body(ops, unit_id, blocks=None, context=None):
    context = context or ops.read_script_unit_context(unit_id)
    return ops.submit_script_unit(unit_id, blocks if blocks is not None else [{"type": "action", "payload": {"text": "甲等待。"}}],
        context["expected_plan_revision"], context["expected_body_revision"], context["span_fingerprint"],
        context["context_fingerprint"], context["plan_fingerprint"], dependency_refs=context["dependency_refs"], reason="测试写作")


def edit_plan(ops, segment_id, unit, patch):
    segment = ops.read_script_segment(segment_id)
    return ops.apply_scene_table_changes(segment_id, [{"op": "update", "unit_id": unit["id"], "changes": {"plan": unit["plan"] | patch}}],
        segment["outline_revision"], reason="手工调整场表")


def test_adopt_edit_plan_keeps_body_and_stable_ids_navigation_not_content(project):
    ops, _screenplay_id, segment_id = project
    source = ops.graph_snapshot()
    scene_table = table(ops, segment_id)
    first = scene_table["units"][0]
    written = body(ops, first["id"])
    adopted = ops.adopt_scene_table(segment_id, scene_table["outline_revision"], reason="采用")
    assert adopted["outline_state"] == "adopted"
    renamed = edit_plan(ops, segment_id, written, {"title": "仅导航改名"})
    unit = renamed["units"][0]
    assert unit["id"] == first["id"] and unit["blocks"] == written["blocks"]
    assert unit["plan_revision"] == written["plan_revision"] and unit["basis_state"] == "current"
    assert renamed["outline_state"] == "changed"
    changed = edit_plan(ops, segment_id, unit, {"summary": "甲决定不再等待。"})["units"][0]
    assert changed["plan_revision"] == written["plan_revision"] + 1
    assert changed["body_presence"] == "present" and changed["basis_state"] == "changed"
    assert changed["blocks"] == written["blocks"]
    assert ops.graph_snapshot() == source


def test_stale_own_plan_or_body_is_atomic_and_unrelated_scene_does_not_block(project):
    ops, _sp, segment_id = project
    first, second = table(ops, segment_id)["units"]
    old = ops.read_script_unit_context(first["id"])
    # The following scene was never read as an input to the first scene.
    body(ops, second["id"])
    body(ops, first["id"], context=old)
    stale = ops.read_script_unit_context(first["id"])
    edit_plan(ops, segment_id, stale["unit"], {"summary": "新计划"})
    snapshot = read_context(ops)[0]
    with pytest.raises(ValueError, match="plan_revision"):
        body(ops, first["id"], context=stale)
    assert read_context(ops)[0] == snapshot
    fresh = ops.read_script_unit_context(first["id"])
    block = fresh["unit"]["blocks"][0]
    ops.apply_script_block_changes(first["id"], [{"op": "update", "block_id": block["id"], "block": {"type": "action", "payload": {"text": "手工修改"}}}],
        [block["id"]], fresh["expected_body_revision"], reason="手工改正文")
    snapshot = read_context(ops)[0]
    with pytest.raises(ValueError, match="body_revision"):
        body(ops, first["id"], context=fresh)
    assert read_context(ops)[0] == snapshot


def test_previous_body_actual_dependency_changes_only_dependent_scene(project):
    ops, _sp, segment_id = project
    first, second = table(ops, segment_id)["units"]
    first = body(ops, first["id"])
    second = body(ops, second["id"])
    stale = ops.read_script_unit_context(second["id"])
    block = first["blocks"][0]
    ops.apply_script_block_changes(first["id"], [{"op": "update", "block_id": block["id"], "block": {"type": "action", "payload": {"text": "前场改变结尾"}}}],
        [block["id"]], first["body_revision"], reason="修改前场结尾")
    now = ops.read_script_segment(segment_id)["units"]
    assert now[0]["basis_state"] == "current"  # Hand edits do not fake a new basis.
    assert now[1]["basis_state"] == "changed"
    before = read_context(ops)[0]
    with pytest.raises(ValueError, match="上下文"):
        body(ops, second["id"], context=stale)
    assert read_context(ops)[0] == before
    renewed = body(ops, second["id"])
    assert renewed["basis_state"] == "current"
    segment = ops.read_script_segment(segment_id)
    moved = ops.apply_scene_table_changes(segment_id, [{"op": "move", "unit_id": second["id"], "after_id": None}],
        segment["outline_revision"], reason="调整顺序")
    assert moved["units"][0]["basis_state"] == "changed"


def test_five_payloads_unbound_warning_typed_refs_and_invalid_payload_atomic(project):
    ops, _sp, segment_id = project
    unit = table(ops, segment_id)["units"][0]
    blocks = [{"type": "action", "payload": {"text": "甲停步。"}},
        {"type": "dialogue", "payload": {"speaker_label": "陌生人", "text": "等等。", "delivery": "off_screen"}},
        {"type": "screen_text", "payload": {"text": "次日", "presentation": "overlay"}},
        {"type": "sound", "payload": {"text": "风声", "category": "effect"}},
        {"type": "transition", "payload": {"kind": "cut_to_black"}}]
    written = body(ops, unit["id"], blocks)
    assert len(written["blocks"]) == 5
    assert any(row["code"] == "DIALOGUE_SPEAKER_UNBOUND" for row in written["diagnostics"])
    before = read_context(ops)[0]
    with pytest.raises(ValueError):
        body(ops, unit["id"], [{"type": "action", "payload": {"text": "非法", "speaker_label": "混用"}}])
    with pytest.raises(ValueError, match="引用"):
        body(ops, unit["id"], [{"type": "dialogue", "payload": {"speaker_id": "missing", "text": "非法"}}])
    with pytest.raises(ValueError):
        ops.apply_scene_table_changes(segment_id, [{"op": "update", "unit_id": unit["id"], "changes": {"enabled": "false"}}],
            ops.read_script_segment(segment_id)["outline_revision"], reason="拒绝字符串bool")
    assert read_context(ops)[0] == before


def test_local_block_targets_and_explicit_deletion_keep_other_body(project):
    ops, _sp, segment_id = project
    unit = table(ops, segment_id)["units"][0]
    written = body(ops, unit["id"], [{"type": "action", "payload": {"text": text}} for text in ("一", "二")])
    first, second = written["blocks"]
    before = read_context(ops)[0]
    with pytest.raises(ValueError, match="非目标"):
        ops.apply_script_block_changes(unit["id"], [{"op": "delete", "block_id": second["id"]}], [first["id"]], written["body_revision"], reason="越界")
    assert read_context(ops)[0] == before
    changed = ops.apply_script_block_changes(unit["id"], [{"op": "update", "block_id": first["id"],
        "block": {"type": "action", "payload": {"text": "只改第一块"}}}], [first["id"]], written["body_revision"], reason="局部编辑")
    assert changed["blocks"][1] == second
    segment = ops.read_script_segment(segment_id)
    before = read_context(ops)[0]
    with pytest.raises(ValueError, match="正文"):
        ops.apply_scene_table_changes(segment_id, [{"op": "delete", "unit_id": unit["id"], "body_policy": "require_empty"}], segment["outline_revision"], reason="不可隐式丢正文")
    assert read_context(ops)[0] == before
    with pytest.raises(ValueError, match="稳定ID"):
        table(ops, segment_id, [{"plan": {"summary": "偷偷重建同名场"}}])


def test_batch_freezes_targets_and_failure_retains_success(project):
    ops, sp, segment_id = project
    first, second = table(ops, segment_id)["units"]
    manifest = ops.prepare_script_writing_targets(sp)
    assert manifest["unit_ids"] == [first["id"], second["id"]]
    completed = body(ops, first["id"])
    assert ops.prepare_script_writing_targets(sp)["unit_ids"] == [second["id"]]
    stale = ops.read_script_unit_context(second["id"])
    edit_plan(ops, segment_id, stale["unit"], {"summary": "任务中改计划"})
    with pytest.raises(ValueError):
        body(ops, second["id"], context=stale)
    assert ops.read_script_unit_context(first["id"])["unit"]["blocks"] == completed["blocks"]
    segment = ops.read_script_segment(segment_id)
    ops.apply_scene_table_changes(segment_id, [{"op": "insert", "unit": {"plan": {"summary": "新增场"}}}], segment["outline_revision"], reason="任务中插场")
    reread = ops.read_script_writing_targets(manifest)
    assert reread["list_changed"] and reread["unit_ids"] == manifest["unit_ids"]
    assert len(reread["targets"]) == 2


def test_invalid_empty_source_preserves_editable_old_body_and_current_basis_only(project):
    ops, sp, segment_id = project
    unit = table(ops, segment_id)["units"][0]
    written = body(ops, unit["id"])
    refreshed = ops.mark_writing_basis_current("unit", unit["id"], written["revision"], reason="明确核对")
    assert refreshed["basis_state"] == "current"
    bases = read_context(ops)[0]["script_writing_bases"]
    assert len([basis for basis in bases.values() if basis["target_id"] == unit["id"]]) == 1
    state = ops.read_screenplay_timeline(sp)
    scene = next(node for node in state["nodes"] if node["node_type"] == "scene")
    ops.suppress_screenplay_timeline_node(sp, scene["id"], expected_timeline_revision=state["revision"], reason="剧本本地屏蔽")
    context = ops.read_script_unit_context(unit["id"])
    assert not context["generation_allowed"] and context["unit"]["basis_state"] == "empty_source"
    with pytest.raises(ValueError, match="invalid/empty"):
        body(ops, unit["id"])
    block = written["blocks"][0]
    edited = ops.apply_script_block_changes(unit["id"], [{"op": "update", "block_id": block["id"], "block": {"type": "action", "payload": {"text": "旧稿仍可编辑"}}}],
        [block["id"]], written["body_revision"], reason="保留旧稿编辑")
    assert edited["body_presence"] == "present" and edited["basis_state"] == "empty_source"


def test_captured_task_scope_allows_new_target_scene_but_rejects_other_writes(project):
    ops, _sp, segment_id = project
    task = ops.prepare_script_writing_task({"kind": "segment", "ids": [segment_id]}, "拆场并写作")
    unit = table(ops, segment_id)["units"][0]
    validate_script_writing_tool_call(task, "submit_script_unit", {"unit_id": unit["id"]}, ops=ops)
    with pytest.raises(ValueError, match="仅能"):
        validate_script_writing_tool_call(task, "edit_adaptation_node", {"changes": {}}, ops=ops)
    written = body(ops, unit["id"])
    precise = ops.prepare_script_writing_task({"kind": "blocks", "ids": [written["blocks"][0]["id"]]}, "改台词")
    with pytest.raises(ValueError, match="整场"):
        validate_script_writing_tool_call(precise, "submit_script_unit", {"unit_id": unit["id"]}, ops=ops)
    original = copy.deepcopy(precise)
    with pytest.raises(ValueError):
        validate_script_writing_tool_call(precise, "apply_script_block_changes", {"unit_id": unit["id"], "target_block_ids": ["other"]}, ops=ops)
    assert precise == original


def test_explicitly_omitted_neighbor_dependency_does_not_fake_staleness(project):
    ops, sp, segment_id = project
    first, second = table(ops, segment_id)["units"]
    first = body(ops, first["id"])
    context = ops.read_script_unit_context(second["id"], dependency_refs=[])
    assert not any(row["kind"] == "previous_unit" for row in context["dependency_refs"])
    second = body(ops, second["id"], context=context)
    block = first["blocks"][0]
    ops.apply_script_block_changes(first["id"], [{"op": "update", "block_id": block["id"],
        "block": {"type": "action", "payload": {"text": "未提供给后场的改动"}}}], [block["id"]], first["body_revision"], reason="无依赖前场修改")
    assert ops.read_script_unit_context(second["id"])["unit"]["basis_state"] == "current"
    # The original graph revision can change without changing any actual input.
    ops.edit_screenplay_timeline_node(sp, changes={"node_type": "person", "name": "无关角色"},
        expected_timeline_revision=ops.read_screenplay_timeline(sp)["revision"], reason="添加无关剧本角色")
    assert ops.read_script_unit_context(second["id"])["unit"]["basis_state"] == "current"


def test_batch_manifest_capture_refuses_mismatch_and_overwriting_completed_scene(project):
    ops, sp, segment_id = project
    first, second = table(ops, segment_id)["units"]
    manifest = ops.prepare_script_writing_targets(sp)
    captured = ops.prepare_script_writing_task({"kind": "unit", "ids": manifest["unit_ids"], "target_manifest": manifest})
    body(ops, first["id"])
    with pytest.raises(ValueError, match="已有正文"):
        validate_script_writing_tool_call(captured, "submit_script_unit", {"unit_id": first["id"]}, ops=ops)
    validate_script_writing_tool_call(captured, "submit_script_unit", {"unit_id": second["id"]}, ops=ops)
    with pytest.raises(ValueError, match="清单一致"):
        ops.prepare_script_writing_task({"kind": "unit", "ids": [second["id"]], "target_manifest": manifest})


def test_writing_uses_independent_script_entities_and_actual_local_dependencies(project):
    ops, sp, segment_id = project
    unit = table(ops, segment_id)["units"][0]
    source_before = ops.adaptation_snapshot()
    timeline = ops.read_screenplay_timeline(sp)
    ops.edit_screenplay_timeline_node(sp, changes={"node_type": "person", "name": "仅本剧本角色"},
        expected_timeline_revision=timeline["revision"], reason="本地创作角色")
    timeline = ops.read_screenplay_timeline(sp)
    person = next(node for node in timeline["nodes"] if node["name"] == "仅本剧本角色")
    ops.edit_screenplay_timeline_node(sp, changes={"node_type": "location", "name": "仅本剧本地点"},
        expected_timeline_revision=timeline["revision"], reason="本地创作地点")
    timeline = ops.read_screenplay_timeline(sp)
    location = next(node for node in timeline["nodes"] if node["name"] == "仅本剧本地点")
    unit = edit_plan(ops, segment_id, unit, {"cast_ids": [person["id"]], "location_id": location["id"],
        "continuity_requirements": [{"entity_id": person["id"], "description": "青衣"}]})["units"][0]
    context = ops.read_script_unit_context(unit["id"], dependency_refs=[{"kind": "adaptation_entity", "id": person["id"]}])
    assert context["timeline_id"] == timeline["timeline_id"]
    assert {row["id"] for row in context["reference_context"]["plan_entities"]} == {person["id"], location["id"]}
    written = body(ops, unit["id"], [{"type": "dialogue", "payload": {"speaker_id": person["id"], "text": "这是本地对白。"}},
        {"type": "sound", "payload": {"entity_id": location["id"], "text": "本地环境声"}}], context=context)
    assert {row["entity_id"] for row in written["entity_usages"]} == {person["id"], location["id"]}
    assert ops.adaptation_snapshot() == source_before
    stale = ops.read_script_unit_context(unit["id"])
    copied_actor = next(node for node in source_before["nodes"] if node["node_type"] == "person")
    ops.edit_adaptation_node(copied_actor["id"], {"name": "源改编角色已改"})
    assert ops.read_script_unit_context(unit["id"])["span_fingerprint"] == stale["span_fingerprint"]
    assert ops.read_script_unit_context(unit["id"])["unit"]["basis_state"] == "current"
    body(ops, unit["id"], context=stale)
    current = ops.read_script_unit_context(unit["id"])
    timeline = ops.read_screenplay_timeline(sp)
    ops.edit_screenplay_timeline_node(sp, person["id"], {"name": "本地角色已改"},
        expected_timeline_revision=timeline["revision"], reason="修改实际依赖本地实体")
    assert ops.read_script_unit_context(unit["id"])["unit"]["basis_state"] == "changed"
    before = read_context(ops)[0]
    with pytest.raises(ValueError, match="上下文"):
        body(ops, unit["id"], context=current)
    assert read_context(ops)[0] == before


def test_same_source_ids_are_scoped_and_local_only_entities_cannot_cross_scripts(project):
    ops, first_sp, segment_id = project
    first_unit = table(ops, segment_id)["units"][0]
    timeline = ops.read_screenplay_timeline(first_sp)
    ops.edit_screenplay_timeline_node(first_sp, changes={"node_type": "person", "name": "第一剧本独有"},
        expected_timeline_revision=timeline["revision"], reason="本地角色")
    first_timeline = ops.read_screenplay_timeline(first_sp)
    exclusive = next(node for node in first_timeline["nodes"] if node["name"] == "第一剧本独有")
    source = ops.adaptation_snapshot()
    second = ops.create_screenplay("第二剧本", reason="另一独立剧本")["screenplay"]
    ops.import_screenplay_timeline(second["id"], [node["id"] for node in source["nodes"] if node["node_type"] == "big_event"],
        expected_timeline_revision=0, expected_adaptation_revision=source["revision"], reason="另一份独立导入")
    second_timeline = ops.read_screenplay_timeline(second["id"])
    assert second_timeline["timeline_id"] != first_timeline["timeline_id"]
    assert {node["id"] for node in second_timeline["nodes"]} & {node["id"] for node in first_timeline["nodes"]}
    events = [node for node in second_timeline["nodes"] if node["node_type"] == "event"]
    span = ops.create_adaptation_span(second["id"], "第二剧本范围", start_node_id=events[0]["id"], end_node_id=events[-1]["id"],
        expected_timeline_revision=second_timeline["revision"], reason="第二剧本选区")
    segment = ops.create_script_segment(second["id"], span["span"]["id"], "第二段", second["structure_revision"], reason="第二剧本建段")
    unit = table(ops, segment["id"])["units"][0]
    captured = ops.prepare_script_writing_task({"kind": "segment", "ids": [segment_id], "screenplay_id": first_sp})
    with pytest.raises(ValueError, match="非目标"):
        validate_script_writing_tool_call(captured, "submit_script_unit", {"unit_id": unit["id"]}, ops=ops)
    with pytest.raises(ValueError, match="指定剧本"):
        ops.prepare_script_writing_task({"kind": "unit", "ids": [first_unit["id"]], "screenplay_id": second["id"]})
    before = read_context(ops)[0]
    with pytest.raises(ValueError, match="引用"):
        edit_plan(ops, segment["id"], unit, {"cast_ids": [exclusive["id"]]})
    with pytest.raises(ValueError, match="引用"):
        body(ops, unit["id"], [{"type": "dialogue", "payload": {"speaker_id": exclusive["id"], "text": "不可跨剧本"}}])
    with pytest.raises(ValueError, match="引用"):
        ops.read_script_unit_context(unit["id"], dependency_refs=[{"kind": "adaptation_entity", "id": exclusive["id"]}])
    assert read_context(ops)[0] == before
    own = edit_plan(ops, segment_id, first_unit, {"cast_ids": [exclusive["id"]]})["units"][0]
    assert body(ops, own["id"], [{"type": "dialogue", "payload": {"speaker_id": exclusive["id"], "text": "本剧本合法"}}])["basis_state"] == "current"
