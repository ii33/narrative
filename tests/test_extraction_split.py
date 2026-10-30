"""只有拿不准的才留给人看：可信的直接进图，存疑的进候选。

可信 = 没有反面证据（引文对上、预检没报、审核没报、模型没存疑、同一事件里没有并列值）。
"""

from __future__ import annotations

import json
from pathlib import Path

from narrative.extraction import commit_extraction, parse_nodes
from narrative.project_catalog import create_graph_project

TEXT = "林舟推门进来。林舟笑了。老周站在门口。"

NODES = [
    {"name": "林舟", "type": "person", "quotes": ["林舟推门进来"]},
    {"name": "老周", "type": "person", "quotes": ["老周站在门口"]},
    {"name": "林舟推门进来", "type": "event", "quotes": ["林舟推门进来"],
     "participants": ["林舟"]},
]


def _project(tmp_path: Path, proposal: dict, review: dict | None = None):
    ops = create_graph_project(tmp_path / "split.nproj")
    sid = ops.import_source("第一章", TEXT)
    data = parse_nodes(json.dumps(proposal, ensure_ascii=False), TEXT) | {
        "source_id": sid,
        "source_hash": ops.source(sid)["hash"],
        "chapter": "第一章",
    }
    if review is not None:
        data["review"] = review
    return ops, data


def test_a_clean_extraction_goes_straight_in(tmp_path: Path) -> None:
    """一条反面证据都没有：不留候选，全进图。"""
    ops, data = _project(tmp_path, {
        "nodes": NODES,
        "properties": [{
            "node": "林舟", "property": "身份", "value": "主角",
            "quotes": ["林舟推门进来"], "at_event": "林舟推门进来", "scope": "world",
        }],
    })
    result = commit_extraction(ops, data)

    assert result["doubtful"] == []
    assert result["candidate"] is None
    assert result["accepted"]["node_count"] == 3
    assert result["accepted"]["property_count"] == 1
    assert ops.graph_project_info()["revision"] == result["accepted"]["revision"]
    assert [c for c in ops.change_candidates() if c.get("status") == "pending"] == []


def test_parallel_values_stay_for_review_but_the_node_goes_in(tmp_path: Path) -> None:
    """同一事件里同一方面有两个不同值：节点照样进图，两条取值留给人看。"""
    ops, data = _project(tmp_path, {
        "nodes": NODES,
        "properties": [
            {"node": "林舟", "property": "身份", "value": "主角",
             "quotes": ["林舟推门进来"], "at_event": "林舟推门进来"},
            {"node": "林舟", "property": "身份", "value": "学生",
             "quotes": ["林舟推门进来"], "at_event": "林舟推门进来"},
        ],
    })
    result = commit_extraction(ops, data)

    # 节点进去了：身份没定下来不影响「林舟是谁」。
    assert result["accepted"]["node_count"] == 3
    assert result["accepted"]["property_count"] == 0
    assert len(result["doubtful"]) == 2
    assert {row["why"] for row in result["doubtful"]} == {"同一事件里同一方面有多个不同值"}
    assert result["candidate"]

    pending = ops.change_candidate(result["candidate"])
    values = [
        row for row in pending["property_records"] if row.get("kind") == "property_value"
    ]
    assert len(values) == 2
    # 图里已经有林舟了，候选只补这两条取值。
    nodes = {node["name"] for node in ops.graph_snapshot()["nodes"]}
    assert "林舟" in nodes


def test_a_value_the_model_was_unsure_about_stays(tmp_path: Path) -> None:
    """模型自己标了存疑的那一条留下来，同章其余照常进图。"""
    ops, data = _project(
        tmp_path,
        {
            "nodes": NODES,
            "properties": [
                {"node": "林舟", "property": "身份", "value": "主角",
                 "quotes": ["林舟推门进来"], "at_event": "林舟推门进来"},
                {"node": "老周", "property": "身份", "value": "掌柜",
                 "quotes": ["老周站在门口"], "at_event": "林舟推门进来"},
            ],
        },
        review={"dropped": [], "unresolved": [
            {"node": "老周", "property": "身份", "value": "掌柜", "why": "原文没说职业"},
        ], "rounds": 2},
    )
    result = commit_extraction(ops, data)

    assert result["accepted"]["property_count"] == 1
    assert len(result["doubtful"]) == 1
    assert result["doubtful"][0]["name"] == "老周"
    assert "模型拿不准" in result["doubtful"][0]["why"]
    assert result["candidate"]
