"""浏览投影：过滤、计数、步骤表和相关节点都在后端算。

界面只提交条件、渲染结果，所以这些规则必须能在这里单独验证。
"""

from __future__ import annotations

from pathlib import Path

from narrative.browse_view import (
    browse_view,
    cut_for,
    related_nodes,
    step_for_granularity,
    steps,
)
from narrative.project_catalog import create_graph_project


def _project(path: Path):
    ops = create_graph_project(path)
    source = ops.import_source("第一章", "林舟推开房门，看见陈默坐在沙发上。老周后来出现在门口，两个人都愣住了。")
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "source_order", "id": "order_1", "source_id": source,
                 "ordinal": 1, "context_id": "canon"},
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟",
                 "disclosure": {"source_id": source, "start": 0}},
                {"kind": "node", "id": "2", "node_type": "person", "name": "陈默",
                 "aliases": ["陈总"], "disclosure": {"source_id": source, "start": 10}},
                {"kind": "node", "id": "3", "node_type": "person", "name": "老周",
                 "disclosure": {"source_id": source, "start": 20}},
                {"kind": "node", "id": "4", "node_type": "scene", "name": "房间冲突",
                 "events": ["5", "6"], "disclosure": {"source_id": source, "start": 0}},
                {"kind": "node", "id": "5", "node_type": "event", "name": "林舟进门",
                 "participants": ["1", "2"], "disclosure": {"source_id": source, "start": 0}},
                {"kind": "node", "id": "6", "node_type": "event", "name": "两人对峙",
                 "participants": ["1"], "disclosure": {"source_id": source, "start": 10}},
                {"kind": "node", "id": "7", "node_type": "scene", "name": "老周现身",
                 "events": ["8"], "disclosure": {"source_id": source, "start": 20}},
                {"kind": "node", "id": "8", "node_type": "event", "name": "老周出场",
                 "participants": ["3"], "disclosure": {"source_id": source, "start": 20}},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    return ops, ops.graph_snapshot()


def test_steps_follow_the_granularity(tmp_path: Path) -> None:
    _ops, snapshot = _project(tmp_path / "steps.nproj")

    assert [step["id"] for step in steps(snapshot, "scene")] == ["4", "7"]
    assert [step["id"] for step in steps(snapshot, "event")] == ["5", "6", "8"]
    # 未知粒度按默认的「场景」处理，不会抛错。
    assert [step["id"] for step in steps(snapshot, "chapter")] == ["4", "7"]


def test_a_scene_cuts_where_its_last_event_is(tmp_path: Path) -> None:
    _ops, snapshot = _project(tmp_path / "anchor.nproj")

    cut = cut_for(snapshot, "4")
    # 场景 4 的最后一个事件是 6：读完场景＝读到事件 6。
    assert cut["event_id"] == "6"
    assert cut["step_id"] == "4"
    assert cut["start"] == 10

    # 事件粒度下，切点就是事件自己。
    assert cut_for(snapshot, "5")["event_id"] == "5"
    assert cut_for(snapshot, "nope") is None


def test_related_nodes_of_a_scene_include_its_events(
    tmp_path: Path,
) -> None:
    _ops, snapshot = _project(tmp_path / "related.nproj")

    # 场景自己 + 它包含的两个事件 + 两个事件里出现过的人。
    # 事件属于场景，所以「只看这一步的相关节点」不能把它们筛掉。
    assert related_nodes(snapshot, "4") == ["1", "2", "4", "5", "6"]
    # 单个事件：自己 + 参与者 + 它所属的场景。
    assert related_nodes(snapshot, "5") == ["1", "2", "4", "5"]
    assert related_nodes(snapshot, "nope") == []


def test_the_list_obeys_the_cut_only_when_asked(tmp_path: Path) -> None:
    _ops, snapshot = _project(tmp_path / "cut.nproj")

    narrowed = browse_view(snapshot, cut_id="4", time_filter=True)
    # 读到事件 6（位置 10）：位置 20 的老周和第二个场景还没出现。
    # 列表按披露位置排，不按编号：编号各类型自成一套，跨类型没有先后。
    assert [node["id"] for node in narrowed["nodes"]] == ["1", "4", "5", "2", "6"]

    whole = browse_view(snapshot, cut_id="4", time_filter=False)
    assert len(whole["nodes"]) == 8
    # 不收窄也照样给出切点：属性区永远跟着它。
    assert whole["cut"]["event_id"] == "6"


def test_counts_follow_the_other_conditions_but_not_the_type_filter(
    tmp_path: Path,
) -> None:
    _ops, snapshot = _project(tmp_path / "counts.nproj")

    by_type = browse_view(snapshot, node_type="person")
    assert [node["name"] for node in by_type["nodes"]] == ["林舟", "陈默", "老周"]
    # 侧栏本身就是类型筛选，所以计数仍然列出各类型有多少。
    assert by_type["counts"] == {"event": 3, "person": 3, "scene": 2}

    # 相关节点把范围收到这一步，计数跟着收——否则数字会和列表对不上。
    related = browse_view(snapshot, cut_id="4", related_only=True)
    assert {node["id"] for node in related["nodes"]} == {"1", "2", "4", "5", "6"}
    assert related["counts"] == {"event": 2, "person": 2, "scene": 1}

    # 搜索也一样：计数说的是"这些条件之下有什么"。
    searched = browse_view(snapshot, query="陈")
    assert [node["name"] for node in searched["nodes"]] == ["陈默"]
    assert searched["counts"] == {"person": 1}


def test_query_matches_aliases(tmp_path: Path) -> None:
    _ops, snapshot = _project(tmp_path / "alias.nproj")

    assert [node["id"] for node in browse_view(snapshot, query="陈总")["nodes"]] == ["2"]


def test_switching_granularity_keeps_the_reading_position(tmp_path: Path) -> None:
    """场景的切点是它的最后一个事件，事件的切点是包含它的场景——同一个位置，两种说法。"""
    _ops, snapshot = _project(tmp_path / "alt.nproj")

    assert step_for_granularity(snapshot, "4", "event") == "6"
    assert step_for_granularity(snapshot, "6", "scene") == "4"
    # 已经是目标粒度时就是它自己。
    assert step_for_granularity(snapshot, "4", "scene") == "4"
    assert step_for_granularity(snapshot, "6", "event") == "6"
    # 停在「最新」时两边都是「最新」，不该凭空选一个步骤。
    assert step_for_granularity(snapshot, "", "event") == ""
    assert step_for_granularity(snapshot, "", "scene") == ""
    # 不认识的编号就是没有对应步骤。
    assert step_for_granularity(snapshot, "99", "scene") == ""


def test_an_event_outside_every_scene_lands_on_the_scene_it_finished() -> None:
    """换粒度不能把读者丢到全书末尾：没有所属场景就退到已经读完的那个场景。"""
    snapshot = {
        "nodes": [
            {"id": "1", "node_type": "scene", "name": "开场", "events": ["2"]},
            {"id": "2", "node_type": "event", "name": "进门"},
            {"id": "3", "node_type": "event", "name": "幕间的独白"},
        ]
    }
    assert step_for_granularity(snapshot, "3", "scene") == "1"


def test_the_payload_carries_both_granularities_of_the_cut(tmp_path: Path) -> None:
    ops, _snapshot = _project(tmp_path / "cutalt.nproj")

    view = browse_view(ops.graph_snapshot(), cut_id="4")
    assert view["cut_alt"] == {"scene": "4", "event": "6"}
    assert browse_view(ops.graph_snapshot())["cut_alt"] == {"scene": "", "event": ""}
