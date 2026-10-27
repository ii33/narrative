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
