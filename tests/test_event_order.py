"""编号与阅读顺序：同类型内按编号排，跨类型按披露位置排。

编号是按 `(原文序, 字符偏移)` 发的，落在两个节点之间就发 `prev.0.k`（如 `E1.0.1`），
从不追加到末尾——所以同一类型里编号顺序就是阅读顺序，事件线、事件比较都直接比编号。
混合列表（人物和事件排在一起）编号之间没有先后，那边按 `(原文序, 披露位置)` 排。
"""

from __future__ import annotations

import json
from pathlib import Path

from narrative.browse_view import browse_view
from narrative.extraction import build_candidate, parse_nodes
from narrative.project_catalog import create_graph_project


def _project(tmp_path: Path):
    ops = create_graph_project(tmp_path / "order.nproj")
    sid = ops.import_source("第一章", "甲出场。乙出场。")
    return ops, sid


def _extract(ops, sid: str, text: str, names: list[str]) -> list[str]:
    """跑一次提取并接受，返回这次新建的节点编号。"""
    data = parse_nodes(
        json.dumps(
            {"nodes": [{"name": name, "type": "event", "quotes": [name]} for name in names],
             "properties": []},
            ensure_ascii=False,
        ),
        text,
    ) | {"source_id": sid, "source_hash": ops.source(sid)["hash"], "chapter": "第一章"}
    built = build_candidate(ops, data)
    ids = [obj["id"] for obj in built["objects"] if obj.get("node_type") == "event"]
    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )
    return ids


def test_a_node_found_between_two_others_gets_a_number_between_them(tmp_path: Path) -> None:
    """后补的事件插在中间，而不是排到末尾——编号因此仍是阅读顺序。"""
    ops = create_graph_project(tmp_path / "order.nproj")
    text = "甲出场。乙出场。丙出场。"
    sid = ops.import_source("第一章", text)

    # 第一次漏掉中间那个，只读出首尾。
    assert _extract(ops, sid, text, ["甲出场", "丙出场"]) == ["E1", "E2"]

    # 第二次补上中间那个：编号落在 E1 与 E2 之间，事件线顺序也跟着对。
    assert _extract(ops, sid, text, ["乙出场"]) == ["E1.0.1"]
    names = [row["name"] for row in ops.graph_event_lines()["events"]]
    assert names == ["甲出场", "乙出场", "丙出场"]


def test_the_event_line_and_comparison_follow_the_number(tmp_path: Path) -> None:
    """事件线、事件比较都按编号；人工挪过披露位置也不改这个顺序。"""
    ops, sid = _project(tmp_path)
    _extract(ops, sid, "甲出场。乙出场。", ["甲出场", "乙出场"])
    assert ops.graph_event_compare("E1", "E2")["relation"] == "before"

    snapshot = ops.graph_snapshot()
    node = next(item for item in snapshot["nodes"] if item["id"] == "E1")
    moved = dict(node) | {"disclosure": {"source_id": sid, "start": 7}}
    revision = ops.graph_project_info()["revision"]
    ops.change_accept(
        ops.change_propose([moved], [], revision, source="correction", note="挪到后面"), 1
    )

    # 编号没变，顺序也不变：同类型里编号就是顺序。
    assert [row["id"] for row in ops.graph_event_lines()["events"]] == ["E1", "E2"]
    result = ops.graph_event_compare("E1", "E2")
    assert result["relation"] == "before"
    assert result["basis"] == "disclosure"

    # 混合列表没有跨类型的编号先后，那边按披露位置排，所以这里换成了乙在前。
    mixed = browse_view(
        ops.graph_snapshot(), granularity="event", cut_id=None, node_type=None,
        query="", related_only=False, time_filter=None,
    )
    assert [node["id"] for node in mixed["nodes"]] == ["E2", "E1"]


def test_a_single_type_list_is_in_number_order(tmp_path: Path) -> None:
    """按类型筛过之后，列表顺序就是编号顺序。"""
    ops, sid = _project(tmp_path)
    _extract(ops, sid, "甲出场。乙出场。", ["甲出场", "乙出场"])
    events = browse_view(
        ops.graph_snapshot(), granularity="event", cut_id=None, node_type="event",
        query="", related_only=False, time_filter=None,
    )
    assert [node["id"] for node in events["nodes"]] == ["E1", "E2"]
