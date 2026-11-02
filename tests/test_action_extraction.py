"""Action drafts enrich an existing event without replacing its narrative level."""
from agent_fixtures import project as project

TEXT = "甲推门进屋，走到窗边，停下张望。"


def seed(project):
    sid = project.import_source("原文", TEXT)
    draft = {"nodes": [
        {"type": "person", "name": "甲", "quotes": [TEXT]},
        {"type": "scene", "name": "屋内", "quotes": [TEXT]},
        {"type": "event", "name": "甲入屋查看", "scene": "屋内", "participants": ["甲"], "quotes": [TEXT]},
    ], "properties": []}
    assert project.submit_extraction(sid, draft)["status"] == "accepted"
    return sid, next(n for n in project.graph_snapshot()["nodes"] if n["node_type"] == "event")


def test_actions_attach_to_existing_event_and_repeat_without_duplicate(project):
    sid, parent = seed(project)
    original = project.graph_snapshot()
    result = {"nodes": [
        {"type": "action", "name": name, "event": parent["id"], "participants": ["甲"], "quotes": [quote]}
        for name, quote in [("推门入屋", "甲推门进屋"), ("走到窗边", "走到窗边"), ("停下观察", "停下张望")]
    ], "properties": []}
    assert project.preview_extraction(sid, result)["valid"]
    assert project.graph_snapshot() == original
    applied = project.submit_extraction(sid, result)
    assert applied["status"] == "accepted"
    current = project.graph_snapshot()
    actions = [n for n in current["nodes"] if n["node_type"] == "action"]
    assert len(actions) == 3 and all(n["id"].startswith("A") for n in actions)
    event = next(n for n in current["nodes"] if n["id"] == parent["id"])
    assert set(event["events"]) == {n["id"] for n in actions}
    assert project.submit_extraction(sid, result)["status"] == "unchanged"
    assert project.graph_snapshot() == current


def test_new_actions_require_an_event_parent_without_writing(project):
    sid, _ = seed(project)
    before = project.graph_snapshot()
    draft = {"nodes": [{"type": "action", "name": "停下观察", "quotes": ["停下张望"]}], "properties": []}
    assert not project.preview_extraction(sid, draft)["valid"]
    assert project.submit_extraction(sid, draft)["status"] == "rejected"
    assert project.graph_snapshot() == before


def test_adding_an_earlier_action_keeps_type_numbering_in_source_order(project):
    from narrative.order_ids import order_key

    sid, parent = seed(project)
    for name, quote in [("停下观察", "停下张望"), ("推门入屋", "甲推门进屋")]:
        result = {"nodes": [{"type": "action", "name": name, "event": parent["id"], "quotes": [quote]}],
                  "properties": []}
        assert project.submit_extraction(sid, result)["status"] == "accepted"
    actions = {n["name"]: n for n in project.graph_snapshot()["nodes"] if n["node_type"] == "action"}
    assert order_key(actions["推门入屋"]["id"]) < order_key(actions["停下观察"]["id"])
