"""属性分类：按对象类型给默认大类，具体属性保持开放，分类随时可调。

分类回答的是「这句话在说对象的哪个方面」，与 scope 回答的「这是谁的记录」
各管一件事。分类只决定显示位置：取值、披露顺序、原文引文都不因为移动分类而改变。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from narrative import property_categories
from narrative.project_catalog import create_graph_project


def _project(path: Path):
    ops = create_graph_project(path)
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "person", "name": "老周"},
                {"kind": "node", "id": "3", "node_type": "item", "name": "铜钥匙"},
                {"kind": "node", "id": "4", "node_type": "event", "name": "开场"},
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


def _value(record_id: str, node_id: str, property_id: str, value, **extra) -> dict:
    return {
        "kind": "property_value", "id": record_id, "node_id": node_id,
        "property_id": property_id, "value": value, "event_id": "4", **extra,
    }


# -- vocabulary ------------------------------------------------------------

def test_every_node_type_ends_with_the_fallback_category() -> None:
    for node_type, values in property_categories.default_vocabulary().items():
        assert values[-1] == property_categories.FALLBACK, node_type
        assert len(values) == len(set(values)), node_type


def test_saved_vocabulary_replaces_one_type_and_keeps_其他_last() -> None:
    merged = property_categories.normalize_vocabulary({"person": ["其他", "外貌", "外貌", "  "]})
    assert merged["person"] == ["外貌", property_categories.FALLBACK]
    # 没有写到的类型仍用默认表。
    assert merged["item"] == property_categories.DEFAULT_CATEGORIES["item"]


def test_malformed_vocabulary_is_rejected_on_save() -> None:
    with pytest.raises(ValueError):
        property_categories.validate_vocabulary({"dragon": ["外貌"]})
    with pytest.raises(ValueError):
        property_categories.validate_vocabulary({"person": ["外貌", "外貌"]})
    with pytest.raises(ValueError):
        property_categories.validate_vocabulary({"person": ["  "]})
    with pytest.raises(ValueError):
        property_categories.validate_vocabulary({})


# -- suggestion ------------------------------------------------------------

def test_property_names_map_to_the_category_of_the_object_type() -> None:
    assert property_categories.suggest("height", "person") == ("外貌", "name")
    assert property_categories.suggest("identity", "person") == ("身份与经历", "name")
    assert property_categories.suggest("holder", "item") == ("归属与持有", "name")
    # 同一个名字在不同类型下可以落到不同大类，也可以完全推不出来。
    assert property_categories.suggest("holder", "location") == (
        property_categories.FALLBACK, "fallback"
    )


def test_a_generic_name_lets_the_value_decide() -> None:
    cases = [
        ("脸色铁青", "外貌"),
        ("光着身体，衣物全部化为灰烬", "外貌"),
        ("心急如火", "心理与意图"),
        ("身体慢慢地冒出金光", "能力与身体状态"),
    ]
    for value, expected in cases:
        assert property_categories.suggest("state", "person", value) == (expected, "value")


def test_unclassifiable_records_land_in_其他_instead_of_being_refused() -> None:
    assert property_categories.suggest("state", "person", "一无所有") == (
        property_categories.FALLBACK, "fallback"
    )


def test_a_hint_that_the_project_removed_falls_back() -> None:
    config = {"person": ["外貌", "其他"]}
    # identity 的默认大类被删掉后不再硬塞进去。
    assert property_categories.suggest("identity", "person", None, config) == (
        property_categories.FALLBACK, "fallback"
    )


# -- projection ------------------------------------------------------------

def test_one_property_can_show_under_two_categories(tmp_path: Path) -> None:
    ops = _project(tmp_path / "split.sqlite3")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            _definitions("state") + [
                _value("v1", "1", "state", "脸色铁青"),
                _value("v2", "1", "state", "身受重伤"),
            ],
            revision,
            source="manual",
        ),
        1,
    )

    summary = ops.property_node_summary("1")

    # 同一条属性按值分到两个大类，各自只带自己那一条记录。
    assert [(group["key"], group["count"]) for group in summary["categories"]] == [
        ("外貌", 1),
        ("能力与身体状态", 1),
    ]
    for group in summary["categories"]:
        item = group["items"][0]
        assert item["property_id"] == "state"
        assert len(item["records"]) == 1
        # 分片时要说明另一片在哪，否则看起来像记录丢了。
        assert len(item["other_categories"]) == 1
    # 扁平列表里是两条切片，不是一条被覆盖的记录。
    assert [item["value"] for item in summary["properties"]] == ["脸色铁青", "身受重伤"]


def test_extraction_category_beats_the_name_hint(tmp_path: Path) -> None:
    ops = _project(tmp_path / "explicit.sqlite3")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            _definitions("appearance") + [
                _value("v1", "1", "appearance", "一套合体的黑色西装", category="持有与使用"),
            ],
            revision,
            source="manual",
        ),
        1,
    )
    item = ops.property_node_summary("1")["properties"][0]
    # 模型给的分类优先于属性名推断：分类是记录自己的属性。
    assert (item["category"], item["category_source"]) == ("持有与使用", "extraction")


# -- moving ----------------------------------------------------------------

def test_moving_a_category_appends_a_record_and_leaves_the_value_alone(
    tmp_path: Path,
) -> None:
    ops = _project(tmp_path / "move.sqlite3")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            _definitions("state") + [_value("v1", "1", "state", "一无所有")],
            revision,
            source="manual",
        ),
        1,
    )
    assert ops.property_node_summary("1")["properties"][0]["category"] == (
        property_categories.FALLBACK
    )

    moved = ops.property_categorize(["v1"], "持有与使用")
    assert moved["moved"] == ["v1"]
    assert moved["revision"] > revision

    item = ops.property_node_summary("1")["properties"][0]
    assert (item["category"], item["category_source"]) == ("持有与使用", "manual")
    assert item["value"] == "一无所有"

    # 取值记录没有被改写：分类是另一条记录。
    values = [r for r in ops.property_records() if r["kind"] == "property_value"]
    assert len(values) == 1 and "category" not in values[0]
    kinds = [r["kind"] for r in ops.property_records()]
    assert kinds.count("property_categorization") == 1

    # 再移到同一处不再追加记录。
    again = ops.property_categorize(["v1"], "持有与使用")
    assert again["moved"] == []
    assert again["revision"] == moved["revision"]


def test_retracting_a_move_restores_the_previous_grouping(tmp_path: Path) -> None:
    ops = _project(tmp_path / "undo.sqlite3")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            _definitions("height") + [_value("v1", "1", "height", "矮小")],
            revision,
            source="manual",
        ),
        1,
    )
    assert ops.property_node_summary("1")["properties"][0]["category"] == "外貌"

    ops.property_categorize(["v1"], "其他")
    assert ops.property_node_summary("1")["properties"][0]["category"] == "其他"

    categorization = next(
        r for r in ops.property_records() if r["kind"] == "property_categorization"
    )
    retraction = {
        "kind": "property_retraction", "id": "undo-1",
        "target_id": categorization["id"], "reason": "分错了",
    }
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose([retraction], revision, source="manual"), 1
    )

    # 撤回之后回到属性名推出的大类，而不是卡在「其他」。
    item = ops.property_node_summary("1")["properties"][0]
    assert (item["category"], item["category_source"]) == ("外貌", "name")


def test_only_value_records_can_be_moved(tmp_path: Path) -> None:
    ops = _project(tmp_path / "badmove.sqlite3")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            _definitions("height") + [_value("v1", "1", "height", "矮小")],
            revision,
            source="manual",
        ),
        1,
    )
    with pytest.raises(ValueError):
        ops.property_categorize(["height"], "外貌")
    with pytest.raises(ValueError):
        ops.property_categorize(["missing"], "外貌")
    with pytest.raises(ValueError):
        ops.property_categorize(["v1"], "  ")


# -- the other end of a relation -------------------------------------------

def test_a_holder_record_is_readable_from_the_holder_too(tmp_path: Path) -> None:
    ops = _project(tmp_path / "inbound.sqlite3")
    revision = ops.graph_project_info()["revision"]
    definition = {
        "kind": "property_definition", "id": "holder", "value_type": "node",
        "cardinality": "one", "persistence": "until_changed",
    }
    ops.property_accept(
        ops.property_propose(
            [definition, _value("v1", "3", "holder", "2")], revision, source="manual"
        ),
        1,
    )

    # 手镯页面：归属与持有。
    item = ops.property_node_summary("3")["properties"][0]
    assert (item["category"], item["value_node"]["name"]) == ("归属与持有", "老周")

    # 老周页面：同一条记录的第二个入口，归到「持有与使用」。
    inbound = ops.property_node_summary("2")["inbound"]
    assert [(row["property_id"], row["category"], row["node_name"]) for row in inbound] == [
        ("holder", "持有与使用", "铜钥匙"),
    ]
    # 同一份记录，不是复制品。
    assert inbound[0]["record_id"] == "v1"


# -- vocabulary as project config ------------------------------------------

def test_renaming_a_category_migrates_its_records(tmp_path: Path) -> None:
    ops = _project(tmp_path / "vocab.sqlite3")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            _definitions("height") + [_value("v1", "1", "height", "矮小")],
            revision,
            source="manual",
        ),
        1,
    )
    assert ops.property_node_summary("1")["properties"][0]["category"] == "外貌"

    # 只改一个名字：外貌 → 外形。原位改名要跟着迁移，不能把记录甩回「其他」。
    vocabulary = [
        "外形" if name == "外貌" else name
        for name in property_categories.DEFAULT_CATEGORIES["person"]
    ]
    result = ops.set_property_categories({"person": vocabulary})

    assert result["vocabulary"]["person"][1] == "外形"
    assert result["moved"] == ["v1"]
    item = ops.property_node_summary("1")["properties"][0]
    assert (item["category"], item["category_source"]) == ("外形", "manual")


def test_a_wholesale_vocabulary_rewrite_is_not_guessed_as_a_rename(
    tmp_path: Path,
) -> None:
    ops = _project(tmp_path / "rewrite.sqlite3")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            _definitions("height") + [_value("v1", "1", "height", "矮小")],
            revision,
            source="manual",
        ),
        1,
    )
    result = ops.set_property_categories({"person": ["外形", "其他"]})

    assert result["moved"] == []
    assert ops.property_node_summary("1")["vocabulary"] == ["外形", "其他"]
    # 推断出来的分类依赖分类表：整表换掉后旧名字不在了，记录退回「其他」，
    # 由审计汇总提示，而不是被硬塞进某个新大类。
    assert ops.property_node_summary("1")["properties"][0]["category"] == "其他"


def test_audit_flags_a_category_outside_the_vocabulary(tmp_path: Path) -> None:
    ops = _project(tmp_path / "audit.sqlite3")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            _definitions("height", "state") + [
                _value("v1", "1", "height", "矮小", category="体型"),
                _value("v2", "1", "state", "一无所有"),
            ],
            revision,
            source="manual",
        ),
        1,
    )
    audit = ops.property_audit()
    codes = [issue["code"] for issue in audit["issues"]]
    assert "category_unknown" in codes
    # 落不到任何大类的记录只汇总一条提示，不按记录刷屏。
    unassigned = [i for i in audit["issues"] if i["code"] == "category_unassigned"]
    assert len(unassigned) == 1
    assert "1 条属性暂时归入" in unassigned[0]["message"]


def test_a_move_that_points_nowhere_is_an_error(tmp_path: Path) -> None:
    ops = _project(tmp_path / "dangling.sqlite3")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            _definitions("height") + [_value("v1", "1", "height", "矮小")],
            revision,
            source="manual",
        ),
        1,
    )
    revision = ops.graph_project_info()["revision"]
    dangling = {
        "kind": "property_categorization", "id": "cat-bad",
        "target_id": "nope", "category": "外貌",
    }
    with pytest.raises(ValueError):
        ops.property_propose([dangling], revision, source="manual")


# -- 一个方面一条线 ---------------------------------------------------------

def test_aspect_is_the_first_token_of_the_name() -> None:
    assert property_categories.aspect_of("cultivation_stage") == "cultivation"
    assert property_categories.aspect_of("height") == "height"
    # 长得像的名字不能因为前缀就被并到一起。
    assert property_categories.aspect_of("state") == "state"
    assert property_categories.aspect_of("status") == "status"
    assert property_categories.aspect_of("statement") == "statement"


def test_two_names_for_one_aspect_share_a_line(tmp_path: Path) -> None:
    ops = _project(tmp_path / "aspect.sqlite3")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            _definitions("cultivation", "cultivation_stage") + [
                _value("v1", "1", "cultivation", "灵寂期", scope="claim", holder_id="2"),
                _value("v2", "1", "cultivation_stage", "辟谷期"),
                _value("v3", "1", "cultivation_stage", "到了元婴初期",
                       scope="cognition", holder_id="1"),
            ],
            revision,
            source="manual",
        ),
        1,
    )

    summary = ops.property_node_summary("1")
    assert len(summary["properties"]) == 1
    item = summary["properties"][0]

    # 同一方面一条线：三个境界不是三个当前值，而是一条披露序列。
    assert item["aspect"] == "cultivation"
    assert item["label"] == "修为"
    assert item["property_ids"] == ["cultivation", "cultivation_stage"]
    assert [row["scope"] for row in item["records"]] == ["claim", "world", "cognition"]
    assert item["scopes"] == ["claim", "cognition", "world"]
    assert item["value"] == "到了元婴初期"
    # 三条都还是有效记录：谁是「当前」由失效标记决定，不由位置推算。
    assert [row["valid"] for row in item["records"]] == [True, True, True]
    # 每条记录仍带自己的属性名，读者能看出它来自哪个名字。
    assert [row["property_label"] for row in item["records"]] == ["修为", "修为境界", "修为境界"]


def test_property_labels_come_from_the_backend(tmp_path: Path) -> None:
    ops = _project(tmp_path / "labels.sqlite3")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            _definitions("height", "quux_level") + [
                _value("v1", "1", "height", "矮小"),
                _value("v2", "1", "quux_level", "三层"),
            ],
            revision,
            source="manual",
        ),
        1,
    )

    labels = {
        item["property_id"]: item["label"]
        for item in ops.property_node_summary("1")["properties"]
    }
    # 认识的属性名给中文，不认识的照原名显示，不猜。
    assert labels["height"] == "身高"
    assert labels["quux_level"] == "quux_level"


# -- 归并属性名 -------------------------------------------------------------

def _two_names(ops, revision: int) -> None:
    ops.property_accept(
        ops.property_propose(
            _definitions("cultivation", "cultivation_stage") + [
                _value("v1", "1", "cultivation", "灵寂期"),
                _value("v2", "1", "cultivation_stage", "辟谷期"),
                _value("v3", "1", "cultivation_stage", "到了元婴初期"),
            ],
            revision,
            source="manual",
        ),
        1,
    )


def test_merging_two_names_reads_them_as_one(tmp_path: Path) -> None:
    ops = _project(tmp_path / "merge.sqlite3")
    _two_names(ops, ops.graph_project_info()["revision"])

    merged = ops.property_merge("cultivation_stage", "cultivation")
    assert merged["alias_id"] == "cultivation_stage"
    assert merged["target_id"] == "cultivation"

    item = ops.property_node_summary("1")["properties"][0]
    # 归并后只剩一个名字，记录本身没有被改写。
    assert item["property_ids"] == ["cultivation"]
    assert item["label"] == "修为"
    assert [row["value"] for row in item["records"]] == ["灵寂期", "辟谷期", "到了元婴初期"]
    # 按合并后的名字查询，看到的是整条序列的当前值。
    assert ops.property_state("1", "cultivation")["state"] == {
        "kind": "known", "value": "到了元婴初期",
    }
    # 名字不再分裂，审计也就不再报近义名。
    codes = [issue["code"] for issue in ops.property_audit()["issues"]]
    assert "name_variant" not in codes

    stored = [r for r in ops.property_records() if r["kind"] == "property_value"]
    assert {r["property_id"] for r in stored} == {"cultivation", "cultivation_stage"}


def test_retracting_a_merge_splits_the_names_again(tmp_path: Path) -> None:
    ops = _project(tmp_path / "unmerge.sqlite3")
    _two_names(ops, ops.graph_project_info()["revision"])
    ops.property_merge("cultivation_stage", "cultivation")

    merge_record = next(
        r for r in ops.property_records() if r["kind"] == "property_merge"
    )
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            [{"kind": "property_retraction", "id": "undo-merge",
              "target_id": merge_record["id"], "reason": "不是同一个方面"}],
            revision,
            source="manual",
        ),
        1,
    )

    names = ops.property_node_summary("1")["properties"][0]["property_ids"]
    assert names == ["cultivation", "cultivation_stage"]


def test_a_merge_needs_two_existing_names(tmp_path: Path) -> None:
    ops = _project(tmp_path / "badmerge.sqlite3")
    _two_names(ops, ops.graph_project_info()["revision"])

    with pytest.raises(ValueError):
        ops.property_merge("cultivation_stage", "cultivation_stage")
    with pytest.raises(ValueError):
        ops.property_merge("nope", "cultivation")


# -- 按阅读位置看状态 -------------------------------------------------------

def _timeline(path: Path):
    ops = create_graph_project(path)
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "初到"},
                {"kind": "node", "id": "3", "node_type": "event", "name": "修炼"},
                {"kind": "node", "id": "4", "node_type": "event", "name": "出关"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    ops.property_accept(
        ops.property_propose(
            _definitions("cultivation") + [
                _value("v1", "1", "cultivation", "辟谷期", event_id="2"),
                _value("v2", "1", "cultivation", "灵寂期", event_id="3"),
                _value("v3", "1", "cultivation", "元婴初期", event_id="4"),
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )
    return ops


def test_a_reading_cut_shows_the_state_at_that_event(tmp_path: Path) -> None:
    ops = _timeline(tmp_path / "cut.sqlite3")

    latest = ops.property_node_summary("1")
    assert latest["cut"] is None
    assert latest["properties"][0]["value"] == "元婴初期"
    assert latest["later_count"] == 0

    middle = ops.property_node_summary("1", cut="3")
    assert middle["cut"] == "3"
    item = middle["properties"][0]
    # 读到「修炼」时只知道灵寂期，元婴初期还没披露。
    assert item["value"] == "灵寂期"
    assert [row["value"] for row in item["records"]] == ["辟谷期", "灵寂期"]
    assert item["later_count"] == 1
    assert middle["later_count"] == 1

    first = ops.property_node_summary("1", cut="2")
    assert first["properties"][0]["value"] == "辟谷期"
    assert first["later_count"] == 2


def test_a_cut_before_the_first_disclosure_shows_nothing(tmp_path: Path) -> None:
    ops = _timeline(tmp_path / "cut-early.sqlite3")

    # 「初到」这个事件还没有任何披露，读到这里就什么都不知道。
    early = ops.property_node_summary("1", cut="2")
    assert [row["value"] for row in early["properties"][0]["records"]] == ["辟谷期"]
    assert early["later_count"] == 2

    # 比第一个事件还早的位置：一条都不可见，条数仍然要说清楚。
    nothing = ops.property_node_summary("1", cut="1.0.1")
    assert nothing["properties"] == []
    assert nothing["later_count"] == 3


def test_inbound_records_follow_the_reading_cut(tmp_path: Path) -> None:
    """反向引用也是披露：还没读到的那一端不该出现。"""
    ops = create_graph_project(tmp_path / "inbound-cut.sqlite3")
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "初到"},
                {"kind": "node", "id": "3", "node_type": "event", "name": "得令牌"},
                {"kind": "node", "id": "4", "node_type": "item", "name": "令牌"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    ops.property_accept(
        ops.property_propose(
            _definitions("holder") + [
                _value("v1", "4", "holder", "1", event_id="3"),
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )

    assert [row["node_name"] for row in ops.property_node_summary("1")["inbound"]] == ["令牌"]
    assert [row["node_name"] for row in ops.property_node_summary("1", cut="3")["inbound"]] == [
        "令牌",
    ]
    # 读到「初到」时令牌还没出现，林舟页面上不该有这条反向引用。
    assert ops.property_node_summary("1", cut="2")["inbound"] == []

