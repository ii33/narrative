"""节点编辑器要用的两件事：撤回（设为失效）与整份历史。

撤回不删行：浏览页不再显示它，编辑器仍然看得到它和撤回原因。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from narrative.project_catalog import create_graph_project


def _project(path: Path):
    ops = create_graph_project(path)
    source = ops.import_source("第一章", "林舟今年二十九岁，脸色黝黑。")
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "交代年龄"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    ops.property_accept(
        ops.property_propose(
            [
                {"kind": "property_definition", "id": "age", "value_type": "string",
                 "cardinality": "one", "persistence": "until_changed"},
                {"kind": "property_value", "id": "v1", "node_id": "1", "property_id": "age",
                 "value": "二十九岁", "event_id": "2",
                 "disclosure": {"source_id": source, "start": 2}},
                {"kind": "property_value", "id": "v2", "node_id": "1", "property_id": "age",
                 "value": "三十岁", "event_id": "2",
                 "disclosure": {"source_id": source, "start": 6}},
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )
    return ops


def test_retracting_a_record_hides_it_from_reading(tmp_path: Path) -> None:
    ops = _project(tmp_path / "retract.sqlite3")

    result = ops.property_retract(["v2"], "三十岁那句读错了")
    assert result["retracted"] == ["v2"]

    item = ops.property_node_summary("1")["properties"][0]
    # 撤回之后，当前值回到还站得住的那条。
    assert [row["value"] for row in item["records"]] == ["二十九岁"]
    assert item["value"] == "二十九岁"


def test_the_editor_lists_only_what_still_stands(tmp_path: Path) -> None:
    """编辑器回答「现在是什么」：失效的披露不进列表，但记录一条都没删。"""
    ops = _project(tmp_path / "editor.sqlite3")
    ops.property_retract(["v2"], "读错了")

    payload = ops.node_editor("1")

    assert payload["node"]["name"] == "林舟"
    assert [event["name"] for event in payload["events"]] == ["交代年龄"]
    item = payload["properties"][0]
    assert [(row["id"], row["value"]) for row in item["records"]] == [("v1", "二十九岁")]
    # 撤回不是删除：记录还在库里，带着原因，只是不再进编辑器。
    stored = {
        record["id"]: record for record in ops.property_records(include_retracted=True)
    }
    assert stored["v2"]["value"] == "三十岁"


def test_an_aspect_with_nothing_left_is_not_listed(tmp_path: Path) -> None:
    ops = _project(tmp_path / "empty.sqlite3")
    ops.property_retract(["v1", "v2"], "整条读错了")

    assert ops.node_editor("1")["properties"] == []
    # 浏览区的「全部」仍然看得到它说过什么、为什么不算数。
    everything = ops.property_node_summary("1", include_invalid=True)["properties"]
    assert [row["value"] for row in everything[0]["records"]] == ["二十九岁", "三十岁"]
    assert [row["valid"] for row in everything[0]["records"]] == [False, False]


def test_retraction_needs_a_reason_and_a_real_record(tmp_path: Path) -> None:
    ops = _project(tmp_path / "bad-retract.sqlite3")

    with pytest.raises(ValueError):
        ops.property_retract(["v1"], "   ")
    with pytest.raises(ValueError):
        ops.property_retract(["nope"], "读错了")
    # 定义是结构，不是披露：不能靠撤回删掉它。
    with pytest.raises(ValueError):
        ops.property_retract(["age"], "不要这个属性")


def test_retracting_twice_is_a_no_op(tmp_path: Path) -> None:
    ops = _project(tmp_path / "twice.sqlite3")
    first = ops.property_retract(["v1"], "读错了")
    second = ops.property_retract(["v1"], "再撤一次")

    assert second["retracted"] == []
    assert second["revision"] == first["revision"]


def test_a_partial_retraction_keeps_the_line_and_counts_the_loss(tmp_path: Path) -> None:
    """撤回一条之后，这一方面仍然站得住：不出现空行，但要说撤了几条。"""
    ops = _project(tmp_path / "partial.sqlite3")
    ops.property_retract(["v2"], "读错了")

    item = ops.property_node_summary("1")["properties"][0]
    assert item["retracted"] is False
    assert item["invalid_count"] == 1
    assert item["record_count"] == 1
    assert [row["value"] for row in item["records"]] == ["二十九岁"]


def test_an_aspect_with_nothing_left_is_gone_from_the_pane(tmp_path: Path) -> None:
    """整条失效之后，「最新」不再列它：这一刻它什么都没有，空行只会像还能编辑。"""
    ops = _project(tmp_path / "gone.sqlite3")
    ops.property_retract(["v1", "v2"], "两句都读错了")

    assert ops.property_node_summary("1")["properties"] == []
    assert ops.node_editor("1")["properties"] == []

    # 记录没有被删掉：「全部」里两条都在，逐条标失效并带着原因。
    union = ops.property_node_summary("1", include_invalid=True)["properties"]
    assert len(union) == 1
    assert union[0]["retracted"] is True
    assert [(row["value"], row["valid"]) for row in union[0]["records"]] == [
        ("二十九岁", False), ("三十岁", False),
    ]
    assert {row["invalid_reason"] for row in union[0]["records"]} == {"两句都读错了"}


def test_a_superseded_record_can_be_taken_back_at_an_earlier_cut(tmp_path: Path) -> None:
    """更早的阅读位置上，被取代的记录仍是读者看到的值：这时撤它说的是「从来不算」。

    只有「从事件 3 起不算」那一条时，站在事件 2 上点失效会被当成重复而静默跳过，
    看起来就是「失效没用」。
    """
    ops = create_graph_project(tmp_path / "earlier.sqlite3")
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "交代年龄"},
                {"kind": "node", "id": "3", "node_type": "event", "name": "过了几年"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    ops.property_accept(
        ops.property_propose(
            [
                {"kind": "property_definition", "id": "age", "value_type": "string",
                 "cardinality": "one", "persistence": "until_changed"},
                {"kind": "property_value", "id": "v1", "node_id": "1",
                 "property_id": "age", "value": "二十九岁", "event_id": "2"},
                {"kind": "property_value", "id": "v2", "node_id": "1",
                 "property_id": "age", "value": "三十岁", "event_id": "3"},
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )
    # 从事件 3 起，二十九岁不再是状态。
    ops.property_supersede(
        [{"target_id": "v1", "event_id": "3", "reason": "被后一条披露取代"}]
    )

    # 站在事件 2：读者看到的还是二十九岁。
    item = ops.property_node_summary("1", cut="2")["properties"][0]
    assert [row["value"] for row in item["records"]] == ["二十九岁"]

    # 在这里撤它，说的是「从来不算」——这是另一句话，要写得进去。
    assert ops.property_retract(["v1"], "这句读错了")["retracted"] == ["v1"]
    assert ops.property_node_summary("1", cut="2")["properties"] == []

    # 再说一遍就真的没意义了。
    assert ops.property_retract(["v1"], "再说一次")["retracted"] == []




def _three_events(path: Path):
    ops = create_graph_project(path)
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "交代年龄"},
                {"kind": "node", "id": "3", "node_type": "event", "name": "过了几年"},
                {"kind": "node", "id": "4", "node_type": "event", "name": "又过了几年"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    ops.property_accept(
        ops.property_propose(
            [
                {"kind": "property_definition", "id": "age", "value_type": "string",
                 "cardinality": "one", "persistence": "until_changed"},
                {"kind": "property_value", "id": "v1", "node_id": "1",
                 "property_id": "age", "value": "二十九岁", "event_id": "2"},
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )
    return ops


def _retraction_id(ops) -> str:
    return next(
        record["id"] for record in ops.property_records(include_retracted=True)
        if record.get("kind") == "property_retraction"
    )


def test_undoing_a_retraction_counts_again_from_there(tmp_path: Path) -> None:
    """撤销失效不擦历史：之前那段仍然没有这个值，从撤销的位置起才重新算。"""
    ops = _three_events(tmp_path / "restore.sqlite3")
    ops.property_retract(["v1"], "读错了")
    assert ops.property_node_summary("1", cut="2")["properties"] == []

    result = ops.property_restore([_retraction_id(ops)], "其实没读错", event_id="4")
    assert result["restored"] == [_retraction_id(ops)]

    # 撤销落在事件 4：事件 2、3 上仍然没有这个值。
    assert ops.property_node_summary("1", cut="2")["properties"] == []
    assert ops.property_node_summary("1", cut="3")["properties"] == []
    # 从事件 4 起，它又算数了。
    item = ops.property_node_summary("1", cut="4")["properties"][0]
    assert [row["value"] for row in item["records"]] == ["二十九岁"]
    # 「全部」里那条记录也回到有效。
    everything = ops.property_node_summary("1", include_invalid=True)["properties"][0]
    assert [row["valid"] for row in everything["records"]] == [True]


def test_a_restoration_cannot_be_undone(tmp_path: Path) -> None:
    """没有撤销叠撤销：恢复记录不能被撤，同一条撤回也不能撤销两次。"""
    ops = _three_events(tmp_path / "no-chain.sqlite3")
    ops.property_retract(["v1"], "读错了")
    ops.property_restore([_retraction_id(ops)], "其实没读错", event_id="4")

    restoration = next(
        record for record in ops.property_records(include_retracted=True)
        if record.get("kind") == "property_restoration"
    )
    with pytest.raises(ValueError):
        ops.property_retract([restoration["id"]], "把撤销再撤掉")
    # 同一条撤回撤销第二次什么也不做。
    assert ops.property_restore(
        [_retraction_id(ops)], "再来一次", event_id="4"
    )["restored"] == []


def test_a_restoration_needs_a_position_and_a_reason(tmp_path: Path) -> None:
    ops = _three_events(tmp_path / "restore-bad.sqlite3")
    ops.property_retract(["v1"], "读错了")
    with pytest.raises(ValueError):
        ops.property_restore([_retraction_id(ops)], "没有位置", event_id="")
    with pytest.raises(ValueError):
        ops.property_restore([_retraction_id(ops)], "   ", event_id="4")
    with pytest.raises(ValueError):
        ops.property_restore(["v1"], "指向取值记录", event_id="4")


def _three_events(path: Path):
    ops = create_graph_project(path)
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "交代年龄"},
                {"kind": "node", "id": "3", "node_type": "event", "name": "过了几年"},
                {"kind": "node", "id": "4", "node_type": "event", "name": "又过了几年"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    ops.property_accept(
        ops.property_propose(
            [
                {"kind": "property_definition", "id": "age", "value_type": "string",
                 "cardinality": "one", "persistence": "until_changed"},
                {"kind": "property_value", "id": "v1", "node_id": "1",
                 "property_id": "age", "value": "二十九岁", "event_id": "2"},
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )
    return ops


def _retraction_id(ops) -> str:
    return next(
        record["id"] for record in ops.property_records(include_retracted=True)
        if record.get("kind") == "property_retraction"
    )


