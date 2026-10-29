"""阅读位置：节点列表与属性区共用同一个切点。

切点是"读者读到哪里"，所以它必须跨章成立——第二章的节点在读到第一章时不该出现。
"""

from __future__ import annotations

from pathlib import Path

from narrative.project_catalog import create_graph_project


def _two_chapters(path: Path):
    ops = create_graph_project(path)
    first = ops.import_source("第一章", "林舟推开房门，看见陈默。")
    second = ops.import_source("第二章", "老周在火星传送阵等他。")
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "source_order", "id": "order_1", "source_id": first,
                 "ordinal": 1, "context_id": "canon"},
                {"kind": "source_order", "id": "order_2", "source_id": second,
                 "ordinal": 2, "context_id": "canon"},
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟",
                 "disclosure": {"source_id": first, "start": 0}},
                {"kind": "node", "id": "2", "node_type": "person", "name": "陈默",
                 "disclosure": {"source_id": first, "start": 10}},
                {"kind": "node", "id": "3", "node_type": "person", "name": "老周",
                 "disclosure": {"source_id": second, "start": 0}},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    return ops, first, second


def test_the_list_stops_at_the_reading_position_in_the_same_chapter(
    tmp_path: Path,
) -> None:
    ops, first, _second = _two_chapters(tmp_path / "cut-same.nproj")

    early = ops.graph_query(disclosure_cut={"source_id": first, "start": 5})
    assert [node["name"] for node in early["nodes"]] == ["林舟"]

    later = ops.graph_query(disclosure_cut={"source_id": first, "start": 30})
    assert [node["name"] for node in later["nodes"]] == ["林舟", "陈默"]


def test_a_later_chapter_is_not_known_yet(tmp_path: Path) -> None:
    ops, first, second = _two_chapters(tmp_path / "cut-chapters.nproj")

    # 读到第一章末尾：第二章的人物还不存在。
    cut = ops.graph_query(disclosure_cut={"source_id": first, "start": 10_000})
    assert [node["name"] for node in cut["nodes"]] == ["林舟", "陈默"]

    # 读到第二章：全书可见。
    whole = ops.graph_query(disclosure_cut={"source_id": second, "start": 10_000})
    assert [node["name"] for node in whole["nodes"]] == ["林舟", "陈默", "老周"]
