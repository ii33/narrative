"""Black-box Operations checks for typed IDs, draft slots and literal boundaries."""
from __future__ import annotations

import copy
import json

import pytest
from agent_fixtures import project as project


def values(ops):
    return [row for row in ops.property_records() if row["kind"] == "property_change"]


def submit_unchanged_input(ops, source_id, payload):
    original=copy.deepcopy(payload)
    checked=ops.preview_extraction(source_id,payload)
    assert checked["valid"], checked
    assert "node_ids" not in checked
    assert payload == original
    result=ops.submit_extraction(source_id,payload)
    assert result["status"] == "accepted", result
    assert payload == original
    return result


def test_identical_names_of_different_types_are_bound_to_the_exact_array_slot(project):
    text="名叫渡口的人在渡口等候。"
    sid=project.import_source("同名跨类型",text)
    payload={"nodes":[
        {"type":"person","name":"渡口","quotes":[text]},
        {"type":"location","name":"渡口","quotes":[text]},
        {"type":"scene","name":"渡口等候","quotes":[text]},
        {"type":"event","name":"在渡口等候","scene":"@3","participants":["@1"],"quotes":[text]},
    ],"properties":[{"node":"@1","op":"set","property":"位置","value":"@2","value_type":"node",
                      "at_event":"@4","related_nodes":["@2"],"quotes":[text]}]}
    submit_unchanged_input(project,sid,payload)
    nodes=project.graph_snapshot()["nodes"]
    person=next(row for row in nodes if row["node_type"] == "person")
    location=next(row for row in nodes if row["node_type"] == "location")
    event=next(row for row in nodes if row["node_type"] == "event")
    assert person["name"] == location["name"] == "渡口"
    assert person["id"] != location["id"]
    assert event["participants"] == [person["id"]]
    record=values(project)[0]
    assert (record["node_id"],record["value"],record["event_id"]) == (person["id"],location["id"],event["id"])
    assert record["related_nodes"] == [location["id"]]


def test_identical_event_names_at_different_occurrences_keep_exact_at_event_slots(project):
    first,second="甲上午进门。","甲下午进门。"
    sid=project.import_source("两次进门",first+second)
    payload={"nodes":[
        {"type":"person","name":"甲","quotes":[first]},
        {"type":"scene","name":"两次进门","quotes":[first,second]},
        {"type":"event","name":"进门","scene":"@2","participants":["@1"],"quotes":[first]},
        {"type":"event","name":"进门","scene":"@2","participants":["@1"],"new_occurrence":True,"quotes":[second]},
    ],"properties":[
        {"node":"@1","op":"set","property":"进门时段","value":"上午","at_event":"@3","quotes":[first]},
        {"node":"@1","op":"set","property":"进门时段","value":"下午","at_event":"@4","quotes":[second]},
    ]}
    submit_unchanged_input(project,sid,payload)
    nodes=project.graph_snapshot()["nodes"]
    events=[row for row in nodes if row["node_type"] == "event"]
    assert len(events) == 2 and all(row["name"] == "进门" for row in events)
    by_start={row["disclosure"]["start"]:row["id"] for row in events}
    records={row["value"]:row for row in values(project)}
    assert records["上午"]["event_id"] == by_start[0]
    assert records["下午"]["event_id"] == by_start[len(first)]
    scene=next(row for row in nodes if row["node_type"] == "scene")
    assert set(scene["events"]) == set(by_start.values())


def seed(project):
    text="甲站在门口。"
    sid=project.import_source("已有人物",text)
    draft={"nodes":[
        {"type":"person","name":"甲","quotes":[text]},
        {"type":"scene","name":"门口等候","quotes":[text]},
        {"type":"event","name":"甲站在门口","scene":"@2","participants":["@1"],"quotes":[text]},
    ],"properties":[]}
    submit_unchanged_input(project,sid,draft)
    nodes=project.graph_snapshot()["nodes"]
    return sid,text,next(row["id"] for row in nodes if row["node_type"] == "person"),next(row["id"] for row in nodes if row["node_type"] == "event")


def test_string_and_any_reference_looking_literals_and_original_payload_are_not_rewritten(project):
    text="甲说@1和P1只是文字。"
    sid=project.import_source("字面值",text)
    literal={"node":"@1","event_id":"P1","nested":["@1","P1",{"holder":"@1"}]}
    payload={"nodes":[
        {"type":"person","name":"甲","quotes":[text]},
        {"type":"scene","name":"字符说明","quotes":[text]},
        {"type":"event","name":"甲说明字符","scene":"@2","participants":["@1"],"quotes":[text]},
    ],"properties":[
        {"node":"@1","op":"set","property":"字面文本","value":"@1","value_type":"string","at_event":"@3","quotes":[text]},
        {"node":"@1","op":"set","property":"字面编号","value":"P1","value_type":"string","at_event":"@3","quotes":[text]},
        {"node":"@1","op":"set","property":"原始结构","value":literal,"value_type":"any","at_event":"@3","quotes":[text]},
    ]}
    submit_unchanged_input(project,sid,payload)
    records={row["property_id"]:row for row in values(project)}
    assert records["字面文本"]["value"] == "@1"
    assert records["字面编号"]["value"] == "P1"
    assert records["原始结构"]["value"] == literal


@pytest.mark.parametrize("invalid",["E404","@99","不存在的事件"])
def test_explicit_invalid_at_event_never_falls_back_to_the_quotation(project,invalid):
    sid,text,person,_event=seed(project)
    before=copy.deepcopy((project.graph_snapshot(),project.property_records()))
    payload={"nodes":[],"properties":[{"node":person,"op":"set","property":"位置","value":"门口","at_event":invalid,"quotes":[text]}]}
    original=copy.deepcopy(payload)
    checked=project.preview_extraction(sid,payload)
    assert not checked["valid"], checked
    assert invalid in json.dumps(checked,ensure_ascii=False)
    assert project.submit_extraction(sid,payload)["status"] == "rejected"
    assert payload == original
    assert (project.graph_snapshot(),project.property_records()) == before


def test_empty_nodes_can_add_existing_id_property_without_repeating_any_node(project):
    sid,text,person,event=seed(project)
    before=copy.deepcopy(project.graph_snapshot()["nodes"])
    payload={"nodes":[],"properties":[{"node":person,"op":"set","property":"位置","value":"门口","at_event":event,"quotes":[text]}]}
    committed=submit_unchanged_input(project,sid,payload)
    assert "node_ids" not in committed
    assert project.graph_snapshot()["nodes"] == before
    assert len(values(project)) == 1
    assert values(project)[0]["node_id"] == person and values(project)[0]["event_id"] == event


def test_repeated_same_identity_slots_deduplicate_to_one_formal_node(project):
    text="甲站在门口。"
    sid=project.import_source("重复声明同一人",text)
    declaration={"type":"person","name":"甲","quotes":[text]}
    payload={"nodes":[
        copy.deepcopy(declaration),copy.deepcopy(declaration),
        {"type":"scene","name":"门口等候","quotes":[text]},
        {"type":"event","name":"甲站在门口","scene":"@3","participants":["@1","@2"],"quotes":[text]},
    ],"properties":[
        {"node":"@1","op":"set","property":"位置","value":"门口","at_event":"@4","quotes":[text]},
        {"node":"@2","op":"set","property":"姿态","value":"站着","at_event":"@4","quotes":[text]},
    ]}
    committed=submit_unchanged_input(project,sid,payload)
    persons=[row for row in project.graph_snapshot()["nodes"] if row["node_type"] == "person"]
    assert len(persons) == 1
    assert committed["node_ids"]["@1"] == committed["node_ids"]["@2"] == persons[0]["id"]
    before=copy.deepcopy((project.graph_snapshot(),project.property_records()))
    original=copy.deepcopy(payload)
    repeated=project.submit_extraction(sid,payload)
    assert repeated["status"] == "unchanged"
    assert repeated["node_ids"] == committed["node_ids"]
    assert payload == original
    assert (project.graph_snapshot(),project.property_records()) == before
    records=values(project)
    assert len(records) == 2 and {row["property_id"] for row in records} == {"位置","姿态"}
    assert {row["node_id"] for row in records} == {persons[0]["id"]}


def test_clear_event_preserves_prior_literal_batch_reference_text(project):
    first, second = '甲说@1', '随后停止标注'
    text = first+'，'+second+'。'
    sid = project.import_source('字面标记结束', text)
    payload = {'nodes': [
        {'type': 'person', 'name': '甲', 'quotes': [first]},
        {'type': 'scene', 'name': '记录后停止', 'quotes': [text]},
        {'type': 'event', 'name': '甲记录标注', 'scene': '@2', 'participants': ['@1'], 'quotes': [first]},
        {'type': 'event', 'name': '停止标注', 'scene': '@2', 'participants': ['@1'], 'quotes': [second]}],
        'properties': [
            {'node': '@1', 'property': '标注', 'op': 'set', 'value': '@1', 'value_type': 'string', 'at_event': '@3', 'quotes': [first]},
            {'node': '@1', 'property': '标注', 'op': 'clear', 'at_event': '@4', 'quotes': [second]}]}
    submit_unchanged_input(project, sid, payload)
    nodes = project.graph_snapshot()['nodes']
    person = next(n['id'] for n in nodes if n['node_type'] == 'person')
    events = {n['name']: n['id'] for n in nodes if n['node_type'] == 'event'}
    assert next(r for r in values(project) if r['op'] == 'set')['value'] == '@1'
    assert project.property_state(person, '标注', events['甲记录标注'])['state']['value'] == '@1'
    assert project.property_state(person, '标注')['state'] == {'kind': 'absent'}
