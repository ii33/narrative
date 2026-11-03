"""Appending selected adaptation time points preserves authored screenplay work."""
from __future__ import annotations

import copy

import pytest
from agent_fixtures import project as project
from test_screenplay_core import seed
from test_screenplay_timeline import by_id, create, import_points

from narrative.screenplay_store import read_context


def append(project, screenplay_id, points):
    return project.append_screenplay_timeline(screenplay_id, points,
        project.read_screenplay_timeline(screenplay_id)["revision"], project.adaptation_snapshot()["revision"], "追加指定时间点")


def edit(project, screenplay_id, node_id, changes):
    return project.edit_screenplay_timeline_node(screenplay_id, node_id, changes,
        expected_timeline_revision=project.read_screenplay_timeline(screenplay_id)["revision"], reason="保留本地创作")


def write_body(project, screenplay_id, event_id, person_id):
    local = project.read_screenplay_timeline(screenplay_id)
    bound = project.create_adaptation_span(screenplay_id, "已有段落", start_node_id=event_id, end_node_id=event_id,
        expected_timeline_revision=local["revision"], reason="已有范围")
    screenplay = project.read_screenplay(screenplay_id)["screenplay"]
    segment = project.create_script_segment(screenplay_id, bound["span"]["id"], "已有段落", screenplay["structure_revision"], "已有段落")
    segment = project.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {"plan": {
        "summary": "人物保持原有行动。", "cast_ids": [person_id]}}}], segment["outline_revision"], reason="已有场表")
    unit = segment["units"][0]
    context = project.read_script_unit_context(unit["id"])
    project.submit_script_unit(unit["id"], [{"type": "action", "payload": {"text": "已有正文必须保留。"}}],
        context["expected_plan_revision"], context["expected_body_revision"], context["span_fingerprint"],
        context["context_fingerprint"], context["plan_fingerprint"], reason="已有正文")
    return segment["id"], unit["id"], bound["span"]["id"]


def test_append_at_tail_preserves_old_content_order_and_written_work_then_deduplicates(project):
    ids = seed(project)
    screenplay_id = create(project)
    import_points(project, screenplay_id, [ids["甲坐下"]])
    edit(project, screenplay_id, ids["门口与屋内"], {"name": "本地场景名"})
    edit(project, screenplay_id, ids["甲坐下"], {"name": "本地静坐"})
    edit(project, screenplay_id, ids["甲"], {"name": "剧本甲"})
    local = project.read_screenplay_timeline(screenplay_id)
    project.edit_screenplay_timeline_node(screenplay_id, changes={"node_type": "event", "name": "本地插入"},
        parent_id=ids["门口与屋内"], expected_timeline_revision=local["revision"], reason="本地插入并调序")
    segment_id, unit_id, span_id = write_body(project, screenplay_id, ids["甲坐下"], ids["甲"])
    project.edit_adaptation_node(ids["门口与屋内"], {"name": "源场景改名"})
    project.edit_adaptation_node(ids["甲坐下"], {"name": "源人物坐下"})
    project.edit_adaptation_node(ids["甲"], {"name": "源甲改名"})
    source = copy.deepcopy(project.adaptation_snapshot())
    before = copy.deepcopy(project.read_screenplay_timeline(screenplay_id))
    authored = read_context(project)[0]
    candidates = project.import_screenplay_timeline_candidates(screenplay_id)
    assert next(r for r in candidates if r["id"] == ids["甲坐下"])["imported"]
    assert not next(r for r in candidates if r["id"] == ids["甲敲门"])["imported"]
    result = append(project, screenplay_id, [ids["甲开门"], ids["甲敲门"]])
    old_ids = [n["id"] for n in before["nodes"]]
    assert [n["id"] for n in result["nodes"] if n["id"] in old_ids] == old_ids
    assert [n["id"] for n in result["nodes"] if n["id"] not in old_ids] == [ids["甲敲门"], ids["甲开门"]]
    assert min(n["sort_id"] for n in result["nodes"] if n["id"] not in old_ids) > max(n["sort_id"] for n in before["nodes"])
    for previous in before["nodes"]:
        current = by_id(result, previous["id"])
        if previous["id"] == ids["门口与屋内"]:
            assert {k: v for k, v in current.items() if k != "events"} == {k: v for k, v in previous.items() if k != "events"}
            assert current["events"] == previous["events"] + [ids["甲敲门"], ids["甲开门"]]
        else:
            assert current == previous
    current_state = read_context(project)[0]
    for table in ("screenplays", "script_segments", "script_units", "script_blocks", "script_entity_usages", "script_writing_bases"):
        assert current_state[table] == authored[table]
    assert project.read_script_segment(segment_id)["units"][0]["id"] == unit_id
    assert project.read_script_segment(segment_id)["units"][0]["basis_state"] == "current"
    assert project.read_adaptation_span(span_id)["effective_node_ids"] == [ids["甲坐下"]]
    assert result["import_summary"]["mode"] == "append"
    assert result["import_summary"]["added_node_ids"] == [ids["甲敲门"], ids["甲开门"]]
    assert ids["甲进屋"] not in {n["id"] for n in result["nodes"]}
    repeated = append(project, screenplay_id, [ids["甲敲门"], ids["甲敲门"], ids["甲开门"]])
    assert repeated["nodes"] == result["nodes"] and repeated["properties"] == result["properties"]
    assert repeated["import_summary"]["added_node_ids"] == repeated["import_summary"]["added_property_ids"] == []
    assert project.adaptation_snapshot() == source


def initial_state(project):
    ids = seed(project)
    source = project.set_adaptation_property(ids["甲"], "服装", "青衣", event_id=ids["甲敲门"])
    record = next(r for r in source["properties"] if r.get("property_id") == "服装")
    screenplay_id = create(project)
    import_points(project, screenplay_id, [ids["甲敲门"]])
    return ids, screenplay_id, record["id"]


def test_append_continues_unedited_open_source_state_with_explicit_boundary_update(project):
    ids, screenplay_id, record_id = initial_state(project)
    old = copy.deepcopy(project.read_screenplay_timeline(screenplay_id))
    project.set_adaptation_property(ids["甲"], "服装", "白衣", event_id=ids["甲坐下"], record_id=record_id)
    result = append(project, screenplay_id, [ids["甲坐下"]])
    prior = next(r for r in result["properties"] if r["id"] == record_id)
    assert prior["value"] == "青衣" and prior["end_event_id"] == ids["甲坐下"]
    assert result["import_summary"]["boundary_updates"] == [{"record_id": record_id, "previous_end_event_id": None, "end_event_id": ids["甲坐下"]}]
    new_values = [r for r in result["properties"] if r.get("property_id") == "服装" and r["id"] != record_id]
    assert len(new_values) == 1 and new_values[0]["value"] == "白衣"
    assert all(by_id(result, n["id"])["sort_id"] == n["sort_id"] for n in old["nodes"])
    assert ids["甲开门"] not in {n["id"] for n in result["nodes"]}
    assert ids["甲进屋"] not in {n["id"] for n in result["nodes"]}
    repeated = append(project, screenplay_id, [ids["甲坐下"]])
    assert repeated["properties"] == result["properties"]
    assert repeated["import_summary"]["boundary_updates"] == []


@pytest.mark.parametrize("changed", ["local_value", "source_value"])
def test_append_never_replaces_changed_existing_value_or_silently_closes_it(project, changed):
    ids, screenplay_id, record_id = initial_state(project)
    write_body(project, screenplay_id, ids["甲敲门"], ids["甲"])
    if changed == "local_value":
        local = project.read_screenplay_timeline(screenplay_id)
        project.set_screenplay_timeline_property(screenplay_id, ids["甲"], "服装", "本地红衣", record_id=record_id,
            expected_timeline_revision=local["revision"], reason="本地属性改写")
    else:
        project.set_adaptation_property(ids["甲"], "服装", "源修正青衣", event_id=ids["甲敲门"], record_id=record_id)
    project.set_adaptation_property(ids["甲"], "服装", "白衣", event_id=ids["甲坐下"], record_id=record_id)
    before = read_context(project)
    with pytest.raises(ValueError, match="区间.*重叠.*未关闭或改写"):
        append(project, screenplay_id, [ids["甲坐下"]])
    assert read_context(project) == before


def test_append_preserves_authored_closed_interval_and_skips_existing_value_updates(project):
    ids, screenplay_id, record_id = initial_state(project)
    local = project.read_screenplay_timeline(screenplay_id)
    local = project.edit_screenplay_timeline_node(screenplay_id, changes={"node_type": "event", "name": "本地服装失效"},
        parent_id=ids["门口与屋内"], after_id=ids["甲敲门"], expected_timeline_revision=local["revision"], reason="本地边界")
    local_end = next(n["id"] for n in local["nodes"] if n["name"] == "本地服装失效")
    project.set_screenplay_timeline_property(screenplay_id, ids["甲"], "服装", event_id=local_end, record_id=record_id,
        expire=True, expected_timeline_revision=local["revision"], reason="明确本地失效")
    previous = copy.deepcopy(next(r for r in project.read_screenplay_timeline(screenplay_id)["properties"] if r["id"] == record_id))
    project.set_adaptation_property(ids["甲"], "服装", "白衣", event_id=ids["甲坐下"], record_id=record_id)
    result = append(project, screenplay_id, [ids["甲坐下"]])
    assert next(r for r in result["properties"] if r["id"] == record_id) == previous
    assert result["import_summary"]["boundary_updates"] == []


def test_append_checks_both_revisions_and_invalid_selections_atomically(project):
    ids = seed(project)
    screenplay_id = create(project)
    import_points(project, screenplay_id, [ids["甲敲门"]])
    local = project.read_screenplay_timeline(screenplay_id)
    source_revision = project.adaptation_snapshot()["revision"]
    before = read_context(project)
    with pytest.raises(ValueError, match="revision"):
        project.append_screenplay_timeline(screenplay_id, [ids["甲坐下"]], 0, source_revision, "本地过期")
    assert read_context(project) == before
    project.edit_adaptation_node(ids["甲"], {"name": "来源改名"})
    before = read_context(project)
    with pytest.raises(ValueError, match="revision"):
        project.append_screenplay_timeline(screenplay_id, [ids["甲坐下"]], local["revision"], source_revision, "来源过期")
    with pytest.raises(ValueError, match="时间点"):
        append(project, screenplay_id, [])
    with pytest.raises(ValueError, match="时间点"):
        append(project, screenplay_id, [ids["甲"]])
    assert read_context(project) == before

def test_append_skips_existing_value_even_when_source_changed_without_new_state(project):
    ids, screenplay_id, record_id = initial_state(project)
    previous = copy.deepcopy(project.read_screenplay_timeline(screenplay_id)["properties"])
    project.set_adaptation_property(ids["甲"], "服装", "来源修正服装", event_id=ids["甲敲门"], record_id=record_id)
    result = append(project, screenplay_id, [ids["甲坐下"]])
    assert result["properties"] == previous
    assert record_id in result["import_summary"]["skipped_existing_property_ids"]
    assert result["import_summary"]["boundary_updates"] == []
