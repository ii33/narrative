"""Explicit per-screenplay copies never follow or mutate the adaptation source."""
from __future__ import annotations

import copy

import pytest
from agent_fixtures import project as project
from test_adaptation_import import setup
from test_screenplay_core import QUOTES, seed

from narrative.graph_operations import GraphOperations
from narrative.screenplay_store import read_context


def create(project, title="独立剧本"):
    return project.create_screenplay(title, reason="创建独立剧本")["screenplay"]["id"]


def import_points(project, screenplay_id, ids):
    return project.import_screenplay_timeline(screenplay_id, ids,
        project.read_screenplay_timeline(screenplay_id)["revision"], project.adaptation_snapshot()["revision"], "显式选择导入")


def by_id(snapshot, identifier):
    return next(n for n in snapshot["nodes"] if n["id"] == identifier)


def test_new_screenplays_start_empty_and_read_candidates_never_import_or_write(project):
    ids = seed(project)
    source = project.adaptation_snapshot()
    one, two = create(project), create(project, "另一个方案")
    first, second = project.read_screenplay_timeline(one), project.read_screenplay_timeline(two)
    assert first["nodes"] == second["nodes"] == []
    assert first["properties"] == [] and first["revision"] == 0
    assert first["timeline_id"] != second["timeline_id"]
    assert first["timeline_id"] != first["source_timeline_id"]
    before = read_context(project)
    candidates = project.import_screenplay_timeline_candidates(one)
    assert [r["sort_id"] for r in candidates] == sorted(r["sort_id"] for r in candidates)
    assert {r["source_revision"] for r in candidates} == {source["revision"]}
    assert ids["甲敲门"] in {r["id"] for r in candidates}
    assert ids["甲"] not in {r["id"] for r in candidates}
    assert read_context(project) == before
    document = project.read_screenplay(one)
    assert document["timeline_id"] == first["timeline_id"] and document["timeline_revision"] == 0
    with pytest.raises(ValueError, match="边界"):
        project.create_adaptation_span(one, "未导入", start_node_id=ids["甲敲门"], end_node_id=ids["甲进屋"], expected_timeline_revision=0, reason="拒绝全量默认")
    assert project.adaptation_snapshot() == source


def test_import_uses_current_adaptation_order_and_state_not_source_disclosure(project):
    ids = setup(project)
    project.sync_adaptation()
    # Original disclosure puts event1 before event2; adaptation now puts it after.
    project.move_adaptation_node(ids["事件1"], after_id=ids["事件2"])
    source = copy.deepcopy(project.adaptation_snapshot())
    screenplay_id = create(project)
    imported = import_points(project, screenplay_id, [ids["事件1"]])
    from narrative.state_anchors import anchor_nodes
    assert anchor_nodes(imported)[ids["事件2"]]["sort_id"] < by_id(imported, ids["事件1"])["sort_id"]
    assert ids["事件2"] not in {n["id"] for n in imported["nodes"]}
    assert ids["事件0"] not in {n["id"] for n in imported["nodes"]}
    assert ids["事件3"] not in {n["id"] for n in imported["nodes"]}
    assert ids["场景0"] not in {n["id"] for n in imported["nodes"]}
    coat = [r for r in imported["properties"] if r.get("property_id") == "服装"]
    assert len(coat) == 1 and coat[0]["value"] == "白衣" and coat[0]["event_id"] == ids["事件2"]
    assert ids["桥边"] in {n["id"] for n in imported["nodes"]}
    assert ids["远处"] not in {n["id"] for n in imported["nodes"]}
    assert project.adaptation_snapshot() == source
    repeated = import_points(project, screenplay_id, [ids["事件1"]])
    assert repeated["nodes"] == imported["nodes"] and repeated["properties"] == imported["properties"]
    reopened = GraphOperations(project.project_info().path)
    assert reopened.read_screenplay_timeline(screenplay_id) == project.read_screenplay_timeline(screenplay_id)


def test_copy_content_order_and_suppression_are_local_and_reimport_preserves_edits(project):
    ids = seed(project)
    one, two = create(project), create(project, "第二方案")
    import_points(project, one, [ids["入屋"]])
    import_points(project, two, [ids["入屋"]])
    untouched = copy.deepcopy(project.read_screenplay_timeline(two))
    first = project.read_screenplay_timeline(one)
    project.edit_screenplay_timeline_node(one, ids["甲开门"], {"name": "本地改为敲窗"}, expected_timeline_revision=first["revision"], reason="本地改写")
    first = project.read_screenplay_timeline(one)
    project.move_screenplay_timeline_node(one, ids["甲开门"], after_id=ids["甲进屋"], expected_timeline_revision=first["revision"], reason="本地调序")
    first = project.read_screenplay_timeline(one)
    project.suppress_screenplay_timeline_node(one, ids["甲坐下"], expected_timeline_revision=first["revision"], reason="本地屏蔽")
    local_before = project.read_screenplay_timeline(one)
    assert project.read_screenplay_timeline(two) == untouched
    assert by_id(project.adaptation_snapshot(), ids["甲开门"])["name"] == "甲开门"
    project.edit_adaptation_node(ids["甲开门"], {"name": "来源改为推门"})
    assert project.read_screenplay_timeline(one) == local_before
    imported = import_points(project, one, [ids["入屋"]])
    assert by_id(imported, ids["甲开门"])["name"] == "本地改为敲窗"
    assert by_id(imported, ids["甲开门"])["sort_id"] == by_id(local_before, ids["甲开门"])["sort_id"]
    assert by_id(imported, ids["甲坐下"])["suppressed"]
    assert any(c["id"] == ids["甲开门"] and c["field"] == "name" for c in imported["conflicts"])
    assert project.read_screenplay_timeline(two) == untouched
    imported_two = import_points(project, two, [ids["入屋"]])
    assert by_id(imported_two, ids["甲开门"])["name"] == "来源改为推门"


def test_suppressed_source_is_copied_and_both_revisions_protect_atomic_import(project):
    ids = seed(project)
    project.suppress_adaptation_node(ids["甲敲门"])
    screenplay_id = create(project)
    source_revision = project.adaptation_snapshot()["revision"]
    imported = import_points(project, screenplay_id, [ids["门口与屋内"]])
    assert by_id(imported, ids["甲敲门"])["suppressed"]
    preview = project.preview_adaptation_span(screenplay_id, start_node_id=ids["甲敲门"], end_node_id=ids["甲敲门"])
    assert preview["status"] == "empty"
    before = read_context(project)
    with pytest.raises(ValueError, match="revision"):
        project.import_screenplay_timeline(screenplay_id, [ids["甲进屋"]], 0, source_revision, "过期本地版本")
    project.edit_adaptation_node(ids["甲"], {"name": "源人物改名"})
    before = read_context(project)
    with pytest.raises(ValueError, match="revision"):
        project.import_screenplay_timeline(screenplay_id, [ids["甲进屋"]], imported["revision"], source_revision, "过期来源版本")
    assert read_context(project) == before
    with pytest.raises(ValueError, match="时间点"):
        import_points(project, screenplay_id, [ids["甲"]])
    assert read_context(project) == before


def test_source_deletion_with_same_ids_does_not_touch_local_script_references_or_basis(project):
    ids = seed(project)
    screenplay_id = create(project)
    import_points(project, screenplay_id, [ids["入屋"]])
    timeline = project.read_screenplay_timeline(screenplay_id)
    bound = project.create_adaptation_span(screenplay_id, "范围", start_node_id=ids[QUOTES[0][:-1]], end_node_id=ids[QUOTES[2][:-1]],
                                           expected_timeline_revision=timeline["revision"], reason="本地选段")
    sp = project.read_screenplay(screenplay_id)["screenplay"]
    segment = project.create_script_segment(screenplay_id, bound["span"]["id"], "段落", sp["structure_revision"], "本地段落")
    segment = project.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {"plan": {"summary": "甲进入屋内", "cast_ids": [ids["甲"]]}}}], segment["outline_revision"], reason="本地场表")
    unit = segment["units"][0]
    context = project.read_script_unit_context(unit["id"])
    project.submit_script_unit(unit["id"], [{"type": "dialogue", "payload": {"speaker_id": ids["甲"], "speaker_label": "甲", "text": "有人吗？"}}],
        context["expected_plan_revision"], context["expected_body_revision"], context["span_fingerprint"], context["context_fingerprint"], context["plan_fingerprint"], reason="本地正文")
    local_before = project.read_screenplay(screenplay_id)
    result = project.delete_adaptation_node(ids["甲"], project.adaptation_snapshot()["revision"], "只删除来源人物")
    assert result["affected_spans"] == []
    assert project.read_screenplay(screenplay_id) == local_before
    assert project.read_script_segment(segment["id"])["units"][0]["basis_state"] == "current"
    with pytest.raises(ValueError, match="仍被剧本引用"):
        project.delete_screenplay_timeline_node(screenplay_id, ids["甲"], timeline["revision"], "拒绝删除本地人物")
    # Deleting a source anchor is also independent, including on explicit reimport.
    project.delete_adaptation_node(ids["甲开门"], project.adaptation_snapshot()["revision"], "只删除来源事件")
    assert project.read_adaptation_span(bound["span"]["id"])["status"] == "valid"
    imported = import_points(project, screenplay_id, [ids["入屋"]])
    assert ids["甲开门"] in {n["id"] for n in imported["nodes"]}
    assert any(c["id"] == ids["甲开门"] and c["field"] == "deleted" for c in imported["conflicts"])
    assert project.read_adaptation_span(bound["span"]["id"])["status"] == "valid"

def test_spans_are_owned_by_one_copy_and_cannot_bind_another_screenplay(project):
    ids = seed(project)
    one, two = create(project), create(project, "第二套副本")
    first = import_points(project, one, [ids["入屋"]])
    second = import_points(project, two, [ids["入屋"]])
    bound = project.create_adaptation_span(one, "第一套范围", start_node_id=ids["甲敲门"], end_node_id=ids["甲进屋"],
                                          expected_timeline_revision=first["revision"], reason="绑定第一套副本")
    other = project.create_adaptation_span(two, "第二套范围", start_node_id=ids["甲敲门"], end_node_id=ids["甲进屋"],
                                          expected_timeline_revision=second["revision"], reason="绑定第二套副本")
    assert bound["span_fingerprint"] != other["span_fingerprint"]
    before = read_context(project)
    revision = project.read_screenplay(two)["screenplay"]["structure_revision"]
    with pytest.raises(ValueError, match="跨改编时间线"):
        project.create_script_segment(two, bound["span"]["id"], "错误跨域", revision, "拒绝共享范围")
    assert read_context(project) == before
