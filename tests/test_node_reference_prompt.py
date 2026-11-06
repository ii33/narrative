"""The task prompt prefers compact typed IDs and stable batch-local references."""
import json
import re
from pathlib import Path

from agent_fixtures import project as project

from narrative import assistant, tool_api
from narrative.extraction_draft import RESULT_SCHEMA


def test_reference_prompt_is_concise_and_does_not_require_repeating_existing_nodes(project):
    from narrative.tool_loading import tool_guidance
    for detail in ("standard", "fine"):
        project.save_project_settings(project.load_project_settings() | {'extraction_granularity': detail})
        initial=assistant._first_prompt("请补充已知人物的属性。", extraction_granularity=detail)
        prompt='\n'.join(row['text'] for row in tool_guidance(project, ['submit_extraction'])['guides'])
        assert "@1" in prompt and "@2" in prompt and "nodes数组从1开始" in prompt
        assert "P1人物" in prompt and "E2事件" in prompt
        assert "已有节点不必重列nodes或引文" in prompt
        assert "nodes:[]" in prompt and "properties直接引用P1/E2" in prompt
        assert "唯一名称可用" in prompt and "同名必须明确ID" in prompt
        assert "不取首个匹配" in prompt
        assert "用户说：请补充已知人物的属性。" in initial
    assert "@1" in tool_api.TOOLS["submit_extraction"].params["result"]["description"]


def test_documented_reference_example_uses_only_current_draft_fields():
    document=Path("docs/AGENT_API.md").read_text(encoding="utf-8")
    examples=[json.loads(value) for value in re.findall(r"```json\n(.*?)\n```",document,re.S)]
    draft=next(value for value in examples if "nodes" in value and "properties" in value)
    allowed_nodes=RESULT_SCHEMA["properties"]["nodes"]["items"]["properties"]
    allowed_properties=RESULT_SCHEMA["properties"]["properties"]["items"]["properties"]
    assert all(set(row) <= set(allowed_nodes) for row in draft["nodes"])
    assert all(set(row) <= set(allowed_properties) for row in draft["properties"])
    indexed = {row["type"]: (f"@{index}", row) for index, row in enumerate(draft["nodes"], 1)}
    person_ref, _person = indexed["person"]
    phase_ref, _phase = indexed["big_event"]
    scene_ref, scene = indexed["scene"]
    event_ref, event = indexed["event"]
    assert scene["big_event"] == phase_ref
    assert event["scene"] == scene_ref
    assert event["participants"] == [person_ref, "P2"]
    assert draft["properties"][0]["node"] == person_ref
    assert draft["properties"][0]["at_event"] == event_ref

    existing=next(value for value in examples if value.get("nodes") == [])
    assert existing["properties"][0]["node"] == "P1"
    assert existing["properties"][0]["at_event"] == "E1"
