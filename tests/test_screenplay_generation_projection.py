"""Generation receives effective facts, never management provenance or hidden prose."""
from __future__ import annotations

import json

from agent_fixtures import project as project
from test_screenplay_core import QUOTES, TEXT, edit, script, seed, span, suppress

from narrative.screenplay_spans import (
    generation_event_ids,
    generation_node,
    generation_state_visible,
)


def source_properties(project, ids, definitions, values, nodes=None):
    sid = project.sources()[0]["id"]
    records = definitions + [dict(row, evidence={"source_id": sid, "quote": row.pop("quote", QUOTES[0])}) for row in values]
    result = project.apply_changes(nodes or [], records, project.graph_project_info()["revision"], reason="隔离生成边界事实fixture")
    assert result["status"] == "accepted", result
    project.sync_adaptation()


def test_effective_input_omits_hidden_quotes_ancestor_narrative_and_provenance(project):
    ids = seed(project)
    source_properties(project, ids, [
        {"id": "称呼", "kind": "property_definition", "value_type": "string", "cardinality": "one"},
        {"id": "关联场景", "kind": "property_definition", "value_type": "node", "cardinality": "one"}], [
        {"id": "source-value-1", "kind": "property_value", "node_id": ids["甲"], "property_id": "称呼", "value": "来客", "event_id": ids["甲敲门"], "quote": TEXT},
        {"id": "source-value-2", "kind": "property_value", "node_id": ids["甲"], "property_id": "关联场景", "value": ids["门口与屋内"], "event_id": ids["甲敲门"]}])
    script(project, ids)
    sid = project.sources()[0]["id"]
    edit(project, ids, ids["甲敲门"], {"evidence": {"source_id": sid, "quote": TEXT},
        "evidence_spans": [{"source_id": sid, "start": 0, "end": len(TEXT), "quote": TEXT}],
        "identity_note": "甲开门的详细旧叙述", "source_marks": [{"source_id": sid, "role": "start", "note": "甲开门"}]})
    edit(project, ids, ids["甲"], {"identity_note": "甲开门的身份说明", "evidence": {"source_id": sid, "quote": TEXT}})
    edit(project, ids, ids["门口与屋内"], {"name": "甲开门的场景叙事"})
    suppress(project, ids, ids["甲开门"])
    result = span(project, ids)
    generated = result["input"]
    text = json.dumps(generated, ensure_ascii=False)
    assert generated["fingerprint_version"] == 2
    assert "甲开门" not in text
    assert not any(key in text for key in ('"evidence"', '"evidence_spans"', '"quote"', '"source_marks"', '"identity_note"', '"disclosure"', '"source_valid_from"'))
    assert ids["甲开门"] in result["member_node_ids"]
    assert ids["甲开门"] not in result["effective_node_ids"] and result["suppressed_count"] == 1
    assert generated["source_event_ids"] == [ids["甲敲门"], ids["甲进屋"]]
    assert all(node["node_type"] not in {"storyline", "big_event", "scene"} and "events" not in node for node in generated["entities"])
    assert all("event_id" not in row and "end_event_id" not in row for cut in generated["states"] for row in cut["values"])
    assert any(row["value"] == "来客" for cut in generated["states"] for row in cut["values"])
    before = result["span_fingerprint"]
    edit(project, ids, ids["甲开门"], {"name": "被屏蔽事件换名"})
    edit(project, ids, ids["甲"], {"identity_note": "仅改管理注释", "evidence": {"source_id": sid, "quote": QUOTES[1]}})
    assert project.read_adaptation_span(result["span"]["id"])["span_fingerprint"] == before


def test_hidden_start_keeps_valid_state_and_hidden_end_still_ends_it(project):
    ids = seed(project)
    script(project, ids)
    screenplay_id = ids["_script"]
    local = project.read_screenplay_timeline(screenplay_id)
    local = project.set_screenplay_timeline_property(screenplay_id, ids["甲"], "伤势", "左臂受伤", event_id=ids["甲敲门"],
        expected_timeline_revision=local["revision"], reason="状态先于有效叙事形成")
    record = next(row for row in local["properties"] if row.get("property_id") == "伤势")
    local = project.set_screenplay_timeline_property(screenplay_id, ids["甲"], "伤势", event_id=ids["甲坐下"], record_id=record["id"], expire=True,
        expected_timeline_revision=local["revision"], reason="明确状态结束")
    edit(project, ids, ids["甲"], {"point_id": ids["甲敲门"]})
    suppress(project, ids, ids["甲敲门"])
    suppress(project, ids, ids["甲坐下"])
    local = edit(project, ids, changes={"node_type": "event", "name": "后续行动", "participants": [ids["甲"]]},
                 parent_id=ids["门口与屋内"], after_id=ids["甲坐下"])
    later = next(n["id"] for n in local["nodes"] if n["name"] == "后续行动")
    result = project.create_adaptation_span(screenplay_id, "有效后续", start_node_id=ids["甲开门"], end_node_id=later,
        expected_timeline_revision=local["revision"], reason="不抹去合法延续状态")
    rows = {cut["event_id"]: cut["values"] for cut in result["input"]["states"]}
    assert any(row["value"] == "左臂受伤" for row in rows[ids["甲开门"]])
    assert any(row["value"] == "左臂受伤" for row in rows[ids["甲进屋"]])
    assert not rows[later]
    assert "甲敲门" not in json.dumps(result["input"], ensure_ascii=False)
    snapshot = project.read_screenplay_timeline(screenplay_id)
    assert generation_state_visible(snapshot, record, ids["甲开门"])
    current = next(row for row in snapshot["properties"] if row["id"] == record["id"])
    assert not generation_state_visible(snapshot, current, later)


def test_event_grounding_never_copies_neighbor_or_character_location(project):
    ids = seed(project)
    sid = project.sources()[0]["id"]
    location = {"id": "L99", "kind": "node", "node_type": "location", "name": "屋内", "disclosure": {"source_id": sid, "start": 0}, "evidence": {"source_id": sid, "quote": QUOTES[0]}}
    source_properties(project, ids, [
        {"id": "地点", "kind": "property_definition", "value_type": "node", "cardinality": "one"},
        {"id": "所在地点", "kind": "property_definition", "value_type": "node", "cardinality": "one"}], [
        {"id": "place-one", "kind": "property_value", "node_id": ids["甲敲门"], "property_id": "地点", "value": "L99", "event_id": ids["甲敲门"]},
        {"id": "place-three", "kind": "property_value", "node_id": ids["甲进屋"], "property_id": "地点", "value": "L99", "event_id": ids["甲进屋"], "quote": QUOTES[2]},
        {"id": "person-place", "kind": "property_value", "node_id": ids["甲"], "property_id": "所在地点", "value": "L99", "event_id": ids["甲敲门"]}], [location])
    script(project, ids)
    result = span(project, ids)
    matrix = {row["event_id"]: row for row in result["input"]["event_grounding"]}
    assert matrix[ids["甲敲门"]]["location_ids"] == ["L99"]
    assert matrix[ids["甲进屋"]]["location_ids"] == ["L99"]
    assert matrix[ids["甲开门"]]["location_status"] == "unrecorded"
    assert matrix[ids["甲开门"]]["location_ids"] == []
    cuts = {row["event_id"]: row["values"] for row in result["input"]["states"]}
    assert not any(row["node_id"] == ids["甲敲门"] for row in cuts[ids["甲开门"]])
    suppress(project, ids, ids["甲敲门"])
    result = project.read_adaptation_span(result["span"]["id"])
    assert not any(row["node_id"] == ids["甲敲门"] for cut in result["input"]["states"] for row in cut["values"])


def test_generation_node_scope_blocks_other_events_and_action_coverage_folds_parent(project):
    ids = seed(project)
    script(project, ids)
    local = edit(project, ids, changes={"node_type": "action", "name": "抬手", "participants": [ids["甲"]]},
                 parent_id=ids["甲敲门"], after_id=ids["甲敲门"])
    action = next(n["id"] for n in local["nodes"] if n["name"] == "抬手")
    result = project.preview_adaptation_span(ids["_script"], selection_ids=[action])
    assert result["effective_node_ids"] == [action]
    assert result["input"]["source_event_ids"] == [ids["甲敲门"]]
    assert generation_event_ids(local, [action]) == [ids["甲敲门"]]
    assert generation_node(local, ids["甲坐下"], effective_node_ids=[action]) is None
    assert generation_node(local, ids["甲敲门"], effective_node_ids=[action]) is None
    container = generation_node(local, ids["门口与屋内"], effective_node_ids=[action])
    assert container["reference_only"] and "name" not in container and "events" not in container
    assert result["input"]["nodes"][0]["name"] == "抬手"
    assert "甲敲门" not in json.dumps(result["input"], ensure_ascii=False)
