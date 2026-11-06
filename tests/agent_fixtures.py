"""Synthetic graph drafts and isolated projects for the unified conversation."""

import pytest

from narrative.operations import Operations

TEXT = "甲约好接乙。乙收起便笺。甲接到乙后离开。"


def draft_result(*, note=False, ended=True):
    result = {
        "nodes": [
            {"type": "person", "name": "甲", "quotes": ["甲约好接乙。"]},
            {"type": "person", "name": "乙", "quotes": ["甲约好接乙。"]},
            {"type": "big_event", "name": "接人", "quotes": [TEXT]},
            {"type": "scene", "name": "甲约定并完成接乙", "big_event": "接人", "quotes": [TEXT]},
            {"type": "event", "name": "甲约好接乙", "scene": "甲约定并完成接乙",
             "participants": ["甲", "乙"], "quotes": ["甲约好接乙。"]},
            {"type": "event", "name": "甲接到乙后离开", "scene": "甲约定并完成接乙",
             "participants": ["甲", "乙"], "quotes": ["甲接到乙后离开。"]},
        ],
        "properties": [
            {"node": "甲", "property": "计划", "value": "接乙", "value_type": "string",
             "scope": "claim", "holder": "甲", "category": "心理与意图", "source": "speech",
             "quotes": ["甲约好接乙。"], "at_event": "甲约好接乙"},
            {"node": "甲", "property": "朋友", "value": "乙", "value_type": "node",
             "category": "关系", "quotes": ["甲约好接乙。"], "at_event": "甲约好接乙"},
        ],
        "uncertainties": [], "exclusions": [],
    }
    for row in result["properties"]:
        row["op"] = "set"
    if ended:
        result["properties"].append({"node": "甲", "property": "计划", "at_event": "甲接到乙后离开",
            "op": "clear", "scope": "claim", "holder": "甲", "quotes": ["甲接到乙后离开。"]})
    if note:
        result["nodes"].append({"type": "item", "name": "便笺", "quotes": ["乙收起便笺。"]})
        result["properties"].append({"node": "便笺", "property": "持有者", "value": "乙", "value_type": "either",
                                     "op": "set", "scope": "world", "category": "归属与持有", "quotes": ["乙收起便笺。"],
                                     "at_event": "甲接到乙后离开"})
    return result


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    return Operations.create_graph_project(tmp_path / "unified.nproj")
