"""harness 里的 agent 能跑提取链，但只碰本轮任务给的来源。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from narrative import graph_agent, tool_api
from narrative.project_catalog import create_graph_project


def _project(path: Path) -> tuple:
    ops = create_graph_project(path)
    source = ops.import_source("第一章", "林舟今年二十九岁，脸色黝黑。")
    cache = tool_api._extraction_path(ops, source)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "chapter": "第一章", "characters": 14, "source_id": source,
        "nodes": [
            {"name": "林舟", "type": "person", "start": 0, "quotes": ["林舟今年二十九岁"],
             "aliases": [], "participants": [], "participants_not_in_text": [], "scene": ""},
            {"name": "交代年龄", "type": "event", "start": 0, "quotes": ["林舟今年二十九岁"],
             "aliases": [], "participants": ["林舟"], "participants_not_in_text": [], "scene": ""},
        ],
        "properties": [
            {"node": "林舟", "property": "age", "value": "二十九岁", "value_type": "string",
             "start": 2, "quotes": ["林舟今年二十九岁"], "scope": "world", "holder": "",
             "at_event": "交代年龄", "source": "narration", "related_nodes": []},
        ],
        "rejected": [],
    }, ensure_ascii=False), encoding="utf-8")
    return ops, source


def test_the_agent_lists_the_extraction_steps() -> None:
    tools = {item["name"]: item for item in graph_agent.capabilities()["tools"]}
    for name in ("extract_propose", "extract_review", "extract_build", "extract_commit"):
        assert name in tools
    # 只有出候选那一步算写；前三步不碰正式图。
    assert tools["extract_commit"]["access"] == "write_pending"
    assert tools["extract_build"]["access"] == "read_only"


def test_the_agent_runs_a_pure_step_on_a_cached_extraction(tmp_path: Path) -> None:
    ops, source = _project(tmp_path / "agent-extract.nproj")
    before = ops.graph_project_info()["revision"]

    built = graph_agent._tool(ops, [source], "extract_build", {"source_id": source}, {})

    assert built["node_count"] == 2
    assert built["errors"] is False
    values = [row for row in built["property_records"] if row["kind"] == "property_value"]
    assert len(values) == 1
    assert ops.graph_project_info()["revision"] == before  # 只算不写


def test_the_agent_cannot_reach_a_source_outside_the_run(tmp_path: Path) -> None:
    ops, source = _project(tmp_path / "agent-guard.nproj")
    with pytest.raises(ValueError, match="不在本轮任务里"):
        graph_agent._tool(ops, [], "extract_build", {"source_id": source}, {})


def test_extraction_tools_take_only_a_source_id(tmp_path: Path) -> None:
    ops, source = _project(tmp_path / "agent-args.nproj")
    with pytest.raises(ValueError, match="只接受 source_id"):
        graph_agent._tool(ops, [source], "extract_build", {"source_id": source, "x": 1}, {})
    with pytest.raises(ValueError, match="须提供 source_id"):
        graph_agent._tool(ops, [source], "extract_build", {}, {})
