"""Current screenplay ranges and strict authoritative episode/act organization."""
from __future__ import annotations

import copy

import pytest
from agent_fixtures import project as project

from narrative.screenplay_store import TABLES

TEXT = "甲敲门。甲开门。甲进屋。甲坐下。"
QUOTES = ["甲敲门。", "甲开门。", "甲进屋。", "甲坐下。"]


def seed(project):
    sid = project.import_source("剧本合成", TEXT)
    nodes = [{"type": "person", "name": "甲", "quotes": [QUOTES[0]]},
             {"type": "big_event", "name": "入屋", "quotes": [TEXT]},
             {"type": "scene", "name": "门口与屋内", "big_event": "入屋", "quotes": [TEXT]}]
    nodes += [{"type": "event", "name": quote[:-1], "scene": "门口与屋内", "participants": ["甲"], "quotes": [quote]} for quote in QUOTES]
    result = project.submit_extraction(sid, {"nodes": nodes, "properties": []})
    assert result["status"] == "accepted", result
    state = project.sync_adaptation()
    return {n["name"]: n["id"] for n in state["nodes"]}


def tables(project):
    with project._store.connect() as db:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def script(project, ids, title="进门", format="single"):
    document = project.create_screenplay(title, format=format, reason="合成剧本")
    screenplay_id = document["screenplay"]["id"]
    source = project.adaptation_snapshot()
    points = [n["id"] for n in source["nodes"] if n["node_type"] in {"storyline", "big_event", "scene", "event", "action"}]
    project.import_screenplay_timeline(screenplay_id, points, 0, source["revision"], "显式导入改编时间点")
    ids["_script"] = screenplay_id
    return project.read_screenplay(screenplay_id)


def span(project, ids, first="甲敲门", last="甲进屋"):
    if "_script" not in ids:
        script(project, ids)
    screenplay_id = ids["_script"]
    return project.create_adaptation_span(screenplay_id, "入屋段", start_node_id=ids[first], end_node_id=ids[last],
                                          expected_timeline_revision=project.read_screenplay_timeline(screenplay_id)["revision"], reason="合成选段")


def edit(project, ids, *args, **kwargs):
    if "_script" not in ids:
        script(project, ids)
    return project.edit_screenplay_timeline_node(ids["_script"], *args, expected_timeline_revision=project.read_screenplay_timeline(ids["_script"])["revision"], reason="本地编辑", **kwargs)


def move(project, ids, *args, **kwargs):
    return project.move_screenplay_timeline_node(ids["_script"], *args, expected_timeline_revision=project.read_screenplay_timeline(ids["_script"])["revision"], reason="本地移动", **kwargs)


def suppress(project, ids, *args, **kwargs):
    return project.suppress_screenplay_timeline_node(ids["_script"], *args, expected_timeline_revision=project.read_screenplay_timeline(ids["_script"])["revision"], reason="本地屏蔽", **kwargs)


def delete(project, ids, node_id):
    return project.delete_screenplay_timeline_node(ids["_script"], node_id, project.read_screenplay_timeline(ids["_script"])["revision"], "明确删除本地时间点")


def segment(project, screenplay_id, span_id, title="入屋段", **kwargs):
    current = project.read_screenplay(screenplay_id)["screenplay"]
    return project.create_script_segment(screenplay_id, span_id, title, current["structure_revision"], "合成段落", **kwargs)


def structure(project, screenplay_id, actions, **kwargs):
    current = project.read_screenplay(screenplay_id)["screenplay"]
    return project.apply_screenplay_structure(screenplay_id, actions, current["structure_revision"], "合成组织", **kwargs)


def test_read_and_preview_do_not_initialize_any_script_table(project):
    ids = seed(project)
    before = tables(project)
    assert project.list_screenplays() == []
    with pytest.raises(ValueError, match="剧本不存在"):
        project.preview_adaptation_span("missing", selection_ids=[ids["甲敲门"]])
    assert tables(project) == before and not set(TABLES) & before
    script(project, ids)
    before = tables(project)
    snapshot = project.read_screenplay_timeline(ids["_script"])
    first = project.preview_adaptation_span(ids["_script"], selection_ids=[ids["门口与屋内"], ids["甲敲门"]])
    second = project.preview_adaptation_span(ids["_script"], selection_ids=[ids["入屋"]])
    assert first["member_node_ids"] == [ids[quote[:-1]] for quote in QUOTES]
    assert first["span_fingerprint"] == second["span_fingerprint"]
    assert first["span"]["timeline_id"] == second["span"]["timeline_id"]
    assert tables(project) == before
    assert project.read_screenplay_timeline(ids["_script"]) == snapshot


def test_explicit_creation_initializes_current_tables_atomically_and_preserves_source(project):
    ids = seed(project)
    source = copy.deepcopy(project.graph_snapshot())
    with pytest.raises(ValueError):
        project.create_screenplay("错误", format="episode", reason="合成错误")
    assert not set(TABLES) & tables(project)
    created = span(project, ids)
    assert set(TABLES) <= tables(project)
    row = segment(project, ids["_script"], created["span"]["id"])
    assert row["span"]["effective_node_ids"] == [ids[q[:-1]] for q in QUOTES[:3]]
    assert row["units"] == [] and row["outline_state"] == "planning"
    assert project.graph_snapshot() == source


def test_span_dynamic_membership_ignores_outside_changes_and_uniform_translation(project):
    ids = seed(project)
    current = span(project, ids)
    span_id = current["span"]["id"]
    initial = current["span_fingerprint"]
    edit(project, ids, ids["甲坐下"], {"name": "甲静坐"})
    assert project.read_adaptation_span(span_id)["span_fingerprint"] == initial
    state = edit(project, ids, changes={"node_type": "event", "name": "整理衣襟"},
                                         parent_id=ids["门口与屋内"], after_id=ids["甲开门"])
    inserted = next(n["id"] for n in state["nodes"] if n["name"] == "整理衣襟")
    assert inserted in project.read_adaptation_span(span_id)["member_node_ids"]
    assert project.read_adaptation_span(span_id)["span_fingerprint"] != initial
    move(project, ids, inserted, after_id=ids["甲坐下"])
    assert project.read_adaptation_span(span_id)["span_fingerprint"] == initial
    state = edit(project, ids, changes={"node_type": "scene", "name": "远处"})
    outside_scene = next(n["id"] for n in state["nodes"] if n["name"] == "远处")
    state = edit(project, ids, changes={"node_type": "event", "name": "风吹过"}, parent_id=outside_scene)
    outside = next(n["id"] for n in state["nodes"] if n["name"] == "风吹过")
    move(project, ids, ids["门口与屋内"], after_id=outside)
    assert project.read_adaptation_span(span_id)["span_fingerprint"] == initial


def test_suppressed_container_selection_and_explicit_boundary_semantics(project):
    ids = seed(project)
    created = span(project, ids)
    suppress(project, ids, ids["甲敲门"])
    preview = project.preview_adaptation_span(ids["_script"], selection_ids=[ids["门口与屋内"]])
    assert preview["span"]["start_node_id"] == ids["甲开门"]
    explicit = project.preview_adaptation_span(ids["_script"], selection_ids=[ids["甲敲门"]])
    assert explicit["status"] == "empty" and explicit["member_node_ids"] == [ids["甲敲门"]]
    assert project.read_adaptation_span(created["span"]["id"])["member_node_ids"][0] == ids["甲敲门"]
    suppress(project, ids, ids["门口与屋内"])
    with pytest.raises(ValueError, match="没有可解析"):
        project.preview_adaptation_span(ids["_script"], selection_ids=[ids["门口与屋内"]])
    assert project.read_adaptation_span(created["span"]["id"])["status"] == "empty"


def test_screenplay_uses_single_authoritative_tree_and_atomic_mode_conversion(project):
    ids = seed(project)
    movie = script(project, ids, "单片")
    movie_id = movie["screenplay"]["id"]
    span_id = span(project, ids)["span"]["id"]
    one = segment(project, movie_id, span_id, "第一段")
    two = segment(project, movie_id, span_id, "第二段")
    movie = structure(project, movie_id, [{"op": "create_act", "id": "act-one", "title": "第一幕"},
        {"op": "move", "child_kind": "segment", "child_id": one["id"], "parent_kind": "act", "parent_id": "act-one"}])
    assert movie["segment_order"] == [one["id"], two["id"]]
    before = project.read_screenplay(movie_id)
    with pytest.raises(ValueError, match="搬移目标"):
        structure(project, movie_id, [{"op": "delete_act", "act_id": "act-one", "expected_revision": 1}])
    assert project.read_screenplay(movie_id) == before
    with pytest.raises(ValueError):
        structure(project, movie_id, [], format="series")
    assert project.read_screenplay(movie_id) == before
    movie = structure(project, movie_id, [{"op": "create_episode", "id": "episode-one", "title": "第1集"},
        {"op": "move", "child_kind": "act", "child_id": "act-one", "parent_kind": "episode", "parent_id": "episode-one"},
        {"op": "move", "child_kind": "segment", "child_id": two["id"], "parent_kind": "episode", "parent_id": "episode-one"}], format="series")
    assert movie["screenplay"]["format"] == "series"
    assert movie["acts"][0]["episode_id"] == "episode-one"
    movie = structure(project, movie_id, [{"op": "delete_episode", "episode_id": "episode-one", "expected_revision": 1,
        "move_to": {"parent_kind": "screenplay", "parent_id": movie_id}}], format="single")
    assert movie["episodes"] == [] and movie["acts"][0]["episode_id"] is None
    assert project.check_screenplay(movie_id)["ok"]


def test_series_default_episode_cross_screenplay_rejection_and_segment_delete(project):
    ids = seed(project)
    series = script(project, ids, "剧集", format="series")
    span_id = span(project, ids)["span"]["id"]
    other = project.create_screenplay("别的剧本", reason="合成隔离")
    row = segment(project, series["screenplay"]["id"], span_id)
    entry = next(e for e in project.read_screenplay(series["screenplay"]["id"])["structure_entries"] if e["child_id"] == row["id"])
    assert entry["parent_id"] == series["episodes"][0]["id"]
    with pytest.raises(ValueError, match="跨剧本"):
        structure(project, series["screenplay"]["id"], [{"op": "move", "child_kind": "segment", "child_id": row["id"],
            "parent_kind": "screenplay", "parent_id": other["screenplay"]["id"]}])
    result = structure(project, series["screenplay"]["id"], [{"op": "delete_segment", "segment_id": row["id"],
        "expected_revision": row["revision"], "body_policy": "require_empty"}])
    assert result["segments"] == []
    assert project.read_adaptation_span(span_id)["status"] == "valid"


def test_anchor_delete_and_reversal_do_not_block_unrelated_adaptation_edits(project):
    ids = seed(project)
    created = span(project, ids)
    screenplay_id = ids["_script"]
    segment(project, screenplay_id, created["span"]["id"])
    move(project, ids, ids["甲敲门"], after_id=ids["甲进屋"])
    result = project.read_adaptation_span(created["span"]["id"])
    assert result["status"] == "invalid" and result["diagnostics"][0]["code"] == "SPAN_REVERSED"
    edit(project, ids, ids["甲"], {"name": "改编甲"})
    deleted = delete(project, ids, ids["甲敲门"])
    assert created["span"]["id"] in {r["span_id"] for r in deleted["affected_spans"]}
    assert project.read_adaptation_span(created["span"]["id"])["diagnostics"][0]["code"] == "SPAN_ANCHOR_MISSING"
    assert len(project.read_screenplay(screenplay_id)["segments"]) == 1
    assert not project.check_screenplay(screenplay_id)["ok"]
    assert "边界已删除" in project.export_screenplay(screenplay_id)["text"]

def test_five_level_selection_deduplicates_and_reports_interleaving_overlap(project):
    ids = seed(project)
    state = edit(project, ids, changes={"node_type": "storyline", "name": "进门故事", "events": [ids["入屋"]]})
    story_id = next(n["id"] for n in state["nodes"] if n["name"] == "进门故事")
    state = edit(project, ids, changes={"node_type": "action", "name": "抬手"}, parent_id=ids["甲敲门"], after_id=ids["甲敲门"])
    action_id = next(n["id"] for n in state["nodes"] if n["name"] == "抬手")
    preview = project.preview_adaptation_span(ids["_script"], selection_ids=[story_id, ids["门口与屋内"], ids["甲敲门"], action_id])
    assert preview["member_node_ids"] == [ids["甲敲门"], action_id, ids["甲开门"], ids["甲进屋"], ids["甲坐下"]]
    state = edit(project, ids, changes={"node_type": "scene", "name": "交错场景"})
    other_scene = next(n["id"] for n in state["nodes"] if n["name"] == "交错场景")
    state = edit(project, ids, changes={"node_type": "event", "name": "交错事件"}, parent_id=other_scene, after_id=ids["甲开门"])
    outside = next(n["id"] for n in state["nodes"] if n["name"] == "交错事件")
    preview = project.preview_adaptation_span(ids["_script"], selection_ids=[ids["门口与屋内"]])
    assert outside in preview["member_node_ids"]
    assert any(d["code"] == "SPAN_INTERLEAVED" for d in preview["diagnostics"])
    span(project, ids)
    second = span(project, ids)
    assert any(d["code"] == "SPAN_OVERLAP" for d in second["diagnostics"])


@pytest.mark.parametrize("suppressed", ["owner", "holder", "location", "start", "end"])
def test_writing_span_input_obeys_existing_adaptation_visibility(suppressed):
    from narrative.screenplay_spans import resolve_span
    adaptation = {"timeline_id": "test-axis", "nodes": [
        {"id": "start", "name": "起点", "node_type": "event", "sort_id": 100, "participants": ["owner", "holder"]},
        {"id": "end", "name": "终点", "node_type": "event", "sort_id": 200, "participants": ["owner", "holder"]},
        {"id": "owner", "name": "甲", "node_type": "person", "sort_id": 1000},
        {"id": "holder", "name": "乙", "node_type": "person", "sort_id": 1001},
        {"id": "location", "name": "桥边", "node_type": "location", "sort_id": 1002}],
        "properties": [{"id": "地点", "kind": "property_definition", "value_type": "node", "cardinality": "one"},
                       {"id": "v1", "kind": "property_value", "node_id": "owner", "property_id": "地点", "value": "location",
                        "holder_id": "holder", "scope": "cognition", "event_id": "start", "end_event_id": "end"}]}
    definition = {"id": "span", "timeline_id": "test-axis", "start_node_id": "start", "end_node_id": "end"}
    initial = resolve_span(adaptation, definition)
    assert initial["input"]["states"][0]["values"][0]["id"] == "v1"
    next(n for n in adaptation["nodes"] if n["id"] == suppressed)["suppressed"] = True
    current = resolve_span(adaptation, definition)
    if suppressed == "end":
        assert any(s["values"] for s in current["input"]["states"])  # hidden end still bounds future validity
    else:
        assert not any(s["values"] for s in current["input"]["states"])
    assert suppressed not in {n["id"] for n in current["input"]["entities"]}
    assert all(suppressed not in n.get("participants", []) for n in current["input"]["nodes"])

def test_export_body_types_invalid_source_retention_and_explicit_segment_discard(project, tmp_path):
    from narrative.screenplay_store import read_context
    ids = seed(project)
    source = project.graph_snapshot()
    bound = span(project, ids)["span"]["id"]
    screenplay_id = ids["_script"]
    row = segment(project, screenplay_id, bound)
    context = project.read_scene_table_context(row["id"])
    table = project.submit_scene_table(row["id"], [
        {"kind": "scene", "scene_number": "1A", "plan": {"title": "入屋", "summary": "甲进入屋内。", "location_text": "门口", "time_label": "日", "interior_exterior": "INT"}},
        {"kind": "screen_segment", "plan": {"summary": "显示片名", "presentation": "title_card"}}],
        context["expected_outline_revision"], context["span_fingerprint"], context["context_fingerprint"], reason="合成场表")
    scene, card = table["units"]
    def write(unit, blocks):
        context = project.read_script_unit_context(unit["id"])
        return project.submit_script_unit(unit["id"], blocks, context["expected_plan_revision"], context["expected_body_revision"],
            context["span_fingerprint"], context["context_fingerprint"], context["plan_fingerprint"], reason="合成正文")
    write(scene, [{"type": "action", "payload": {"text": "甲进入屋内。"}},
                  {"type": "dialogue", "payload": {"speaker_id": ids["甲"], "speaker_label": "甲", "text": "有人吗？", "delivery": "off_screen", "parenthetical": "轻声"}},
                  {"type": "screen_text", "payload": {"text": "次日", "presentation": "overlay"}},
                  {"type": "sound", "payload": {"text": "脚步声", "category": "effect"}},
                  {"type": "transition", "payload": {"kind": "cut", "text": "切黑"}}])
    write(card, [{"type": "screen_text", "payload": {"text": "入屋短片", "presentation": "card"}}])
    exported = project.export_screenplay(screenplay_id, path=tmp_path / "剧本.txt")
    assert (tmp_path / "剧本.txt").read_text(encoding="utf-8") == exported["text"]
    assert all(word in exported["text"] for word in ["1A INT 门口 日", "甲（画外）", "（轻声）", "有人吗？", "【字幕】次日", "【声音】脚步声", "【转场】切黑"])
    structured = project.export_screenplay(screenplay_id, format="json")
    assert structured["document"]["segments"][0]["units"][0]["id"] == scene["id"]
    delete(project, ids, ids["甲敲门"])
    retained = project.read_script_segment(row["id"])
    assert retained["units"][0]["basis_state"] == "invalid"
    assert retained["units"][0]["blocks"][1]["payload"]["text"] == "有人吗？"
    assert "有人吗？" in project.export_screenplay(screenplay_id)["text"]
    assert "边界已删除" in project.export_screenplay(screenplay_id)["text"]
    before = read_context(project)[0]
    with pytest.raises(ValueError, match="已有场次"):
        structure(project, screenplay_id, [{"op": "delete_segment", "segment_id": row["id"], "expected_revision": retained["revision"], "body_policy": "require_empty"}])
    assert read_context(project)[0] == before
    structure(project, screenplay_id, [{"op": "delete_segment", "segment_id": row["id"], "expected_revision": retained["revision"], "body_policy": "discard"}])
    state = read_context(project)[0]
    assert all(not state[key] for key in ("script_segments", "script_units", "script_blocks", "script_entity_usages", "script_writing_bases"))
    assert bound in state["adaptation_spans"]
    assert project.graph_snapshot() == source

@pytest.mark.parametrize("reference", ["cast", "location", "continuity", "dialogue", "sound", "usage"])
def test_authored_script_entity_references_protect_physical_deletion_atomically(project, reference):
    from narrative.screenplay_store import read_context
    ids = seed(project)
    target = ids["甲"]
    if reference == "location":
        state = edit(project, ids, changes={"node_type": "location", "name": "屋内"})
        target = next(n["id"] for n in state["nodes"] if n["name"] == "屋内")
    bound = span(project, ids)["span"]["id"]
    screenplay = ids["_script"]
    row = segment(project, screenplay, bound)
    plan = {"summary": "甲进入屋内。"}
    if reference == "cast":
        plan["cast_ids"] = [target]
    elif reference == "location":
        plan["location_id"] = target
    elif reference == "continuity":
        plan["continuity_requirements"] = [{"entity_id": target, "requirement": "保持服装"}]
    row = project.apply_scene_table_changes(row["id"], [{"op": "insert", "unit": {"plan": plan}}], row["outline_revision"], reason="合成场表")
    unit = row["units"][0]
    blocks = [{"type": "action", "payload": {"text": "保留此段正文。"}}]
    if reference == "dialogue":
        blocks.append({"type": "dialogue", "payload": {"speaker_id": target, "text": "有人吗？"}})
    elif reference == "sound":
        blocks.append({"type": "sound", "payload": {"text": "脚步声", "entity_id": target}})
    context = project.read_script_unit_context(unit["id"])
    kwargs = {"entity_usages": [{"entity_id": target, "role": "mentioned"}]} if reference == "usage" else {}
    project.submit_script_unit(unit["id"], blocks, context["expected_plan_revision"], context["expected_body_revision"],
        context["span_fingerprint"], context["context_fingerprint"], context["plan_fingerprint"], reason="合成正文", **kwargs)
    before = read_context(project)
    with pytest.raises(ValueError, match="仍被剧本引用.*先显式修正引用"):
        delete(project, ids, target)
    assert read_context(project) == before
    assert project.read_script_segment(row["id"])["units"][0]["blocks"][0]["payload"]["text"] == "保留此段正文。"
    assert project.check_screenplay(screenplay)["ok"]
