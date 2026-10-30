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


def test_a_storyline_spans_its_scenes(tmp_path: Path) -> None:
    """大事件不是一个点：它占的是所有场景合起来的那一段阅读范围。"""
    from narrative.extraction import build_candidate, parse_nodes
    from narrative.project_catalog import create_graph_project

    ops = create_graph_project(tmp_path / "span.nproj")
    text = "林舟推门。老周递钥匙。林舟收下。"
    data = parse_nodes(json.dumps({
        "nodes": [
            {"name": "接手杂货铺", "type": "storyline", "quotes": ["老周递钥匙"], "aliases": []},
            {"name": "门口", "type": "scene", "quotes": ["林舟推门"], "aliases": [],
             "storyline": "接手杂货铺"},
            {"name": "柜台", "type": "scene", "quotes": ["老周递钥匙"], "aliases": [],
             "storyline": "接手杂货铺"},
            {"name": "林舟推门进来", "type": "event", "quotes": ["林舟推门"], "aliases": [],
             "participants": ["林舟"], "scene": "门口"},
            {"name": "老周递钥匙", "type": "event", "quotes": ["老周递钥匙"], "aliases": [],
             "participants": ["老周", "林舟"], "scene": "柜台"},
            {"name": "林舟收下钥匙", "type": "event", "quotes": ["林舟收下"], "aliases": [],
             "participants": ["林舟"], "scene": "柜台"},
        ],
        "properties": [],
    }, ensure_ascii=False), text) | {
        "source_id": (sid := ops.import_source("第一章", text)),
        "source_hash": ops.source(sid)["hash"],
        "chapter": "第一章",
    }
    built = build_candidate(ops, data)
    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )

    spans = ops.graph_event_lines()["storylines"]
    assert len(spans) == 1
    span = spans[0]
    assert span["scene_count"] == 2
    assert span["event_count"] == 3
    # 起点是它第一个场景的第一个事件，终点是最后一个事件。
    names = {row["id"]: row["name"] for row in ops.graph_event_lines()["events"]}
    assert names[span["first"]] == "林舟推门进来"
    assert names[span["last"]] == "林舟收下钥匙"

    # 大事件自己的披露位置 = 第一个场景的位置，不是它随手引的那句。
    story = next(n for n in ops.graph_snapshot()["nodes"] if n["node_type"] == "storyline")
    assert story["disclosure"]["start"] == 0


def test_a_node_lists_the_time_points_it_contains(tmp_path: Path) -> None:
    """点一个时间点，能看到它下面那一级：大事件→场景→事件。"""
    from narrative.extraction import build_candidate, parse_nodes
    from narrative.project_catalog import create_graph_project

    ops = create_graph_project(tmp_path / "children.nproj")
    text = "林舟推门。老周递钥匙。"
    data = parse_nodes(json.dumps({
        "nodes": [
            {"name": "接手杂货铺", "type": "storyline", "quotes": ["老周递钥匙"],
             "aliases": [], "provisional_name": "有人来了"},
            {"name": "门口", "type": "scene", "quotes": ["林舟推门"], "aliases": [],
             "storyline": "接手杂货铺"},
            {"name": "林舟推门进来", "type": "event", "quotes": ["林舟推门"], "aliases": [],
             "participants": ["林舟"], "scene": "门口"},
            {"name": "老周递钥匙", "type": "event", "quotes": ["老周递钥匙"], "aliases": [],
             "participants": ["老周", "林舟"], "scene": "门口"},
        ],
        "properties": [],
    }, ensure_ascii=False), text) | {
        "source_id": (sid := ops.import_source("第一章", text)),
        "source_hash": ops.source(sid)["hash"],
        "chapter": "第一章",
    }
    built = build_candidate(ops, data)
    ops.change_accept(
        ops.change_propose(
            built["objects"], built["property_records"], built["revision"], source="model"
        ),
        1,
    )

    nodes = {node["name"]: node for node in ops.graph_snapshot()["nodes"]}
    story = nodes["接手杂货铺"]
    assert story["provisional_name"] == "有人来了"

    # 大事件列出它下面的场景，场景列出它下面的事件。
    story_children = ops.property_node_summary(story["id"])["children"]
    assert [row["type_label"] for row in story_children] == ["场景"]
    scene_children = ops.property_node_summary(story_children[0]["id"])["children"]
    assert [row["name"] for row in scene_children] == ["林舟推门进来", "老周递钥匙"]
