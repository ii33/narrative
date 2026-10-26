"""属性改名：一个方面换个名字，取值不改写，定义跟着搬。

改名回答的是「这个方面叫什么」，不是「这两个名字是不是同一个方面」——后者是归并。
所以新名字不需要已经存在（造出来正是目的），而落到另一个方面的名字上会被拒绝：
静默合并会让读者看不出哪个方面吞了哪个。改名和归并一样是读时映射，
撤回那条记录就恢复原名，已记下的披露一条都不动。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from narrative import property_core
from narrative.project_catalog import create_graph_project
from narrative.property_store import supersede_plan, supersede_records


def _project(path: Path):
    ops = create_graph_project(path)
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "person", "name": "老周"},
                {"kind": "node", "id": "4", "node_type": "event", "name": "开场"},
                {"kind": "node", "id": "5", "node_type": "event", "name": "后来又过了些日子"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    return ops


def _definitions(*property_ids: str) -> list[dict]:
    return [
        {"kind": "property_definition", "id": property_id, "value_type": "string",
         "cardinality": "one", "persistence": "until_changed"}
        for property_id in property_ids
    ]


def _value(record_id: str, node_id: str, property_id: str, value, event_id: str = "4") -> dict:
    return {
        "kind": "property_value", "id": record_id, "node_id": node_id,
        "property_id": property_id, "value": value, "event_id": event_id,
    }


def _accept(ops, records: list[dict]) -> None:
    ops.property_accept(
        ops.property_propose(
            records, ops.graph_project_info()["revision"], source="manual"
        ),
        1,
    )


def _household(ops) -> None:
    """林舟：身份是总经理，自小家贫。两者都在开场披露。"""
    _accept(ops, _definitions("identity", "family_background") + [
        _value("v1", "1", "identity", "贸易公司总经理"),
        _value("v2", "1", "family_background", "贫寒"),
    ])


def test_a_rename_reads_old_records_under_the_new_name(tmp_path: Path) -> None:
    ops = _project(tmp_path / "rename.sqlite3")
    _household(ops)

    result = ops.property_rename(["family_background"], "出身")
    assert result["to_id"] == "出身"
    assert result["renamed"] == ["family_background"]

    rows = {item["property_id"]: item for item in ops.property_node_summary("1")["properties"]}
    # 新名字下就是同一份披露：不是新建了一条记录，而是换个名字读。
    assert rows["出身"]["value"] == "贫寒"
    assert rows["出身"]["label"] == "出身"
    # 定义跟着搬：新名字下也认得出这是同一个方面、同一种取值形态。
    assert rows["出身"]["value_type"] == "string"
    # 记录本身没有被改写，旧名字还在库里。
    stored = [
        record["property_id"] for record in ops.property_records()
        if record["kind"] == "property_value"
    ]
    assert "family_background" in stored
    assert "出身" not in stored
    # 按新名字查状态，看到的是同一条记录。
    assert ops.property_state("1", "出身")["state"] == {"kind": "known", "value": "贫寒"}


def test_a_rename_does_not_touch_other_aspects(tmp_path: Path) -> None:
    ops = _project(tmp_path / "others.sqlite3")
    _household(ops)
    ops.property_rename(["family_background"], "出身")

    rows = {item["property_id"]: item["value"] for item in ops.property_node_summary("1")["properties"]}
    assert rows == {"identity": "贸易公司总经理", "出身": "贫寒"}


def test_renaming_a_line_moves_every_spelling(tmp_path: Path) -> None:
    ops = _project(tmp_path / "line.sqlite3")
    _accept(ops, _definitions("cultivation", "cultivation_stage") + [
        _value("v1", "2", "cultivation", "灵寂期"),
        _value("v2", "2", "cultivation_stage", "辟谷期"),
    ])

    # 一行里有两个名字时，改的是整行：只改一个，下一条披露就会把旧名字带回来。
    ops.property_rename(["cultivation", "cultivation_stage"], "修为")

    item = ops.property_node_summary("2")["properties"][0]
    assert item["property_ids"] == ["修为"]
    assert item["label"] == "修为"
    assert [row["value"] for row in item["records"]] == ["灵寂期", "辟谷期"]


def test_renaming_twice_follows_the_chain(tmp_path: Path) -> None:
    ops = _project(tmp_path / "chain.sqlite3")
    _household(ops)
    ops.property_rename(["family_background"], "出身")
    # 改过的名字还能再改：链条要一路跟到底。
    ops.property_rename(["出身"], "家世")

    rows = {item["property_id"] for item in ops.property_node_summary("1")["properties"]}
    assert "家世" in rows
    assert ops.property_aliases()["family_background"] == "家世"
    assert ops.property_state("1", "家世")["state"]["value"] == "贫寒"


def test_retracting_a_rename_restores_the_old_name(tmp_path: Path) -> None:
    ops = _project(tmp_path / "undo.sqlite3")
    _household(ops)
    ops.property_rename(["family_background"], "出身")

    rename_record = next(
        record for record in ops.property_records() if record["kind"] == "property_rename"
    )
    _accept(ops, [{
        "kind": "property_retraction", "id": "undo-rename",
        "target_id": rename_record["id"], "reason": "原名更准确",
    }])

    rows = {item["property_id"]: item["label"] for item in ops.property_node_summary("1")["properties"]}
    assert rows["family_background"] == "家境"
    assert "出身" not in rows


def test_a_rename_refuses_a_name_that_another_aspect_owns(tmp_path: Path) -> None:
    ops = _project(tmp_path / "clash.sqlite3")
    _household(ops)

    # 目标是另一个方面的名字：那是合并，不该悄悄发生。
    with pytest.raises(ValueError, match="合并"):
        ops.property_rename(["family_background"], "identity")
    with pytest.raises(ValueError, match="新旧名字相同"):
        ops.property_rename(["family_background"], "family_background")
    with pytest.raises(ValueError, match="不存在"):
        ops.property_rename(["nope"], "出身")
    with pytest.raises(ValueError):
        ops.property_rename(["family_background"], "   ")


def test_a_rename_onto_one_of_the_row_names_is_allowed(tmp_path: Path) -> None:
    """整行两个名字时，改到其中一个上就是把这个方面统一成那个名字。"""
    ops = _project(tmp_path / "consolidate.sqlite3")
    _accept(ops, _definitions("cultivation", "cultivation_stage") + [
        _value("v1", "2", "cultivation", "灵寂期"),
        _value("v2", "2", "cultivation_stage", "辟谷期"),
    ])

    ops.property_rename(["cultivation", "cultivation_stage"], "cultivation")
    item = ops.property_node_summary("2")["properties"][0]
    assert item["property_ids"] == ["cultivation"]


def test_the_audit_sees_one_name_after_a_rename(tmp_path: Path) -> None:
    ops = _project(tmp_path / "audit.sqlite3")
    _household(ops)
    ops.property_rename(["family_background"], "出身")

    codes = [issue["code"] for issue in ops.property_audit()["issues"]]
    # 改名之后不该再报「定义缺失」，也不该把旧名字算成没人用的定义。
    assert "definition_missing" not in codes
    assert "definition_unused" not in codes
    assert "name_variant" not in codes


def test_a_new_value_under_the_new_name_supersedes_the_old_one(tmp_path: Path) -> None:
    """改名之后补的披露要和改名前的接上，否则同一条线会出现两个当前值。"""
    ops = _project(tmp_path / "supersede.sqlite3")
    _household(ops)
    ops.property_rename(["family_background"], "出身")

    fresh = [_value("v3", "1", "出身", "小康", event_id="5")]
    plan = supersede_plan(ops.property_records(), fresh)
    assert [item["target_id"] for item in plan] == ["v2"]
    _accept(ops, fresh + supersede_records(ops.property_records(), fresh))

    # 「最新」只剩一个当前值；失效的那条要切到「全部」才看得到。
    latest = next(
        row for row in ops.property_node_summary("1")["properties"]
        if row["property_id"] == "出身"
    )
    assert latest["value"] == "小康"
    assert [row["value"] for row in latest["records"]] == ["小康"]

    # 被取代的那条不再显示，但记录还在库里。
    stored = [
        record["value"] for record in ops.property_records(include_retracted=True)
        if record.get("kind") == "property_value"
    ]
    assert {"贫寒", "小康"} <= set(stored)


def test_the_new_name_validates_as_the_same_aspect() -> None:
    """新名字下写的记录要能过校验，否则改名之后补录就得用回旧名字。"""
    snapshot = {
        "nodes": [
            {"id": "1", "kind": "node", "node_type": "person", "name": "林舟"},
            {"id": "4", "kind": "node", "node_type": "event", "name": "开场"},
        ],
        "sources": [],
    }
    accepted = _definitions("family_background") + [
        _value("v1", "1", "family_background", "贫寒"),
        {"kind": "property_rename", "id": "r1", "from_id": "family_background",
         "to_id": "出身"},
    ]
    issues = property_core.validate_records(
        [_value("v2", "1", "出身", "小康", event_id="4")], snapshot, accepted
    )
    assert issues == []


def test_a_rename_needs_a_name_it_starts_from() -> None:
    snapshot = {
        "nodes": [
            {"id": "1", "kind": "node", "node_type": "person", "name": "林舟"},
            {"id": "4", "kind": "node", "node_type": "event", "name": "开场"},
        ],
        "sources": [],
    }
    issues = property_core.validate_records(
        [{"kind": "property_rename", "id": "r1", "from_id": "nope", "to_id": "出身"}],
        snapshot,
        _definitions("family_background"),
    )
    assert [issue["code"] for issue in issues] == ["RENAME"]


def test_a_rename_cannot_form_a_cycle() -> None:
    snapshot = {
        "nodes": [
            {"id": "1", "kind": "node", "node_type": "person", "name": "林舟"},
            {"id": "4", "kind": "node", "node_type": "event", "name": "开场"},
        ],
        "sources": [],
    }
    accepted = _definitions("a", "b") + [
        {"kind": "property_rename", "id": "r1", "from_id": "a", "to_id": "b"},
    ]
    issues = property_core.validate_records(
        [{"kind": "property_rename", "id": "r2", "from_id": "b", "to_id": "a"}],
        snapshot,
        accepted,
    )
    assert [issue["code"] for issue in issues] == ["RENAME"]


def test_the_inbound_side_reads_the_new_name(tmp_path: Path) -> None:
    """反向引用用的是同一份记录，名字也得跟着改。"""
    ops = _project(tmp_path / "inbound.sqlite3")
    _accept(ops, [
        {"kind": "property_definition", "id": "leader", "value_type": "node",
         "cardinality": "one", "persistence": "until_changed"},
        _value("v1", "2", "leader", "1"),
    ])
    ops.property_rename(["leader"], "师父")

    inbound = ops.property_node_summary("1")["inbound"]
    assert [(row["property_id"], row["property_label"]) for row in inbound] == [
        ("师父", "师父")
    ]
