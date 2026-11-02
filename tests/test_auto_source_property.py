"""A supplied quote is never discarded when source is determined from its event."""
import pytest
from agent_fixtures import project as project
from test_action_extraction import TEXT, seed

from narrative import tool_api
from narrative.project_package import layout


def test_property_source_and_coordinates_are_derived_from_explicit_event(project):
    sid, event = seed(project)
    project.import_source("另一份原文", "乙离开。")
    person = next(row for row in project.graph_snapshot()["nodes"] if row["node_type"] == "person")
    params = {"node_id": person["id"], "property_id": "方式", "value": "观察", "value_type": "string", "event_id": event["id"], "quote": "停下张望"}
    entry = layout(project.project_info().path).entry
    result = tool_api.call("add_property", params, project=entry)
    assert result["status"] == "accepted"
    value = next(row for row in project.property_records() if row.get("property_id") == "方式")
    assert value["evidence"] == {"source_id": sid, "quote": "停下张望"}
    assert value["disclosure"] == {"source_id": sid, "start": TEXT.index("停下张望")}
    revision = project.graph_project_info()["revision"]
    with pytest.raises(tool_api.ToolError, match="引文"):
        tool_api.call("add_property", params | {"quote": "并不存在的引文"}, project=entry)
    assert project.graph_project_info()["revision"] == revision
