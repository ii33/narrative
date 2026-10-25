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


def test_the_editor_sees_what_was_retracted(tmp_path: Path) -> None:
    ops = _project(tmp_path / "editor.sqlite3")
    ops.property_retract(["v2"], "读错了")

    payload = ops.node_editor("1")

    assert payload["node"]["name"] == "林舟"
    assert [event["name"] for event in payload["events"]] == ["交代年龄"]
    item = payload["properties"][0]
    rows = {row["id"]: row for row in item["records"]}
    assert rows["v2"]["retracted"] is True
    assert rows["v2"]["retraction_reason"] == "读错了"
    assert rows["v1"]["retracted"] is False


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
    assert item["retracted_count"] == 1
    assert item["record_count"] == 1
    assert [row["value"] for row in item["records"]] == ["二十九岁"]


def test_an_aspect_with_nothing_left_says_so_instead_of_vanishing(tmp_path: Path) -> None:
    """整条失效是一句答案，不能让它悄悄消失——否则看不见自己做过什么。"""
    ops = _project(tmp_path / "gone.sqlite3")
    ops.property_retract(["v1", "v2"], "两句都读错了")

    items = ops.property_node_summary("1")["properties"]
    assert len(items) == 1
    item = items[0]
    assert item["label"] == "年龄"
    assert item["retracted"] is True
    assert item["current_retracted"] is True
    # 值不再可见，但「撤了几条」还在。
    assert item["value"] is None
    assert item["record_count"] == 0
    assert item["retracted_count"] == 2
    assert item["records"] == []

    # 编辑器里仍然看得见这两条和原因。
    rows = ops.node_editor("1")["properties"][0]["records"]
    assert [(row["value"], row["retracted"]) for row in rows] == [
        ("二十九岁", True), ("三十岁", True),
    ]
    assert {row["retraction_reason"] for row in rows} == {"两句都读错了"}

