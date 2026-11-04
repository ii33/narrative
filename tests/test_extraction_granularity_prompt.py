"""Task-local instructions reflect the saved project detail without action auto-expansion."""
import pytest
from agent_fixtures import project as project

from narrative.assistant import _first_prompt
from narrative.extraction_draft import DRAFT_VERSION, RESULT_SCHEMA


def test_standard_and_fine_are_distinct_but_both_leave_actions_opt_in():
    standard = _first_prompt("提取", extraction_granularity="standard")
    fine = _first_prompt("提取", extraction_granularity="fine")
    assert "当前工程提取粒度：standard" in standard
    assert "当前工程提取粒度：fine" in fine
    assert "更细事件" in fine
    assert "次要、无名人物和具体地点" in fine
    assert "无法区分的泛称或重复提及不拆成新实体" in fine
    assert "不臆造身份" in fine
    assert "地点以事件world节点引用属性记录" in standard
    assert "上层容器由成员派生人物与地点，不重复写回" in standard
    assert all("标准与精细粒度都默认不提取动作" in text for text in (standard, fine))
    assert DRAFT_VERSION == "conversation-draft/13"
    property_fields = RESULT_SCHEMA["properties"]["properties"]["items"]["properties"]
    node_fields = RESULT_SCHEMA["properties"]["nodes"]["items"]["properties"]
    assert "事件地点使用属性名‘地点’" in property_fields["property"]["description"]
    assert "不含地点" in node_fields["participants"]["description"]
    assert "location" not in node_fields and "locations" not in node_fields
    with pytest.raises(ValueError, match="提取粒度"):
        _first_prompt("提取", extraction_granularity="unknown")


def test_operations_accepts_event_location_reference_and_secondary_people(project):
    text = "旅客甲和旅客乙跟着无名侍者走进客栈后院。"
    source_id = project.import_source("精细地点和次要人物", text)
    draft = {"nodes": [
        {"type": "person", "name": "旅客甲", "quotes": [text]},
        {"type": "person", "name": "旅客乙", "quotes": [text]},
        {"type": "person", "name": "无名侍者", "quotes": ["无名侍者"]},
        {"type": "location", "name": "客栈后院", "quotes": ["客栈后院"]},
        {"type": "scene", "name": "进入客栈后院", "quotes": [text]},
        {"type": "event", "name": "跟随侍者进入后院", "scene": "@5",
         "participants": ["@1", "@2", "@3"], "quotes": [text]},
    ], "properties": [{
        "node": "@6", "property": "地点", "value": "@4", "value_type": "node",
        "scope": "world", "at_event": "@6", "quotes": ["客栈后院"],
    }]}
    accepted = project.submit_extraction(source_id, draft)
    assert accepted["status"] == "accepted", accepted

    nodes = {row["id"]: row for row in project.graph_snapshot()["nodes"]}
    people = {row["name"]: row["id"] for row in nodes.values() if row["node_type"] == "person"}
    location = next(row for row in nodes.values() if row["node_type"] == "location")
    event = next(row for row in nodes.values() if row["node_type"] == "event")
    assert set(event["participants"]) == set(people.values())
    assert len(people) == 3 and "无名侍者" in people

    before = project.property_records()
    location_properties = [row for row in before
                           if row.get("kind") == "property_value" and row.get("property_id") == "地点"]
    assert len(location_properties) == 1
    location_property = location_properties[0]
    location_definition = next(row for row in before if row.get("kind") == "property_definition"
                              and row.get("id") == "地点")
    assert (location_property["node_id"], location_property["property_id"],
            location_property["value"], location_definition["value_type"],
            location_property["scope"], location_property["event_id"]) == (
                event["id"], "地点", location["id"], "node", "world", event["id"])
    assert location_property["disclosure"]["start"] == text.index("客栈后院")
    summary = project.property_node_summary(event["id"])
    assert next(row for row in summary["properties"] if row["property_id"] == "地点")["category"] == "基本信息"
    assert project.property_records() == before
