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
