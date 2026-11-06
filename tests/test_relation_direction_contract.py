"""Direction is explicit model input and preserved by the real projection."""
import json

from agent_fixtures import project as project
from test_relationship_state import TEXT, draft

from narrative.assistant import _first_prompt
from narrative.extraction_draft import RESULT_SCHEMA
from narrative.project_package import layout
from narrative.tool_api import TOOLS, call


def test_prompt_schema_and_tools_expose_consistent_direction_semantics():
    example = "node=A,property=父亲,value=B表示B是A的父亲"
    assert "value所指对象相对于node所属对象的身份、关系或作用" in _first_prompt("核对关系")
    fields = RESULT_SCHEMA["properties"]["properties"]["items"]["properties"]
    assert example in fields["property"]["description"]
    assert "参照方A" in fields["node"]["description"]
    assert "作用角色" in fields["node"]["description"]
    assert "作用中的角色" in fields["property"]["description"]
    assert "作用中的角色" in TOOLS["record_property_change"].params["property_id"]["description"]
    assert "普通string/any值保持字面内容" in fields["value"]["description"]
    assert example in TOOLS["record_property_change"].params["property_id"]["description"]
    draft_property = TOOLS["submit_extraction"].schema()["properties"]["result"]["properties"]["properties"]["items"]["properties"]["property"]
    assert example in draft_property["description"]


def test_relation_projection_preserves_declared_subject_and_counterpart(project):
    text = TEXT.replace("朋友乙", "父亲乙")
    sid = project.import_source("方向投影", text)
    payload = json.loads(json.dumps(draft(), ensure_ascii=False).replace("朋友乙", "父亲乙"))
    payload["properties"] = [dict(payload["properties"][0], property="父亲", symmetric=False, op="set")]
    outcome = project.submit_extraction(sid, payload)
    assert outcome["status"] == "accepted", outcome
    ids = {n["name"]: n["id"] for n in project.graph_snapshot()["nodes"]}
    before = project.property_records()
    edges = call("relation_map", {}, project=layout(project.project_info().path).entry)["edges"]
    assert len(edges) == 1
    assert (edges[0]["from"], edges[0]["kind"], edges[0]["to"]) == (ids["甲"], "父亲", ids["乙"])
    assert edges[0]["symmetric"] is False
    assert project.property_records() == before
