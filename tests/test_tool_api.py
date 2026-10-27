"""The tool surface: one set of answers for the CLI, MCP and any program."""

from __future__ import annotations

from pathlib import Path

import pytest

from narrative import tool_api
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
                 "cardinality": "one", "persistence": "until_changed"},
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
    return _project(tmp_path / "tools.sqlite3")


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
    moments = tool_api.call("list_moments", {}, project=project)
    assert [level["key"] for level in moments["levels"]] == ["scene", "event"]
    scene = next(row for row in moments["nodes"] if row["id"] == "4")
    assert scene["parent_id"] == ""
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
