"""Task-local instructions reflect the saved project detail without action auto-expansion."""
import pytest
from agent_fixtures import project as project

from narrative.assistant import _first_prompt
from narrative.extraction_draft import DRAFT_VERSION, RESULT_SCHEMA


def loaded_extraction_guide(project, mode):
    from narrative.tool_loading import tool_guidance
    project.save_project_settings(project.load_project_settings() | {'extraction_granularity': mode})
    result = tool_guidance(project, ['submit_extraction'])
    assert result['extraction_granularity'] == mode
    return '\n'.join(row['text'] for row in result['guides'])


def test_standard_and_fine_are_distinct_but_both_leave_actions_opt_in(project):
    standard = loaded_extraction_guide(project, "standard")
    fine = loaded_extraction_guide(project, "fine")
    assert "当前工程提取粒度：standard" in _first_prompt('提取', extraction_granularity='standard')
    assert "当前工程提取粒度：fine" in _first_prompt('提取', extraction_granularity='fine')
    assert "真正引发后续独立展开的新目标、矛盾或情境进展" in fine
    assert "次要、无名人物和具体地点" in fine
    assert "无法区分的泛称或重复提及不拆成新实体" in fine
    assert "不臆造身份" in fine
    assert "地点以事件world节点引用属性记录" in standard
    assert "上层容器由成员派生人物与地点，不重复写回" in standard
    assert all("标准与精细粒度都默认不提取动作" in text for text in (standard, fine))
    assert DRAFT_VERSION == "conversation-draft/16"
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
        "node": "@6", "op": "set", "property": "地点", "value": "@4", "value_type": "node",
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
                           if row.get("kind") == "property_change" and row.get("property_id") == "地点"]
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


def test_both_modes_distinguish_complete_events_from_actions_without_banning_consequences(project):
    from narrative.prompt_defaults import DEFAULT_PROMPTS
    for mode in ('standard', 'fine'):
        prompt = loaded_extraction_guide(project, mode)
        assert '同一语境里具有独立叙事意义的一次完整发生或过程' in prompt
        assert '同一叙事目标下的准备→实施→即时结果→收尾优先合为一个完整事件' in prompt
        assert '新信息、决定、关系或结果变化不自动各成边界' in prompt
        assert '不按句子、动词或每条属性变更逐项创建E' in prompt
        assert '不能把本应按需展开的动作改标为E' in prompt
        assert '完整动作链本身不证明它是独立事件' in prompt
        assert '目标—过程—直接结果' in prompt
        assert '相邻候选是否只是同一目标的准备、承接、直接后果或收尾' in prompt
        assert '先合成一个候选再提交' in prompt
        assert '多个动作串成一个名称也不证明过程独立' in prompt
        assert '不新增固定提取分轮，不要求额外工具、重复读取或preview' in prompt
        assert '动作过程和它带来的信息或结果拆成两个E' in prompt
        assert '整理包装、送达、收件确认、收拾离开' in prompt
        assert '不把准备、发现、决定、执行和直接结果各拆一E' in prompt
        assert '寻找替代供货方、重新协商条件的新目标过程' in prompt
        assert '不能因它是具体动作就一概排除' in prompt
        assert '不因它有直接结果就自动单列' in prompt
        assert '边界由模型结合原文语义判断' in prompt
        assert '不以例子、词表或固定事件数机械套用' in prompt
    fine = DEFAULT_PROMPTS['granularity_fine']['text']
    assert '精细不等于把连续动作链拆成更多E' in fine
    assert '每个属性变化' in fine
    assert '次要、无名人物和具体地点' in fine
    assert '可独立处理的情节节拍' not in fine
    assert DEFAULT_PROMPTS['extraction']['version'] == '13'
    assert DEFAULT_PROMPTS['granularity_standard']['version'] == '3'
    assert DEFAULT_PROMPTS['granularity_fine']['version'] == '3'


def test_default_remains_fine_and_independently_consequential_action_can_be_an_event(project):
    before = project.load_project_settings()['extraction_granularity']
    assert before == 'fine'
    text = '甲销毁唯一副本，计划因此无法继续。'
    sid = project.import_source('独立后果', text)
    result = project.submit_extraction(sid, {'nodes': [
        {'type': 'person', 'name': '甲', 'quotes': [text]},
        {'type': 'scene', 'name': '计划中断', 'quotes': [text]},
        {'type': 'event', 'name': '销毁副本使计划中断', 'scene': '@2', 'participants': ['@1'], 'quotes': [text]},
    ], 'properties': []})
    assert result['status'] == 'accepted'
    assert [n['node_type'] for n in project.graph_snapshot()['nodes']].count('event') == 1
    assert not any(n['node_type'] == 'action' for n in project.graph_snapshot()['nodes'])
    assert project.load_project_settings()['extraction_granularity'] == before
