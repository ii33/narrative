"""Single storyline names are strict; removal never rewrites existing user text."""
from __future__ import annotations

import copy
import json

import pytest
from agent_fixtures import project as project
from pydantic import ValidationError

from narrative.assistant import _first_prompt
from narrative.extraction_draft import RESULT_SCHEMA
from narrative.graph_models import Node
from narrative.graph_store import GRAPH_SCHEMA_VERSION

TEXT="甲进屋。甲离开。"


def draft(name="甲进出屋"):
    return {"nodes":[
        {"type":"person","name":"甲","quotes":["甲进屋。"]},
        {"type":"big_event","name":name,"quotes":[TEXT]},
        {"type":"scene","name":"屋内行动","big_event":"@2","quotes":[TEXT]},
        {"type":"event","name":"甲进屋","scene":"@3","participants":["@1"],"quotes":["甲进屋。"]},
        {"type":"event","name":"甲离开","scene":"@3","participants":["@1"],"quotes":["甲离开。"]},
    ],"properties":[]}


def test_current_node_and_tool_contracts_have_no_provisional_field(project):
    assert "provisional_name" not in Node.model_fields
    assert "provisional_name" not in RESULT_SCHEMA["properties"]["nodes"]["items"]["properties"]
    with pytest.raises(ValidationError, match="provisional_name"):
        Node.model_validate({"kind":"node","id":"T1","node_type":"storyline","name":"清晰概括名","provisional_name":"旧暂称"})
    schema=project.get_schema()
    assert "provisional_name" not in json.dumps(schema)
    assert schema["contract_version"] == "graph-nesting/11"
    assert schema["schema_version"] == GRAPH_SCHEMA_VERSION == 11
    assert "直接用清晰概括名，未完也不加暂称" in _first_prompt("提取")


def test_old_draft_field_is_rejected_without_silently_stripping_or_writing(project):
    sid=project.import_source("屋内过程",TEXT)
    payload=draft()
    payload["nodes"][1]["provisional_name"]="旧双轨名称"
    original=copy.deepcopy(payload)
    before=project.graph_snapshot()
    for method in (project.preview_extraction,project.submit_extraction):
        with pytest.raises(ValueError, match="provisional_name"):
            method(sid,payload)
        assert payload == original and project.graph_snapshot() == before
    direct={"kind":"node","id":"T1","node_type":"storyline","name":"清晰概括名",
            "provisional_name":"旧字段","disclosure":{"source_id":sid,"start":0},
            "evidence":{"source_id":sid,"quote":TEXT}}
    rejected=project.apply_changes([direct],[],before["revision"],"不允许旧字段写入")
    assert rejected["status"] == "rejected" and "provisional_name" in json.dumps(rejected)
    assert project.graph_snapshot() == before


@pytest.mark.parametrize("suffix",["（暂称）","(暂称)"])
def test_storyline_suffix_marker_is_diagnosed_instead_of_auto_trimmed(project,suffix):
    sid=project.import_source("屋内过程",TEXT)
    payload=draft("甲进出屋"+suffix)
    original=copy.deepcopy(payload)
    before=project.graph_snapshot()
    checked=project.preview_extraction(sid,payload)
    assert not checked["valid"] and "暂称" in json.dumps(checked,ensure_ascii=False)
    assert project.submit_extraction(sid,payload)["status"] == "rejected"
    assert project.graph_snapshot() == before and payload == original
    with pytest.raises(ValidationError, match="暂称"):
        Node.model_validate({"kind":"node","id":"T1","node_type":"storyline","name":"甲进出屋"+suffix})
    assert Node.model_validate({"kind":"node","id":"P1","node_type":"person","name":"角色"+suffix}).name == "角色"+suffix
    assert Node.model_validate({"kind":"node","id":"T1","node_type":"storyline","name":"讨论暂称这个词"}).name == "讨论暂称这个词"


def test_new_project_persists_only_one_unchanged_name_per_node(project):
    sid=project.import_source("屋内过程",TEXT)
    payload=draft()
    assert project.submit_extraction(sid,payload)["status"] == "accepted"
    nodes=project.graph_snapshot()["nodes"]
    assert next(row["name"] for row in nodes if row["node_type"] == "big_event") == "甲进出屋"
    with project.project.store.connect() as db:
        stored=[json.loads(row[0]) for row in db.execute("SELECT data FROM objects WHERE kind='node'")]
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
    assert all("provisional_name" not in row for row in stored)
