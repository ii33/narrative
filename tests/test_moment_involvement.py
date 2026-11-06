"""Moment membership is a time/context filtered read projection, never stored duplication."""
from copy import deepcopy

from agent_fixtures import project as project
from test_browse_detail_sync import choose
from test_chat_scroll import transcript_shell
from test_static_check_ui import click, plain, visual_find

from narrative.browse_view import related_nodes
from narrative.node_involvement import involvement
from narrative.operations import Operations
from narrative.property_audit import audit_properties

TEXT = "甲带乙在院门停步。丙在茶馆候着。甲进入后院。篇章完。"
QUOTES = ["甲带乙在院门停步。", "丙在茶馆候着。", "甲进入后院。"]


def install(ops):
    sid = ops.import_source("场所与参与者", TEXT)
    nodes = [{"type": "person", "name": name, "quotes": [QUOTES[1] if name == "丙" else QUOTES[0]]} for name in "甲乙丙"]
    nodes += [{"type": "location", "name": name, "quotes": [QUOTES[index]]} for index, name in enumerate(["院门", "茶馆", "后院"])]
    nodes += [
        {"type": "big_event", "name": "会合经过", "quotes": [TEXT]},
        {"type": "scene", "name": "门前与茶馆", "big_event": "@7", "quotes": [QUOTES[0]+QUOTES[1]]},
        {"type": "scene", "name": "后院", "big_event": "@7", "quotes": [QUOTES[2]]},
        {"type": "event", "name": "门前停步", "scene": "@8", "participants": ["@1"], "quotes": [QUOTES[0]]},
        {"type": "event", "name": "茶馆等候", "scene": "@8", "participants": ["@3"], "quotes": [QUOTES[1]]},
        {"type": "event", "name": "走入后院", "scene": "@9", "participants": ["@1"], "quotes": [QUOTES[2]]},
        {"type": "action", "name": "同行停下", "event": "@10", "participants": ["@2"], "quotes": [QUOTES[0]]},
        {"type": "storyline", "name": "会合篇章", "closing_quote": "篇章完。", "children": ["@7"]},
    ]
    props = [{"node": f"@{10+i}", "property": "地点", "value": f"@{4+i}", "value_type": "node", "scope": "world", "at_event": f"@{10+i}", "quotes": [quote]} for i, quote in enumerate(QUOTES)]
    payload = {"nodes": nodes, "properties": props}
    result = ops.submit_extraction(sid, payload)
    assert result["status"] == "accepted", result
    return sid, result["node_ids"]


def ids(summary, key):
    return {row["id"] for row in summary["involvement"][key]}


def test_all_five_levels_aggregate_explicit_people_and_event_locations_without_writing(project):
    _sid, mapping = install(project)
    before = project.graph_snapshot(), project.property_records()
    for parent in ["@7", "@14"]:
        data = project.property_node_summary(mapping[parent])
        assert ids(data, "people") == {mapping[x] for x in ["@1", "@2", "@3"]}
        assert ids(data, "locations") == {mapping[x] for x in ["@4", "@5", "@6"]}
        person = next(row for row in data["involvement"]["people"] if row["id"] == mapping["@1"])
        assert person["event_ids"] == [mapping["@10"], mapping["@12"]]
    scene = project.property_node_summary(mapping["@8"])
    assert ids(scene, "locations") == {mapping["@4"], mapping["@5"]}
    event = project.property_node_summary(mapping["@10"])
    assert ids(event, "people") == {mapping["@1"], mapping["@2"]}  # parent event + child action
    assert ids(event, "locations") == {mapping["@4"]}
    action = project.property_node_summary(mapping["@13"])
    assert ids(action, "people") == {mapping["@2"]} and not ids(action, "locations")
    assert not any(issue["code"] == "property_on_event" for issue in audit_properties(before[1], before[0]["nodes"])["issues"])
    assert (project.graph_snapshot(), project.property_records()) == before


def test_cut_expiration_context_and_subjective_or_literal_references_do_not_leak(project):
    sid, mapping = install(project)
    early = project.property_node_summary(mapping["@7"], cut=mapping["@10"])
    assert ids(early, "people") == {mapping["@1"], mapping["@2"]}
    assert ids(early, "locations") == {mapping["@4"]}
    snapshot, records = project.graph_snapshot(), project.property_records()
    record = next(row for row in records if row.get("node_id") == mapping["@10"])
    altered = deepcopy(records)
    next(row for row in altered if row.get("id") == record["id"])["valid_to"] = {"source_id": sid, "start": TEXT.index(QUOTES[1])}
    assert not involvement(snapshot, altered, mapping["@10"])["locations"]
    assert involvement(snapshot, altered, mapping["@10"], mapping["@10"])["locations"]
    # Read projection must not turn a subjective location or a literal ID into occurrence membership.
    subjective = [dict(row, scope="cognition", holder_id=mapping["@1"]) if row.get("id") == record["id"] else row for row in records]
    assert not involvement(snapshot, subjective, mapping["@10"])["locations"]
    literal = [dict(row, value_type="string") if row.get("kind") == "property_definition" and row["id"] == "地点" else row for row in records]
    assert not involvement(snapshot, literal, mapping["@10"])["locations"]
    cross = deepcopy(snapshot)
    next(node for node in cross["nodes"] if node["id"] == mapping["@4"])["context_id"] = "other-context"
    assert not involvement(cross, records, mapping["@10"])["locations"]
    related = set(related_nodes(snapshot, mapping["@10"], records))
    assert {mapping["@1"], mapping["@2"], mapping["@4"]} <= related
    assert mapping["@5"] not in related and mapping["@3"] not in related


def test_actual_browse_container_membership_links_jump_to_referenced_node(tmp_path, monkeypatch):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        _sid, mapping = install(ops)
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "browse")
        page = ui.window.findChild(object, "browsePage")
        page.load()
        ui.wait(lambda: not ui.window.property("busy"))
        ui.settle()
        choose(ui, mapping["@7"])
        ui.wait(lambda: len(plain(page.property("involvement"))["locations"]) == 3)
        ui.wait(lambda: not ui.window.property("busy"))
        group = visual_find(ui.window.contentItem(), "browseInvolvementlocations")
        assert group.isVisible()
        links = [child for child in group.childItems() if child.objectName() == "browseInvolvementLink"]
        assert len(links) == 3
        # Scroll the existing detail pane so this is an actual visible click.
        scroll = ui.window.findChild(object, "browseDetailScroll")
        content = scroll.property("contentItem")
        content.setProperty("contentY", max(0, group.y()-40))
        ui.settle()
        target = next(link for link in links if mapping["@5"] in link.property("text"))
        click(ui, target)
        ui.wait(lambda: page.property("selectedId") == mapping["@5"])
        ui.wait(lambda: plain(page.property("detail")).get("node", {}).get("id") == mapping["@5"])


def test_future_member_does_not_leak_an_already_known_participant_or_location(project):
    _sid, mapping = install(project)
    snapshot, records = deepcopy(project.graph_snapshot()), project.property_records()
    for identifier in [mapping["@3"], mapping["@5"]]:
        next(row for row in snapshot["nodes"] if row["id"] == identifier)["disclosure"]["start"] = 0
    from narrative.browse_view import cut_for
    boundary = cut_for(snapshot, mapping["@10"])
    related = set(related_nodes(snapshot, mapping["@7"], records, cut=boundary))
    assert mapping["@3"] not in related and mapping["@5"] not in related
    aggregate = involvement(snapshot, records, mapping["@7"], boundary)
    assert mapping["@3"] not in {row["id"] for row in aggregate["people"]}
    assert mapping["@5"] not in {row["id"] for row in aggregate["locations"]}


def test_related_view_keeps_item_holder_one_hop_but_never_follows_person_dossier(project):
    from test_item_possession import SENTENCES, record, setup
    sid, names, item = setup(project)
    snapshot = project.graph_snapshot()
    event = dict(next(row for row in snapshot["nodes"] if row["id"] == names[SENTENCES[2]]), participants=[item["id"]])
    records = record(sid, names, names["甲"], event_index=1)
    records += [{"kind": "property_definition", "id": "朋友", "value_type": "node", "cardinality": "one"},
                {"kind": "property_value", "node_id": names["甲"], "property_id": "朋友", "value": names["乙"], "scope": "world",
                 "event_id": event["id"], "disclosure": event["disclosure"], "evidence": event["evidence"]}]
    result = project.apply_changes([item, event], records, snapshot["revision"], "一跳物品持有者读取fixture")
    assert result["status"] == "accepted", result
    related = set(related_nodes(project.graph_snapshot(), event["id"], project.property_records()))
    assert item["id"] in related and names["甲"] in related
    assert names["乙"] not in related


def test_event_with_later_action_uses_the_same_actual_cut_in_browse_and_membership(project):
    from test_granularity_actions import FIRST, SECOND, action_batch

    from narrative.browse_view import cut_for
    sid, _obj, objects = action_batch(project)
    event = objects[0]
    event["evidence"] = {"source_id": sid, "quote": FIRST}
    event["evidence_spans"] = [{"source_id": sid, "start": 0, "end": len(FIRST), "quote": FIRST}]
    person = deepcopy(next(row for row in project.graph_snapshot()["nodes"] if row["id"] == "P1"))
    person.update(id="P2", name="乙", disclosure={"source_id":sid,"start":len(FIRST)},
                  evidence={"source_id":sid,"quote":SECOND}, evidence_spans=[{"source_id":sid,"start":len(FIRST),"end":len(FIRST)+len(SECOND),"quote":SECOND}])
    objects[-1]["participants"] = ["P2"]
    assert project.apply_changes([*objects, person], [], project.graph_project_info()["revision"], "事件自身早于后续动作的读取fixture")["status"] == "accepted"
    snapshot = project.graph_snapshot()
    boundary = cut_for(snapshot, "E9")
    assert boundary["event_id"] == "A1"
    view = project.browse_view(cut_id="E9", related_only=True)
    assert "P2" in {node["id"] for node in view["nodes"]}
    membership = project.property_node_summary("E9",cut="E9")["involvement"]
    assert "P2" in {row["id"] for row in membership["people"]}


def test_event_without_active_action_uses_own_boundary_without_recursion(project):
    from test_granularity_actions import FIRST, action_batch

    from narrative.temporal_state import boundary_at
    _sid, _obj, objects = action_batch(project)
    assert project.apply_changes(objects, [], project.graph_project_info()["revision"], "空动作边界fixture")["status"] == "accepted"
    snapshot = deepcopy(project.graph_snapshot())
    event = next(row for row in snapshot["nodes"] if row["id"] == "E9")
    event["evidence_spans"] = [dict(event["evidence_spans"][0], end=len(FIRST), quote=FIRST)]
    for row in snapshot["nodes"]:
        if row["node_type"] == "action":
            row["retracted"] = True
    assert boundary_at(snapshot, "E9")["start"] == len(FIRST)-1
    event["events"] = []
    assert boundary_at(snapshot, "E9")["start"] == len(FIRST)-1
