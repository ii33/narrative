"""Chapter extraction: quote verification and candidate assembly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from narrative.extraction import build_candidate, build_prompt, known_properties, parse_nodes
from narrative.project_catalog import create_graph_project

TEXT = (
    "林舟走进站前旅社三零二房间。\n"
    "他看见陈默和女友在一起，抽出水果刀刺了过去。\n"
    "老周后来告诉他，火纹石已经改过他的外形。\n"
)

RAW = """
{"nodes": [
  {"name": "林舟", "type": "person", "quotes": ["林舟走进站前旅社三零二房间"], "aliases": ["林总"]},
  {"name": "陈默", "type": "person", "quotes": ["他看见陈默和女友在一起"], "aliases": []},
  {"name": "水果刀", "type": "item", "quotes": ["抽出水果刀刺了过去"], "aliases": []},
  {"name": "站前旅社三零二房间", "type": "location", "quotes": ["林舟走进站前旅社三零二房间"], "aliases": []},
  {"name": "房间冲突", "type": "scene", "quotes": ["他看见陈默和女友在一起"], "aliases": []},
  {"name": "林舟刺伤陈默", "type": "event", "quotes": ["抽出水果刀刺了过去"],
   "aliases": [], "participants": ["林舟", "陈默", "水果刀"], "scene": "房间冲突"},
  {"name": "火纹石改造外形", "type": "event", "quotes": ["火纹石已经改过他的外形"],
   "aliases": [], "participants": ["老周"], "scene": "房间冲突"}
]}
"""


def test_parse_keeps_verified_quotes_and_orders_by_position() -> None:
    result = parse_nodes(RAW, TEXT)
    assert result["counts"] == {
        "person": 2, "group": 0, "item": 1, "location": 1, "scene": 1, "event": 2,
        "storyline": 0,
    }
    assert [item["name"] for item in result["grouped"]["event"]] == ["林舟刺伤陈默", "火纹石改造外形"]
    assert all(item["quotes"] for item in result["nodes"])
    assert result["rejected"] == []


def test_parse_drops_quotes_that_are_not_in_the_source() -> None:
    raw = '{"nodes": [{"name": "林舟", "type": "person", "quotes": ["这句话原文没有"]}]}'
    result = parse_nodes(raw, TEXT)
    assert result["nodes"] == []
    assert result["rejected"][0]["reason"] == "引文不在原文中"


def test_parse_tolerates_a_wrapping_quote_the_model_added() -> None:
    """模型常把整句再套一层引号：多出来的是排版，不是内容。"""
    raw = """
    {"nodes": [
      {"name": "老周", "type": "person",
       "quotes": ["“老周后来告诉他，火纹石已经改过他的外形。”"]}
    ], "properties": []}
    """
    result = parse_nodes(raw, TEXT)
    assert [node["name"] for node in result["nodes"]] == ["老周"]
    # 存下来的引文是去掉外层引号之后的那段，仍然是原文的子串。
    assert result["nodes"][0]["quotes"] == ["老周后来告诉他，火纹石已经改过他的外形。"]
    assert result["nodes"][0]["quotes"][0] in TEXT
    assert result["rejected"] == []


def test_parse_still_drops_a_quote_that_only_rhymes() -> None:
    """去掉首尾引号之后还是对不上，就还是拒收。"""
    raw = """
    {"nodes": [
      {"name": "陈默", "type": "person", "quotes": ["“陈默和女友一起离开了”"]}
    ], "properties": []}
    """
    result = parse_nodes(raw, TEXT)
    assert result["nodes"] == []
    assert result["rejected"][0]["reason"] in {"引文不在原文中", "没有可用的原文引文"}


def test_parse_rejects_unknown_types() -> None:
    raw = '{"nodes": [{"name": "火纹石", "type": "artifact", "quotes": ["火纹石已经改过他的外形"]}]}'
    result = parse_nodes(raw, TEXT)
    assert result["nodes"] == []
    assert "类型无效" in result["rejected"][0]["reason"]


def _project(tmp_path: Path):
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", TEXT)
    return ops, source_id, ops.source(source_id)["hash"]


def test_build_candidate_wires_participants_and_scene_children(tmp_path: Path) -> None:
    ops, source_id, source_hash = _project(tmp_path)
    data = parse_nodes(RAW, TEXT) | {"source_id": source_id, "source_hash": source_hash}

    built = build_candidate(ops, data)
    assert built["errors"] == []

    candidate = ops.change_propose(
        built["objects"], built["property_records"], built["revision"], source="model"
    )
    ops.change_accept(candidate, 1)

    snapshot = ops.graph_snapshot()
    nodes = {item["name"]: item for item in snapshot["nodes"]}
    event = nodes["林舟刺伤陈默"]
    assert event["participants"] == [
        nodes["林舟"]["id"], nodes["陈默"]["id"], nodes["水果刀"]["id"]
    ]
    # The scene is a composite event: its children are a field, not an assertion.
    scene = nodes["房间冲突"]
    assert set(scene["events"]) == {
        nodes["林舟刺伤陈默"]["id"], nodes["火纹石改造外形"]["id"]
    }
    # 断言层已经没了：场景的子事件只是字段，不会额外生成一条关系。
    assert "assertions" not in snapshot


def test_second_run_only_submits_what_is_new(tmp_path: Path) -> None:
    """Re-extraction must not rewrite reviewed nodes; it only adds new ones."""
    ops, source_id, source_hash = _project(tmp_path)
    data = parse_nodes(RAW, TEXT) | {"source_id": source_id, "source_hash": source_hash}

    first = build_candidate(ops, data)
    ops.change_accept(
        ops.change_propose(
            first["objects"], first["property_records"], first["revision"], source="model"
        ),
        1,
    )
    second = build_candidate(ops, data)

    assert second["empty"] is True
    assert second["objects"] == []
    assert second["property_records"] == []


PROP_TEXT = "林舟今年二十九岁，脸色黝黑。\n后来火纹石把他的皮肤变得白净细嫩。\n"

PROP_RAW = """
{"nodes": [
  {"name": "林舟", "type": "person", "quotes": ["林舟今年二十九岁"]},
  {"name": "火纹石", "type": "item", "quotes": ["火纹石把他的皮肤变得白净细嫩"]},
  {"name": "林舟出场", "type": "event", "quotes": ["林舟今年二十九岁，脸色黝黑"],
   "participants": ["林舟"], "after": ""},
  {"name": "火纹石改造皮肤", "type": "event", "quotes": ["火纹石把他的皮肤变得白净细嫩"],
   "participants": ["林舟", "火纹石"], "after": "林舟出场"}
],
 "properties": [
  {"node": "林舟", "property": "age", "value": 29, "value_type": "number",
   "quotes": ["林舟今年二十九岁"], "scope": "world"},
  {"node": "林舟", "property": "skin", "value": "黝黑", "value_type": "string",
   "quotes": ["脸色黝黑"], "scope": "world"},
  {"node": "林舟", "property": "skin", "value": "白净细嫩", "value_type": "string",
   "quotes": ["火纹石把他的皮肤变得白净细嫩"], "scope": "world"}
]}
"""


def test_parse_properties_keeps_order_and_rejects_bad_quotes() -> None:
    result = parse_nodes(PROP_RAW, PROP_TEXT)
    assert [item["property"] for item in result["properties"]] == ["age", "skin", "skin"]
    assert result["properties"][0]["value"] == 29

    broken = ('{"nodes": [{"name": "林舟", "type": "person", "quotes": ["林舟今年二十九岁"]}], '
              '"properties": [{"node": "林舟", "property": "age", "value": 29, "quotes": ["原文没有"]}]}')
    rejected = parse_nodes(broken, PROP_TEXT)
    assert rejected["properties"] == []
    assert rejected["rejected"][0]["reason"] == "引文不在原文中"


def test_properties_form_one_value_sequence(tmp_path: Path) -> None:
    """Each entry is (value, event); the value at an event is the deepest match."""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", PROP_TEXT)
    data = parse_nodes(PROP_RAW, PROP_TEXT) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }

    built = build_candidate(ops, data)
    assert built["errors"] == []
    assert built["property_count"] == 3
    kinds = {item["kind"] for item in built["property_records"]}
    # 同一方面出现多个值时，候选里同时带着「旧值失效」的标记：值和取代它的
    # 披露一起落地，不会出现「新值进了、旧值还站着」的半截状态。
    assert kinds <= {"property_definition", "property_value", "property_retraction"}
    marks = [item for item in built["property_records"] if item["kind"] == "property_retraction"]
    assert all(mark["basis"] == "audit" and mark["event_id"] for mark in marks)

    candidate = ops.change_propose(
        built["objects"], built["property_records"], built["revision"], source="model"
    )
    ops.change_accept(candidate, 1)

    nodes = {item["name"]: item["id"] for item in ops.graph_snapshot()["nodes"]}
    liqiang = nodes["林舟"]
    # Each value is visible from the checkpoint where the reader learns it.
    assert ops.property_event_state(liqiang, "age", nodes["林舟出场"])["value"] == 29
    assert ops.property_event_state(liqiang, "skin", nodes["林舟出场"])["value"] == "黝黑"
    assert (
        ops.property_event_state(liqiang, "skin", nodes["火纹石改造皮肤"])["value"]
        == "白净细嫩"
    )
    # Before the first checkpoint nothing has been disclosed yet.
    assert ops.property_event_state(liqiang, "age", "nope")["kind"] == "unrecorded"


ANCHOR_TEXT = (
    "林舟走进房间。\n"
    "老周用火纹石改造林舟身体，皮肤变得白净。\n"
)

ANCHOR_RAW = """
{"nodes": [
  {"name": "林舟", "type": "person", "quotes": ["林舟走进房间"]},
  {"name": "老周", "type": "person", "quotes": ["老周用火纹石改造林舟身体"]},
  {"name": "火纹石", "type": "item", "quotes": ["火纹石改造林舟身体"]},
  {"name": "林舟走进房间", "type": "event", "quotes": ["林舟走进房间"],
   "participants": ["林舟"], "scene": ""},
  {"name": "老周改造林舟身体", "type": "event", "quotes": ["老周用火纹石改造林舟身体"],
   "participants": ["老周", "林舟", "火纹石"], "scene": "",
   "after": "林舟走进房间"}
],
 "properties": [
  {"node": "林舟", "property": "skin", "value": "黝黑", "value_type": "string",
   "quotes": ["林舟走进房间"]},
  {"node": "林舟", "property": "skin", "value": "白净", "value_type": "string",
   "quotes": ["皮肤变得白净"], "at_event": "老周改造林舟身体"}
]}
"""


def test_property_changes_hang_on_their_event(tmp_path: Path) -> None:
    """A change is a new value on the event that caused it, not a mutation."""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", ANCHOR_TEXT)
    data = parse_nodes(ANCHOR_RAW, ANCHOR_TEXT) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }

    built = build_candidate(ops, data)
    assert built["errors"] == []
    values = [item for item in built["property_records"] if item["kind"] == "property_value"]
    assert len(values) == 2

    candidate = ops.change_propose(
        built["objects"], built["property_records"], built["revision"], source="model"
    )
    ops.change_accept(candidate, 1)

    nodes = {item["name"]: item["id"] for item in ops.graph_snapshot()["nodes"]}
    liqiang = nodes["林舟"]
    before = ops.property_event_state(liqiang, "skin", nodes["林舟走进房间"])
    after = ops.property_event_state(liqiang, "skin", nodes["老周改造林舟身体"])
    assert before["value"] == "黝黑"
    # No explicit at_event: the value hangs on the checkpoint covering it.
    assert before["event_id"] == nodes["林舟走进房间"]
    assert after["value"] == "白净"
    assert after["event_id"] == nodes["老周改造林舟身体"]


def test_events_compare_by_disclosure_and_story_stays_unknown(tmp_path: Path) -> None:
    """Events are already ordered by the chain; comparison walks it."""
    ops = create_graph_project(tmp_path / "graph.nproj")
    first = ops.import_source("第一章", "甲出场了。")
    second = ops.import_source("第二章", "乙出场了。")
    candidate = ops.change_propose(
        [
            {"kind": "source_order", "id": "o1", "source_id": first, "ordinal": 1, "context_id": "canon"},
            {"kind": "source_order", "id": "o2", "source_id": second, "ordinal": 2, "context_id": "canon"},
            # Ids are the disclosure order, so the fixture uses the same numbering
            # rule as real extraction: 1 in the first chapter, 2 in the second.
            {"kind": "node", "id": "1", "node_type": "event", "name": "甲出场",
             "disclosure": {"source_id": first, "start": 0}, "successors": ["2"]},
            {"kind": "node", "id": "2", "node_type": "event", "name": "乙出场",
             "disclosure": {"source_id": second, "start": 0}, "predecessors": ["1"]},
        ],
        [], ops.graph_project_info()["revision"], source="manual",
    )
    ops.change_accept(candidate, 1)

    forward = ops.graph_event_compare("1", "2")
    assert forward["relation"] == "before"
    assert forward["basis"] == "disclosure"
    assert forward["axis"] == "disclosure"
    backward = ops.graph_event_compare("2", "1")
    assert backward["relation"] == "after"
    assert backward["basis"] == "disclosure"

    # 故事轴没有数据来源：提取不产出事件先后，`predecessors` 只是记录，不参与比较。
    story = ops.graph_event_compare("1", "2", axis="story")
    assert story["relation"] == "unknown"
    assert story["basis"] == "none"
    assert "不产出事件间关系" in story["reason"]


def test_nodes_carry_the_sentence_they_came_from(tmp_path: Path) -> None:
    """A node is checkable only if its quote survives into the graph."""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", ANCHOR_TEXT)
    data = parse_nodes(ANCHOR_RAW, ANCHOR_TEXT) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    candidate = ops.change_propose(
        built["objects"], built["property_records"], built["revision"], source="model"
    )
    ops.change_accept(candidate, 1)

    nodes = {item["name"]: item for item in ops.graph_snapshot()["nodes"]}
    event = nodes["老周改造林舟身体"]
    assert event["evidence"]["source_id"] == source_id
    assert event["evidence"]["quote"] in ANCHOR_TEXT
    assert event["disclosure"]["start"] == ANCHOR_TEXT.index(event["evidence"]["quote"])


def test_same_name_events_stay_separate(tmp_path: Path) -> None:
    """Two deeds may share a name; the sentence is what tells them apart."""
    text = "林舟在房间修炼。\n三天后，林舟在洞府修炼。\n"
    raw = """
{"nodes": [
  {"name": "林舟", "type": "person", "quotes": ["林舟在房间修炼"]},
  {"name": "林舟修炼", "type": "event", "quotes": ["林舟在房间修炼"], "participants": ["林舟"]},
  {"name": "林舟修炼", "type": "event", "quotes": ["林舟在洞府修炼"], "participants": ["林舟"]}
]}
"""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", text)
    data = parse_nodes(raw, text) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    assert built["errors"] == []
    events = [item for item in built["objects"] if item.get("node_type") == "event"]
    assert len(events) == 2
    assert len({item["id"] for item in events}) == 2
    assert {item["evidence"]["quote"] for item in events} == {"林舟在房间修炼", "林舟在洞府修炼"}


def test_late_disclosure_is_not_pulled_back_to_the_start(tmp_path: Path) -> None:
    """A value learned in chapter three must not be visible at chapter one's start."""
    text = "林舟走进房间。\n他今年二十九岁。\n"
    raw = """
{"nodes": [
  {"name": "林舟", "type": "person", "quotes": ["林舟走进房间"]},
  {"name": "林舟走进房间", "type": "event", "quotes": ["林舟走进房间"], "participants": ["林舟"]},
  {"name": "提到年龄", "type": "event", "quotes": ["他今年二十九岁"], "participants": ["林舟"]}
],
 "properties": [
  {"node": "林舟", "property": "age", "value": 29, "value_type": "number",
   "quotes": ["他今年二十九岁"]}
]}
"""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", text)
    data = parse_nodes(raw, text) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    assert built["errors"] == []
    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )
    nodes = {item["name"]: item["id"] for item in ops.graph_snapshot()["nodes"]}
    # Anchored to the event that discloses it, not to the chapter's first event.
    assert ops.property_event_state(nodes["林舟"], "age", nodes["提到年龄"])["value"] == 29
    assert (
        ops.property_event_state(nodes["林舟"], "age", nodes["林舟走进房间"])["kind"]
        == "unrecorded"
    )


def test_text_after_the_last_event_still_gets_an_anchor(tmp_path: Path) -> None:
    """A note at the end of a chapter belongs to that chapter's last event."""
    text = "林舟走进房间。\n老周离开了。\n章末补记：林舟今年二十九岁。\n"
    raw = """
{"nodes": [
  {"name": "林舟", "type": "person", "quotes": ["林舟走进房间"]},
  {"name": "林舟走进房间", "type": "event", "quotes": ["林舟走进房间"], "participants": ["林舟"]},
  {"name": "老周离开", "type": "event", "quotes": ["老周离开了"], "participants": ["林舟"]}
],
 "properties": [
  {"node": "林舟", "property": "age", "value": 29, "value_type": "number",
   "quotes": ["林舟今年二十九岁"]}
]}
"""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", text)
    data = parse_nodes(raw, text) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    assert built["errors"] == []
    # Nothing is dropped for lack of a following event.
    assert built["skipped"] == []
    assert built["property_count"] == 1
    values = [item for item in built["property_records"] if item["kind"] == "property_value"]
    events = {
        item["name"]: item["id"] for item in built["objects"] if item.get("node_type") == "event"
    }
    assert values[0]["event_id"] == events["老周离开"]
    assert values[0]["anchor_basis"] == "nearest"


def test_story_axis_refuses_to_borrow_the_disclosure_anchor(tmp_path: Path) -> None:
    """A disclosure anchor does not say the value holds at that story event."""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", PROP_TEXT)
    data = parse_nodes(PROP_RAW, PROP_TEXT) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    candidate = ops.change_propose(
        built["objects"], built["property_records"], built["revision"], source="model"
    )
    ops.change_accept(candidate, 1)

    nodes = {item["name"]: item["id"] for item in ops.graph_snapshot()["nodes"]}
    liqiang = nodes["林舟"]
    # Disclosure axis answers; story axis says the link is not established yet.
    assert ops.property_event_state(liqiang, "age", nodes["林舟出场"])["value"] == 29
    story = ops.property_event_state(liqiang, "age", nodes["林舟出场"], axis="story")
    assert story["kind"] == "unlinked"
    assert "逻辑事件" in story["reason"]


def test_each_node_type_has_its_own_numbering(tmp_path: Path) -> None:
    """每种节点各自一套编号：人是 P1、P2，物品是 I1，两个 1 互不相关。"""
    from narrative.order_ids import prefix_for, split_id

    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", TEXT)
    data = parse_nodes(RAW, TEXT) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    nodes = [item for item in built["objects"] if item.get("node_type")]

    by_type: dict[str, list[tuple[int, ...]]] = {}
    for item in nodes:
        parsed = split_id(item["id"])
        assert parsed is not None
        prefix, segments = parsed
        assert prefix == prefix_for(item["node_type"])
        by_type.setdefault(item["node_type"], []).append(segments)

    # 每个类型都从 1 开始、连续发号，别人的编号不影响它。
    for segments in by_type.values():
        assert sorted(segments) == [(number,) for number in range(1, len(segments) + 1)]
    assert by_type["person"] == [(1,), (2,)]
    assert by_type["item"] == [(1,)]

    # 前缀让编号在工程里仍然唯一，但数字本身没有跨类型的意思。
    ids = [item["id"] for item in nodes]
    assert len(set(ids)) == len(ids)
    assert "P1" in ids and "I1" in ids

    ops.change_accept(
        ops.change_propose(built["objects"], [], built["revision"], source="model"), 1
    )
    stored = {item["name"]: item for item in ops.graph_snapshot()["nodes"] if item.get("disclosure")}
    # 同一个数字在两类里各算一次，谁先谁后只能看披露位置。
    assert split_id(stored["林舟"]["id"])[1] == split_id(stored["水果刀"]["id"])[1] == (1,)
    assert stored["林舟"]["disclosure"]["start"] < stored["水果刀"]["disclosure"]["start"]

    # Renaming keeps the id: it identifies the node, not the label.
    before = stored["林舟"]
    revised = dict(before)
    revised["name"] = "林舟（改）"
    ops.change_accept(
        ops.change_propose([revised], [], ops.graph_project_info()["revision"], source="correction"), 1
    )
    after = next(
        item for item in ops.graph_snapshot()["nodes"] if item["name"] == "林舟（改）"
    )
    assert before["id"] == after["id"]


def test_inserted_mention_sorts_between_neighbours(tmp_path: Path) -> None:
    """A late find gets a sub-segment instead of renumbering the neighbours."""
    from narrative.order_ids import order_key

    text = "林舟走进房间。\n老周出现了。\n"
    raw = """
{"nodes": [
  {"name": "林舟", "type": "person", "quotes": ["林舟走进房间"]},
  {"name": "林舟走进房间", "type": "event", "quotes": ["林舟走进房间"], "participants": ["林舟"]},
  {"name": "老周出现", "type": "event", "quotes": ["老周出现了"], "participants": ["林舟"]}
]}
"""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", text)
    data = parse_nodes(raw, text) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    ops.change_accept(
        ops.change_propose(built["objects"], [], built["revision"], source="model"), 1
    )
    existing = {item["name"]: item["id"] for item in ops.graph_snapshot()["nodes"]}

    # A second extraction finds something between the two events.
    late = {
        "nodes": [
            {"name": "门外有人敲门", "type": "event", "quotes": ["老周出现了"],
             "participants": ["林舟"], "aliases": []},
        ],
        "properties": [],
        "source_id": source_id,
        "source_hash": ops.source(source_id)["hash"],
    }
    late["nodes"][0]["start"] = text.index("老周出现了") - 1
    second = build_candidate(ops, late)
    new_id = next(
        item["id"] for item in second["objects"] if item.get("node_type") == "event"
    )
    assert order_key(existing["林舟走进房间"]) < order_key(new_id) < order_key(existing["老周出现"])


def test_property_retraction_hides_the_record_from_reads(tmp_path: Path) -> None:
    """Retracting keeps the row for audit but removes it from every projection."""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", PROP_TEXT)
    data = parse_nodes(PROP_RAW, PROP_TEXT) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )
    target = next(
        item for item in ops.property_records()
        if item["kind"] == "property_value" and item["property_id"] == "skin"
        and item["value"] == "黝黑"
    )

    ops.change_accept(
        ops.change_propose(
            [],
            [{"kind": "property_retraction", "id": "ret_1",
              "target_id": target["id"], "reason": "测试撤回"}],
            ops.graph_project_info()["revision"],
            source="correction",
        ),
        1,
    )

    visible = [item for item in ops.property_records() if item["kind"] == "property_value"]
    assert all(item["id"] != target["id"] for item in visible)
    audited = [
        item for item in ops.property_records(include_retracted=True)
        if item["kind"] == "property_value"
    ]
    assert any(item["id"] == target["id"] for item in audited)

    nodes = {item["name"]: item["id"] for item in ops.graph_snapshot()["nodes"]}
    # The retracted value must not come back through a query either.
    assert (
        ops.property_event_state(nodes["林舟"], "skin", nodes["林舟出场"])["kind"]
        == "unrecorded"
    )


def test_value_shape_mismatch_is_a_warning_not_a_block(tmp_path: Path) -> None:
    """A property may hold 180 and "矮小"; the declared type is a hint."""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", PROP_TEXT)
    data = parse_nodes(PROP_RAW, PROP_TEXT) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )
    nodes = {item["name"]: item["id"] for item in ops.graph_snapshot()["nodes"]}
    liqiang = nodes["林舟"]

    # Declare height as string, then record a number: the type is a hint.
    definition = {"kind": "property_definition", "id": "height", "value_type": "string",
                  "cardinality": "one"}
    revision = ops.graph_project_info()["revision"]
    ops.change_accept(
        ops.change_propose([], [definition], revision, source="correction"), 1
    )

    numeric = {
        "kind": "property_value", "id": "val_height_180", "node_id": liqiang,
        "property_id": "height", "value": 180, "event_id": nodes["林舟出场"],
        "disclosure": {"source_id": source_id, "start": PROP_TEXT.index("林舟今年")},
        "evidence": {"source_id": source_id, "quote": "林舟今年二十九岁"},
    }
    revision = ops.graph_project_info()["revision"]
    # Shape is not policed at all: 180 and "矮小" are both valid values.
    assert ops.change_precheck([], [numeric], revision) == []

    ops.change_accept(ops.change_propose([], [numeric], revision, source="correction"), 1)
    values = [
        item["value"] for item in ops.property_records()
        if item["kind"] == "property_value" and item["property_id"] == "height"
    ]
    assert 180 in values
    assert "变高了" not in values  # this fixture never recorded that


def test_world_and_cognition_values_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """Same property, different scope: two records, not one."""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", PROP_TEXT)
    data = parse_nodes(PROP_RAW, PROP_TEXT) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )
    nodes = {item["name"]: item["id"] for item in ops.graph_snapshot()["nodes"]}
    event = nodes["林舟出场"]
    disclosure = {"source_id": source_id, "start": PROP_TEXT.index("林舟今年")}

    world = {
        "kind": "property_value", "id": "val_age_world", "node_id": nodes["林舟"],
        "property_id": "age", "value": 29, "event_id": event,
        "disclosure": disclosure,
        "evidence": {"source_id": source_id, "quote": "林舟今年二十九岁"},
    }
    cognition = {
        "kind": "property_value", "id": "val_age_cognition", "node_id": nodes["林舟"],
        "property_id": "age", "value": 30, "event_id": event,
        "scope": "cognition", "holder_id": nodes["林舟"],
        "disclosure": disclosure,
        "evidence": {"source_id": source_id, "quote": "林舟今年二十九岁"},
    }
    revision = ops.graph_project_info()["revision"]
    assert ops.change_precheck([], [world, cognition], revision) == []
    ops.change_accept(ops.change_propose([], [world, cognition], revision, source="correction"), 1)

    assert ops.property_event_state(nodes["林舟"], "age", event)["value"] == 29
    assert (
        ops.property_event_state(
            nodes["林舟"], "age", event, scope="cognition", holder_id=nodes["林舟"]
        )["value"]
        == 30
    )


def test_chapter_marks_hang_on_events(tmp_path: Path) -> None:
    """A chapter boundary is a mark on the reading chain, not a break."""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", PROP_TEXT)
    revision = ops.graph_project_info()["revision"]

    marked = {
        "kind": "node", "id": "e1", "node_type": "event", "name": "开场",
        "disclosure": {"source_id": source_id, "start": 0},
        "source_marks": [{"source_id": source_id, "role": "start"}],
    }
    assert ops.graph_precheck([marked], revision) == []

    wrong_role = dict(marked, id="e2", source_marks=[{"source_id": source_id, "role": "middle"}])
    with pytest.raises(ValueError, match="role"):
        ops.graph_propose([wrong_role], revision)

    not_an_event = {
        "kind": "node", "id": "p1", "node_type": "person", "name": "甲",
        "source_marks": [{"source_id": source_id, "role": "start"}],
    }
    with pytest.raises(ValueError, match="事件"):
        ops.graph_propose([not_an_event], revision)


def test_one_anchor_may_hold_several_disclosures(tmp_path: Path) -> None:
    """An event is a disclosure anchor, not a slot: several values may share it."""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", PROP_TEXT)
    data = parse_nodes(PROP_RAW, PROP_TEXT) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )
    nodes = {item["name"]: item["id"] for item in ops.graph_snapshot()["nodes"]}
    event = nodes["林舟出场"]
    disclosure = {"source_id": source_id, "start": PROP_TEXT.index("林舟今年")}
    base = {
        "kind": "property_value", "node_id": nodes["林舟"], "property_id": "age",
        "event_id": event, "disclosure": disclosure,
        "evidence": {"source_id": source_id, "quote": "林舟今年二十九岁"},
    }
    revision = ops.graph_project_info()["revision"]

    # Two statements at one anchor: a retelling and a correction. Both are real
    # disclosures, so neither is refused.
    both = [dict(base, id="h1", value=29), dict(base, id="h2", value=30)]
    assert ops.change_precheck([], both, revision) == []

    # A cognition and a world statement at the same anchor are also separate.
    split = [
        dict(base, id="h3", value=29),
        dict(base, id="h4", value=30, scope="cognition", holder_id=nodes["林舟"]),
    ]
    assert ops.change_precheck([], split, revision) == []


def test_same_anchor_keeps_source_order_not_write_order(tmp_path: Path) -> None:
    """Two disclosures at one event: the later sentence wins, whenever it was added."""
    ops = create_graph_project(tmp_path / "graph.nproj")
    source_id = ops.import_source("第一章", PROP_TEXT)
    data = parse_nodes(PROP_RAW, PROP_TEXT) | {
        "source_id": source_id, "source_hash": ops.source(source_id)["hash"]
    }
    built = build_candidate(ops, data)
    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )
    nodes = {item["name"]: item["id"] for item in ops.graph_snapshot()["nodes"]}
    event = nodes["林舟出场"]
    quote = "林舟今年二十九岁"
    base = PROP_TEXT.index(quote)

    def record(rid: str, value, offset: int) -> dict:
        return {
            "kind": "property_value", "id": rid, "node_id": nodes["林舟"],
            "property_id": "age", "value": value, "event_id": event,
            "disclosure": {"source_id": source_id, "start": offset},
            "evidence": {"source_id": source_id, "quote": quote},
        }

    revision = ops.graph_project_info()["revision"]
    # The later sentence is written first; the earlier one is added afterwards.
    later = record("age-late", 30, base + 10)
    earlier = record("age-early", 29, base)
    ops.change_accept(
        ops.change_propose([], [later, earlier], revision, source="correction"), 1
    )

    # Both survive, and "latest disclosure" is still the later sentence.
    assert ops.property_state(nodes["林舟"], "age")["state"] == {"kind": "known", "value": 30}
    assert len([
        item for item in ops.property_records()
        if item["kind"] == "property_value" and item["property_id"] == "age"
    ]) >= 2

    # Reading only up to the earlier sentence shows the earlier value.
    cut = {"source_id": source_id, "start": base + 1}
    assert ops.property_state(nodes["林舟"], "age", at=cut)["state"] == {"kind": "known", "value": 29}


def test_the_later_sentence_wins_even_when_it_was_written_first(tmp_path: Path) -> None:
    """One property, two disclosures: the later sentence wins, whenever it was added."""
    ops = create_graph_project(tmp_path / "node-props.nproj")
    source_id = ops.import_source("第一章", "林舟今年二十九岁。三年后他三十三岁。")
    candidate = ops.graph_propose(
        [
            {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
            {"kind": "node", "id": "2", "node_type": "event", "name": "交代年龄"},
        ],
        ops.graph_project_info()["revision"],
    )
    ops.graph_accept(candidate, 1)
    revision = ops.graph_project_info()["revision"]

    def record(rid: str, value: str, offset: int) -> dict:
        return {
            "kind": "property_value", "id": rid, "node_id": "1", "property_id": "age",
            "value": value, "event_id": "2", "related_nodes": ["2"],
            "disclosure": {"source_id": source_id, "start": offset},
        }

    definition = {
        "kind": "property_definition", "id": "age", "value_type": "string",
        "cardinality": "one",
    }
    # The later sentence is written first; the earlier one is added afterwards.
    later = record("age-late", "三十三岁", 9)
    earlier = record("age-early", "二十九岁", 0)
    ops.property_accept(
        ops.property_propose([definition, later, earlier], revision, source="manual"), 1
    )

    summary = ops.property_node_summary("1")

    assert len(summary["properties"]) == 1
    item = summary["properties"][0]
    assert item["property_id"] == "age"
    # 谁算数看**阅读位置**，不看写的先后：后写的那条（更早的披露）被取代。
    assert item["value"] == "三十三岁"
    assert [row["value"] for row in item["records"]] == ["三十三岁"]
    # 读者看到的不是裸 id：事件和关联节点都带名字。
    assert item["records"][0]["event_name"] == "交代年龄"
    assert item["records"][0]["related"] == [{"id": "2", "name": "交代年龄"}]

    # 编辑器只管现在：失效的那条不在列表里，但记录本身没有被删掉。
    rows = ops.node_editor("1")["properties"][0]["records"]
    assert [(row["value"], row["valid"]) for row in rows] == [("三十三岁", True)]
    stored = [
        record for record in ops.property_records(include_retracted=True)
        if record.get("kind") == "property_value"
    ]
    assert {record["value"] for record in stored} == {"二十九岁", "三十三岁"}


def test_node_properties_keeps_one_line_per_aspect(tmp_path: Path) -> None:
    ops = create_graph_project(tmp_path / "node-props-scope.nproj")
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "person", "name": "老周"},
                {"kind": "node", "id": "3", "node_type": "event", "name": "交谈"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    revision = ops.graph_project_info()["revision"]
    records = [
        {"kind": "property_definition", "id": "realm", "value_type": "string",
         "cardinality": "one"},
        {"kind": "property_value", "id": "v-world", "node_id": "1", "property_id": "realm",
         "value": "灵寂期", "event_id": "3", "scope": "world"},
        {"kind": "property_value", "id": "v-claim", "node_id": "1", "property_id": "realm",
         "value": "深不可测", "event_id": "3", "scope": "claim", "holder_id": "2"},
    ]
    ops.property_accept(ops.property_propose(records, revision, source="manual"), 1)

    items = ops.property_node_summary("1")["properties"]

    # 别人说的不算他的属性：「老周说他深不可测」是老周的说法，记在节点 1 上，
    # 但显示在老周那一页，不在节点 1 的方面里。
    assert len(items) == 1
    item = items[0]
    assert item["aspect"] == "realm"
    assert [row["scope"] for row in item["records"]] == ["world"]
    assert item["value"] == "灵寂期"

    claims = ops.property_node_summary("2")["claims"]
    assert [row["property_id"] for row in claims] == ["realm"]
    assert claims[0]["value"] == "深不可测"
    assert claims[0]["node_id"] == "1"   # 关于谁


CATEGORY_TEXT = "林舟身穿一套黑色西装，脸色铁青。\n"
CATEGORY_RAW = """
{"nodes": [
  {"name": "林舟", "type": "person", "quotes": ["林舟身穿一套黑色西装"]},
  {"name": "林舟出场", "type": "event", "quotes": ["林舟身穿一套黑色西装"],
   "participants": ["林舟"]}
],
 "properties": [
  {"node": "林舟", "property": "clothing", "value": "一套黑色西装", "value_type": "string",
   "category": "外貌", "quotes": ["林舟身穿一套黑色西装"], "scope": "world",
   "at_event": "林舟出场"}
]}
"""


def test_parse_keeps_the_category_the_model_chose() -> None:
    result = parse_nodes(CATEGORY_RAW, CATEGORY_TEXT)
    assert result["properties"][0]["category"] == "外貌"
    # 没给分类不报错：解析层只管收下，落不到大类时由投影兜到「其他」。
    plain = parse_nodes(PROP_RAW, PROP_TEXT)
    assert all(item["category"] == "" for item in plain["properties"])


def test_build_candidate_writes_the_category_on_the_disclosure(tmp_path: Path) -> None:
    ops = create_graph_project(tmp_path / "category.nproj")
    source_id = ops.import_source("第一章", CATEGORY_TEXT)
    data = parse_nodes(CATEGORY_RAW, CATEGORY_TEXT) | {"source_id": source_id}

    built = build_candidate(ops, data)
    value = next(
        record for record in built["property_records"] if record["kind"] == "property_value"
    )
    assert value["category"] == "外貌"

    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )
    item = ops.property_node_summary(value["node_id"])["properties"][0]
    assert (item["category"], item["category_source"]) == ("外貌", "extraction")


def test_the_prompt_lists_categories_and_the_names_already_in_use(tmp_path: Path) -> None:
    ops = create_graph_project(tmp_path / "prompt.nproj")
    source_id = ops.import_source("第一章", PROP_TEXT)
    data = parse_nodes(PROP_RAW, PROP_TEXT) | {"source_id": source_id}
    built = build_candidate(ops, data)
    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )

    known = known_properties(ops)
    assert {(item["property"], item["node_type"], item["category"]) for item in known} == {
        ("age", "person", "基本信息"),
        ("skin", "person", "外貌"),
    }

    prompt = build_prompt("第一章", PROP_TEXT, known)
    # 提示词自带分类表，并要求每条属性给一个 category。
    assert "## 属性分类（展示用）" in prompt
    assert "拿不准就写「其他」" in prompt
    assert "`age`（人物 · 基本信息）" in prompt
    assert "`skin`（人物 · 外貌）" in prompt


def test_a_storyline_holds_its_scenes(tmp_path: Path) -> None:
    """大事件是节点，下面挂场景：场景说自己属于哪个大事件，程序反过来给它填子节点。"""
    ops = create_graph_project(tmp_path / "story.nproj")
    text = "林舟推门进来。老周把钥匙交给他。"
    data = parse_nodes(json.dumps({
        "nodes": [
            {"name": "林舟接手杂货铺", "type": "storyline",
             "quotes": ["老周把钥匙交给他"], "aliases": []},
            {"name": "杂货铺交接", "type": "scene",
             "quotes": ["老周把钥匙交给他"], "aliases": [],
             "storyline": "林舟接手杂货铺"},
            {"name": "老周交出钥匙", "type": "event",
             "quotes": ["老周把钥匙交给他"], "aliases": [],
             "participants": ["老周", "林舟"], "scene": "杂货铺交接"},
        ],
        "properties": [],
    }, ensure_ascii=False), text) | {
        "source_id": (sid := ops.import_source("第一章", text)),
        "source_hash": ops.source(sid)["hash"],
        "chapter": "第一章",
    }
    built = build_candidate(ops, data)
    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )

    nodes = {node["name"]: node for node in ops.graph_snapshot()["nodes"]}
    scene = nodes["杂货铺交接"]
    story = nodes["林舟接手杂货铺"]
    assert scene["node_type"] == "scene"
    assert story["node_type"] == "storyline"
    # 大事件挂的是场景，场景挂的是事件。
    assert story["events"] == [scene["id"]]
    assert scene["events"] == [nodes["老周交出钥匙"]["id"]]
