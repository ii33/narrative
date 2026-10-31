"""The tool surface: one set of answers for the CLI, MCP and any program."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from narrative import tool_api
from narrative.operations import Operations
from narrative.project_catalog import create_graph_project


def _project(path: Path) -> Path:
    ops = create_graph_project(path)
    source = ops.import_source("第一章", "林舟今年二十九岁，脸色黝黑。后来他笑了。")
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "交代年龄",
                 "participants": ["1"]},
                {"kind": "node", "id": "3", "node_type": "event", "name": "他笑了",
                 "participants": ["1"]},
                {"kind": "node", "id": "4", "node_type": "scene", "name": "初见",
                 "events": ["2", "3"]},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    ops.property_accept(
        ops.property_propose(
            [
                {"kind": "property_definition", "id": "age", "value_type": "string",
                 "cardinality": "one"},
                {"kind": "property_value", "id": "v1", "node_id": "1", "property_id": "age",
                 "value": "二十九岁", "event_id": "2",
                 "disclosure": {"source_id": source, "start": 2}},
                {"kind": "property_value", "id": "v2", "node_id": "1", "property_id": "age",
                 "value": "三十岁", "event_id": "3",
                 "disclosure": {"source_id": source, "start": 14}},
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )
    return path


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    return _project(tmp_path / "tools.nproj")


def test_the_tool_list_describes_every_tool() -> None:
    rows = tool_api.describe()
    names = [row["name"] for row in rows]
    assert "list_nodes" in names and "add_property" in names
    for row in rows:
        assert row["description"]
        assert row["inputSchema"]["type"] == "object"
        for key, spec in row["inputSchema"]["properties"].items():
            assert spec.get("type") or spec.get("enum") or spec.get("items"), key


def test_reading_a_node_at_a_reading_position(project: Path) -> None:
    latest = tool_api.call("get_node", {"node_id": "1"}, project=project)
    assert [item["value"] for item in latest["properties"]] == ["三十岁"]

    early = tool_api.call("get_node", {"node_id": "1", "cut": "2"}, project=project)
    assert [item["value"] for item in early["properties"]] == ["二十九岁"]


def test_listing_nodes_and_counting_them(project: Path) -> None:
    payload = tool_api.call("list_nodes", {"node_type": "event"}, project=project)
    assert [node["id"] for node in payload["nodes"]] == ["2", "3"]
    # 计数不跟类型筛选走：侧栏本身就是那个筛选。
    assert payload["counts"]["person"] == 1


def test_scene_and_event_state(project: Path) -> None:
    scene = tool_api.call("scene_state", {"scene_id": "4"}, project=project)
    assert scene["cut"] == "3"  # 场景切在它最后一个事件上
    assert scene["scene"]["name"] == "初见"

    event = tool_api.call("event_state", {"event_id": "2"}, project=project)
    assert event["cut"] == "2"
    assert any(row["id"] == "1" for row in event["state"])


def test_statistics_cover_nodes_sources_and_records(project: Path) -> None:
    stats = tool_api.call("statistics", {}, project=project)
    assert stats["nodes"]["person"] == 1
    assert stats["sources"][0]["chars"] > 0
    assert stats["properties"]["value_records"] == 2


def test_adding_a_value_invalidates_the_older_one(project: Path) -> None:
    result = tool_api.call(
        "add_property",
        {"node_id": "1", "property_id": "age", "value": "三十一岁", "event_id": "3"},
        project=project,
    )
    assert result["status"] == "accepted"

    latest = tool_api.call("node_properties", {"node_id": "1"}, project=project)
    assert [item["value"] for item in latest["properties"]] == ["三十一岁"]


def test_correcting_a_record_takes_the_old_one_back(project: Path) -> None:
    result = tool_api.call(
        "correct_property", {"record_id": "v2", "value": "三十一岁"}, project=project
    )
    assert result["status"] == "accepted"

    latest = tool_api.call("node_properties", {"node_id": "1"}, project=project)
    assert [item["value"] for item in latest["properties"]] == ["三十一岁"]


def test_retracting_needs_a_reason_and_hides_the_record(project: Path) -> None:
    result = tool_api.call(
        "retract_properties", {"ids": ["v2"], "reason": "这句读错了"}, project=project
    )
    assert result["retracted"] == ["v2"]
    # 二十九岁 早在 三十岁 披露时就被取代了，撤回后者之后这一方面就什么都不剩。
    latest = tool_api.call("node_properties", {"node_id": "1"}, project=project)
    assert latest["properties"] == []


def test_revising_a_node_name(project: Path) -> None:
    tool_api.call("revise_node", {"node_id": "1", "name": "林舟强"}, project=project)
    node = tool_api.call("get_node", {"node_id": "1"}, project=project)["node"]
    assert node["name"] == "林舟强"


def test_the_documented_table_covers_every_tool() -> None:
    """加了工具忘了写文档，就是让调用方看不见它。"""
    doc = (Path(__file__).resolve().parents[1] / "docs" / "AGENT_API.md").read_text(
        encoding="utf-8"
    )
    missing = [name for name in tool_api.TOOLS if f"`{name}`" not in doc]
    assert missing == []


def test_moments_are_grouped_by_granularity(project: Path) -> None:
    """三级固定：大事件 → 场景 → 事件。没有大事件的场景进桶，不占第一列。"""
    moments = tool_api.call("list_moments", {}, project=project)
    assert [level["key"] for level in moments["levels"]] == ["storyline", "scene", "event"]
    scene = next(row for row in moments["nodes"] if row["id"] == "4")
    assert scene["parent_id"] == "~orphan"
    bucket = next(row for row in moments["nodes"] if row["id"] == "~orphan")
    assert bucket["synthetic"] is True
    assert [row["id"] for row in moments["nodes"] if row["parent_id"] == "4"] == ["2", "3"]


def test_material_cards_through_the_tool_surface(project: Path, tmp_path: Path) -> None:
    image = tmp_path / "ref.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d4944415478da63f8cfc0f01f00050001ff9d5f6d0d0000000049454e44ae426082"
        )
    )

    created = tool_api.call(
        "add_material_card",
        {"node_id": "1", "title": "青衫少年", "detail": "洗得发白的青衫",
         "valid_from": "3"},
        project=project,
    )
    card = created["card"]
    assert created["cards"][0]["title"] == "青衫少年"

    # 停在 E2：这张卡还没开始，只剩个数量提示。
    early = tool_api.call("node_material", {"node_id": "1", "cut": "2"}, project=project)
    assert early["cards"] == []
    assert early["hidden_count"] == 1

    added = tool_api.call(
        "add_material_image",
        {"node_id": "1", "card_id": card["id"], "path": str(image), "caption": "正面"},
        project=project,
    )
    assert added["image"]["caption"] == "正面"

    edited = tool_api.call(
        "update_material_card",
        {"node_id": "1", "card_id": card["id"], "title": "白衣客"},
        project=project,
    )
    assert edited["card"]["title"] == "白衣客"
    # 只改给了的字段：详情还在。
    assert edited["card"]["detail"] == "洗得发白的青衫"

    image_id = added["image"]["id"]
    removed = tool_api.call(
        "delete_material_image",
        {"node_id": "1", "card_id": card["id"], "image_id": image_id},
        project=project,
    )
    assert removed["cards"][0]["images"] == []

    gone = tool_api.call(
        "delete_material_card", {"node_id": "1", "card_id": card["id"]}, project=project
    )
    assert gone["removed"] == card["id"]
    assert gone["cards"] == []


def test_retracting_without_an_event_means_it_never_counted(project: Path) -> None:
    """删除 = 从来不算：在它自己那次披露的位置上也看不到。"""
    tool_api.call(
        "retract_properties", {"ids": ["v1"], "reason": "手工删除"}, project=project
    )
    at_its_own_event = tool_api.call(
        "node_properties", {"node_id": "1", "cut": "2"}, project=project
    )
    assert at_its_own_event["properties"] == []


def _fresh_project(path: Path) -> tuple[Path, str]:
    """一个只有原文、还没有节点的工程：编号从 P1/E1 开始，好断言。"""
    ops = create_graph_project(path)
    source = ops.import_source("第一章", "林舟推门进来。老周笑了。")
    return path, source


def _seed_extraction(project: Path, source_id: str, text: str, payload: dict) -> None:
    """把一次「模型读出来的东西」直接写进缓存，跳过模型调用。"""
    from narrative.extraction import parse_nodes

    data = parse_nodes(json.dumps(payload, ensure_ascii=False), text)
    data["source_id"] = source_id
    data["chapter"] = "第一章"
    cache = project.with_name(project.stem + "_extraction") / f"{source_id}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


PROPOSAL = {
    "nodes": [
        {"name": "林舟", "type": "person", "quotes": ["林舟推门进来"], "aliases": []},
        {"name": "老周", "type": "person", "quotes": ["老周笑了"], "aliases": []},
        {"name": "老周笑了", "type": "event", "quotes": ["老周笑了"],
         "participants": ["老周"], "scene": ""},
    ],
    "properties": [
        {"node": "老周", "property": "state", "value": "心情不错",
         "quotes": ["老周笑了"], "at_event": "老周笑了", "scope": "world"},
    ],
}


def test_extract_build_computes_the_candidate_without_writing(tmp_path: Path) -> None:
    """落库这一步是纯计算：编号、锚点都在这里定，但图一动不动。"""
    project, source_id = _fresh_project(tmp_path / "build.nproj")
    text = "林舟推门进来。老周笑了。"
    _seed_extraction(project, source_id, text, PROPOSAL)

    before = tool_api.call("project_info", {}, project=project)["project"]["revision"]
    built = tool_api.call("extract_build", {"source_id": source_id}, project=project)

    assert built["errors"] is False
    # 每种类型自己一套编号，由程序发，不由模型猜。
    nodes = [obj for obj in built["objects"] if obj.get("node_type")]
    assert sorted(obj["id"] for obj in nodes) == ["E1", "P1", "P2"]
    # 顺带一条 source_order：这一章在阅读顺序里的位置。
    assert any(obj.get("kind") == "source_order" for obj in built["objects"])
    values = [
        record for record in built["property_records"]
        if record.get("kind") == "property_value"
    ]
    assert [record["node_id"] for record in values] == ["P2"]
    assert values[0]["event_id"] == "E1"

    # 只算不写：修订号没动，候选也没多出来。
    after = tool_api.call("project_info", {}, project=project)["project"]["revision"]
    assert after == before
    pending = tool_api.call("list_candidates", {}, project=project)
    assert pending["changes"] == []


def test_extract_commit_lets_the_confident_part_straight_in(tmp_path: Path) -> None:
    """引文对得上、预检没问题、模型没存疑：不用人点头，直接进图。"""
    project, source_id = _fresh_project(tmp_path / "commit.nproj")
    _seed_extraction(project, source_id, "林舟推门进来。老周笑了。", PROPOSAL)

    before = tool_api.call("project_info", {}, project=project)["project"]["revision"]
    committed = tool_api.call("extract_commit", {"source_id": source_id}, project=project)

    accepted = committed["accepted"]
    assert accepted and accepted["node_count"] == 3
    assert accepted["revision"] == before + 1
    # 没有拿不准的，就不留候选。
    assert committed["candidate"] is None
    assert committed["doubtful"] == []
    assert tool_api.call("list_candidates", {}, project=project)["changes"] == []

    nodes = tool_api.call("list_nodes", {}, project=project)["nodes"]
    assert sorted(node["id"] for node in nodes) == ["E1", "P1", "P2"]


def test_extract_commit_can_still_hand_everything_to_review(tmp_path: Path) -> None:
    """review="all" 是旧行为：一条都不自动接受，全部留成待审候选。"""
    project, source_id = _fresh_project(tmp_path / "review.nproj")
    _seed_extraction(project, source_id, "林舟推门进来。老周笑了。", PROPOSAL)

    before = tool_api.call("project_info", {}, project=project)["project"]["revision"]
    committed = tool_api.call(
        "extract_commit", {"source_id": source_id, "review": "all"}, project=project
    )
    assert committed["accepted"] is None
    assert committed["candidate"]
    # 候选不是图：接受之前修订号不动。
    assert tool_api.call("project_info", {}, project=project)["project"]["revision"] == before
    pending = tool_api.call("list_candidates", {}, project=project)
    assert [row["id"] for row in pending["changes"]] == [committed["candidate"]]

    accepted = tool_api.call(
        "accept_candidate", {"candidate_id": committed["candidate"]}, project=project
    )
    assert accepted["revision"] == before + 1
    nodes = tool_api.call("list_nodes", {}, project=project)["nodes"]
    assert sorted(node["id"] for node in nodes) == ["E1", "P1", "P2"]


def test_extract_build_needs_a_proposal_first(tmp_path: Path) -> None:
    project, source_id = _fresh_project(tmp_path / "empty.nproj")
    with pytest.raises(tool_api.ToolError, match="extract_propose"):
        tool_api.call("extract_build", {"source_id": source_id}, project=project)


def test_bad_calls_say_what_is_wrong(project: Path) -> None:
    with pytest.raises(tool_api.ToolError):
        tool_api.call("no_such_tool", {}, project=project)
    with pytest.raises(tool_api.ToolError):
        tool_api.call("get_node", {"nope": 1}, project=project)
    with pytest.raises(tool_api.ToolError):
        tool_api.call("get_node", {}, project=project)
    with pytest.raises(tool_api.ToolError):
        tool_api.call("get_node", {"node_id": "999"}, project=project)
    with pytest.raises(tool_api.ToolError):
        tool_api.call("project_info", {}, project=None)


def test_attach_scenes_puts_an_earlier_scene_under_a_later_storyline(tmp_path: Path) -> None:
    """大事件常常是后面才成形的：回头把前面的场景挂上去，不用重提。"""
    project, source_id = _fresh_project(tmp_path / "attach.nproj")
    ops = Operations.open_graph_project(project)
    revision = ops.graph_project_info()["revision"]
    quote = {"source_id": source_id, "quote": "林舟推门进来。老周笑了。"}
    ops.change_accept(ops.change_propose([
        {"kind": "node", "id": "S1", "node_type": "scene", "name": "旅社房间",
         "disclosure": {"source_id": source_id, "start": 0}, "evidence": quote},
        {"kind": "node", "id": "S2", "node_type": "scene", "name": "走廊",
         "disclosure": {"source_id": source_id, "start": 0}, "evidence": quote},
    ], [], revision, source="manual"), 1)

    # 后面一章才发现这两场其实是同一件事
    revision = ops.graph_project_info()["revision"]
    ops.change_accept(ops.change_propose([
        {"kind": "node", "id": "T1", "node_type": "storyline", "name": "旅社一夜",
         "disclosure": {"source_id": source_id, "start": 0}, "evidence": quote},
    ], [], revision, source="manual"), 1)

    attached = tool_api.call(
        "attach_scenes", {"storyline_id": "T1", "scene_ids": ["S1", "S2"]}, project=project
    )
    assert attached["status"] == "accepted"
    assert attached["events"] == ["S1", "S2"]

    # 再加一次不会重复
    again = tool_api.call(
        "attach_scenes", {"storyline_id": "T1", "scene_ids": ["S1"]}, project=project
    )
    assert again["events"] == ["S1", "S2"]


def test_relation_map_reads_both_directions_and_states(tmp_path: Path) -> None:
    """关系网：人是点，关系是有方向的边；括号里的是状态。"""
    project, source_id = _fresh_project(tmp_path / "relations.nproj")
    ops = Operations.open_graph_project(project)
    revision = ops.graph_project_info()["revision"]
    quote = {"source_id": source_id, "quote": "林舟推门进来。老周笑了。"}
    ops.change_accept(ops.change_propose([
        {"kind": "node", "id": "P1", "node_type": "person", "name": "林舟",
         "disclosure": {"source_id": source_id, "start": 0}, "evidence": quote},
        {"kind": "node", "id": "P2", "node_type": "person", "name": "老周",
         "disclosure": {"source_id": source_id, "start": 0}, "evidence": quote},
        {"kind": "node", "id": "P3", "node_type": "person", "name": "苏娘",
         "disclosure": {"source_id": source_id, "start": 0}, "evidence": quote},
        {"kind": "node", "id": "E1", "node_type": "event", "name": "老周收徒",
         "disclosure": {"source_id": source_id, "start": 0}, "evidence": quote},
    ], [], revision, source="manual"), 1)

    revision = ops.graph_project_info()["revision"]
    ops.property_accept(ops.property_propose([
        {"kind": "property_definition", "id": "师父", "value_type": "node",
         "cardinality": "one"},
        {"kind": "property_definition", "id": "徒弟", "value_type": "node",
         "cardinality": "one"},
        {"kind": "property_definition", "id": "崇拜", "value_type": "node",
         "cardinality": "one"},
        {"kind": "property_definition", "id": "恋人（冷战中）", "value_type": "node",
         "cardinality": "one"},
        {"kind": "property_value", "id": "r1", "event_id": "E1", "node_id": "P2", "property_id": "师父",
         "value": "P1", "evidence": quote},
        {"kind": "property_value", "id": "r2", "event_id": "E1", "node_id": "P1", "property_id": "徒弟",
         "value": "P2", "evidence": quote},
        # 单向：只有林舟崇拜苏娘，苏娘那边没有对应的边。
        {"kind": "property_value", "id": "r3", "event_id": "E1", "node_id": "P1", "property_id": "崇拜",
         "value": "P3", "evidence": quote},
        {"kind": "property_value", "id": "r4", "event_id": "E1", "node_id": "P2", "property_id": "恋人（冷战中）",
         "value": "P3", "evidence": quote},
    ], revision, source="manual"), 1)

    graph = tool_api.call("relation_map", {}, project=project)
    assert {row["name"] for row in graph["persons"]} == {"林舟", "老周", "苏娘"}
    edges = {(row["from_name"], row["kind"], row["to_name"]): row["state"] for row in graph["edges"]}
    # 对等关系两支箭头，词不一样。
    assert ("老周", "师父", "林舟") in edges
    assert ("林舟", "徒弟", "老周") in edges
    # 单向关系只有一支。
    assert ("林舟", "崇拜", "苏娘") in edges
    assert not [key for key in edges if key[0] == "苏娘"]
    # 状态单独给出。
    assert edges[("老周", "恋人", "苏娘")] == "冷战中"


def test_relation_map_leaves_out_a_person_pointing_at_themselves(tmp_path: Path) -> None:
    """自己指向自己不是关系：那种记录说的是身份，画出来是个自环。"""
    project, source_id = _fresh_project(tmp_path / "self.nproj")
    ops = Operations.open_graph_project(project)
    revision = ops.graph_project_info()["revision"]
    quote = {"source_id": source_id, "quote": "林舟推门进来。老周笑了。"}
    ops.change_accept(ops.change_propose([
        {"kind": "node", "id": "P1", "node_type": "person", "name": "林舟",
         "disclosure": {"source_id": source_id, "start": 0}, "evidence": quote},
        {"kind": "node", "id": "E1", "node_type": "event", "name": "林舟进门",
         "disclosure": {"source_id": source_id, "start": 0}, "evidence": quote},
    ], [], revision, source="manual"), 1)
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(ops.property_propose([
        {"kind": "property_definition", "id": "关系", "value_type": "either",
         "cardinality": "one"},
        {"kind": "property_value", "id": "v1", "event_id": "E1", "node_id": "P1",
         "property_id": "关系", "value": "林舟", "evidence": quote},
    ], revision, source="manual"), 1)

    graph = tool_api.call("relation_map", {}, project=project)
    assert graph["persons"] and graph["edges"] == []


def test_relations_stop_at_the_reading_position(tmp_path) -> None:
    """停在第一场时，后面才揭晓的关系不该出现在图上。"""
    from narrative.project_catalog import create_graph_project
    from narrative.tool_api import _relation_map

    ops = create_graph_project(tmp_path / "rel.nproj")
    text = "林舟把钥匙交给老周。后来林舟和苏娘成了仇敌。"
    ops.import_source("第一章", text)
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "person", "name": "老周"},
                {"kind": "node", "id": "3", "node_type": "person", "name": "苏娘"},
                {"kind": "node", "id": "4", "node_type": "scene", "name": "第一场",
                 "events": ["5"]},
                {"kind": "node", "id": "5", "node_type": "event", "name": "交钥匙",
                 "participants": ["1", "2"]},
                {"kind": "node", "id": "6", "node_type": "scene", "name": "第二场",
                 "events": ["7"]},
                {"kind": "node", "id": "7", "node_type": "event", "name": "结仇"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    rev = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            [
                {"kind": "property_definition", "id": "朋友", "value_type": "node",
                 "cardinality": "one"},
                {"kind": "property_definition", "id": "仇敌", "value_type": "node",
                 "cardinality": "one"},
                {"kind": "property_value", "id": "v1", "node_id": "1",
                 "property_id": "朋友", "value": "2", "event_id": "5"},
                {"kind": "property_value", "id": "v2", "node_id": "1",
                 "property_id": "仇敌", "value": "3", "event_id": "7"},
            ],
            rev,
            source="manual",
        ),
        1,
    )

    whole = _relation_map(ops, {})
    assert len(whole["edges"]) == 2

    early = _relation_map(ops, {"cut": "4"})
    assert [edge["kind"] for edge in early["edges"]] == ["朋友"]
    assert {person["name"] for person in early["persons"]} == {"林舟", "老周"}

    # 只看相关：两头都得在这一场里。第一场只有林舟和老周，苏娘不该出现。
    only = _relation_map(ops, {"cut": "4", "related_only": True})
    assert [edge["kind"] for edge in only["edges"]] == ["朋友"]
    assert {person["name"] for person in only["persons"]} == {"林舟", "老周"}


    # 只看相关：两头都得在这一场里。第一场只有林舟和老周，苏娘不该出现。
    only = _relation_map(ops, {"cut": "4", "related_only": True})
    assert [edge["kind"] for edge in only["edges"]] == ["朋友"]
    assert {person["name"] for person in only["persons"]} == {"林舟", "老周"}

    # 一个人出场的场景：就画一个人，不把她的其他关系拖进来。
    ops.graph_accept(
        ops.graph_propose(
            [{"kind": "node", "id": "8", "node_type": "scene", "name": "第三场",
              "events": ["9"]},
             {"kind": "node", "id": "9", "node_type": "event", "name": "一个人待着"}],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    alone = _relation_map(ops, {"cut": "8", "related_only": True})
    assert alone["edges"] == []
    assert alone["persons"] == []
