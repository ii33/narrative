"""候选详情要能读：一条只有属性的候选，也要列成人看得懂的行。"""

from __future__ import annotations

import json
from pathlib import Path

from narrative.extraction import commit_extraction, parse_nodes
from narrative.graph_display import candidate_rows
from narrative.project_catalog import create_graph_project

TEXT = "林舟推门进来。林舟笑了。"
NODES = [
    {"name": "林舟", "type": "person", "quotes": ["林舟推门进来"]},
    {"name": "林舟推门进来", "type": "event", "quotes": ["林舟推门进来"],
     "participants": ["林舟"]},
]


def test_a_candidate_of_values_alone_still_lists_its_rows(tmp_path: Path) -> None:
    """同一方面两个并列值 → 节点进图、两条取值留候选；详情要看得见这两条。"""
    ops = create_graph_project(tmp_path / "rows.nproj")
    sid = ops.import_source("第一章", TEXT)
    data = parse_nodes(
        json.dumps({
            "nodes": NODES,
            "properties": [
                {"node": "林舟", "property": "身份", "value": "主角",
                 "quotes": ["林舟推门进来"], "at_event": "林舟推门进来"},
                {"node": "林舟", "property": "身份", "value": "学生",
                 "quotes": ["林舟推门进来"], "at_event": "林舟推门进来"},
            ],
        }, ensure_ascii=False),
        TEXT,
    ) | {"source_id": sid, "source_hash": ops.source(sid)["hash"], "chapter": "第一章"}
    result = commit_extraction(ops, data)

    detail = ops.change_candidate(result["candidate"])
    rows = candidate_rows(ops.graph_snapshot(), detail)

    assert len(rows) == 2
    assert {row["kind"] for row in rows} == {"属性"}
    assert {row["label"] for row in rows} == {"林舟 · 身份 = 主角", "林舟 · 身份 = 学生"}
    # 节点已经在图里了，候选里没有节点行——这正是之前面板空着的原因。
    assert all(row["id"].startswith("val_") for row in rows)


def test_node_rows_come_first_when_the_candidate_creates_them(tmp_path: Path) -> None:
    ops = create_graph_project(tmp_path / "nodes.nproj")
    sid = ops.import_source("第一章", TEXT)
    data = parse_nodes(
        json.dumps({"nodes": NODES, "properties": []}, ensure_ascii=False), TEXT
    ) | {"source_id": sid, "source_hash": ops.source(sid)["hash"], "chapter": "第一章"}
    detail = commit_extraction(ops, data)  # 干净提议：直接进图，没有候选
    assert detail["candidate"] is None

    rows = candidate_rows(
        {"nodes": []},
        {
            "graph_objects": [
                {"kind": "node", "id": "P1", "node_type": "person", "name": "林舟"},
            ],
            "property_records": [
                {"kind": "property_value", "id": "val_1", "node_id": "P1",
                 "property_id": "身份", "value": "主角", "scope": "world"},
            ],
        },
    )
    assert [row["kind"] for row in rows] == ["人物", "属性"]
    assert rows[0]["label"] == "林舟"
    assert rows[1]["label"] == "林舟 · 身份 = 主角"
