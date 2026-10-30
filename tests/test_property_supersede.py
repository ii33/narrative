"""审核轮判定失效：旧值从取代它的披露起不再算状态。

这里测三件事：规则本身（谁被取代、谁不算）、位置语义（更早的阅读位置仍然看得到
旧值）、以及第二轮模型给出的「这个值已经过去了」能不能落到具体记录上。
"""

from __future__ import annotations

from pathlib import Path

from narrative.extraction import _review_supersede
from narrative.project_catalog import create_graph_project
from narrative.property_store import supersede_plan

TEXT = "林舟脸色铁青。林舟走进房间。后来他笑了。"


def _project(path: Path):
    ops = create_graph_project(path)
    source = ops.import_source("第一章", TEXT)
    ops.graph_accept(
        ops.graph_propose(
            [
                {"kind": "node", "id": "1", "node_type": "person", "name": "林舟"},
                {"kind": "node", "id": "2", "node_type": "event", "name": "站在门口"},
                {"kind": "node", "id": "3", "node_type": "event", "name": "他笑了"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    return ops, source


def _value(
    rid: str, value: object, event: str, start: int,
    property_id: str = "state", **extra,
) -> dict:
    return {
        "kind": "property_value", "id": rid, "node_id": "1",
        "property_id": property_id, "value": value, "event_id": event,
        "disclosure": {"source_id": extra.pop("source_id", "s1"), "start": start},
        **extra,
    }


def _definition(property_id: str = "state", cardinality: str = "one") -> dict:
    return {
        "kind": "property_definition", "id": property_id, "value_type": "string",
        "cardinality": cardinality,
    }


# -- 规则 ------------------------------------------------------------------

def test_the_later_value_supersedes_the_earlier_one() -> None:
    plan = supersede_plan([_definition(), _value("v1", "脸色铁青", "2", 0),
                           _value("v2", "笑了", "3", 15)])

    assert len(plan) == 1
    assert plan[0]["target_id"] == "v1"
    # 失效从取代它的那次披露起，不是从旧值自己的披露起。
    assert plan[0]["event_id"] == "3"
    assert plan[0]["disclosure"]["start"] == 15


def test_a_restatement_supersedes_nothing() -> None:
    plan = supersede_plan([_definition(), _value("v1", "脸色铁青", "2", 0),
                           _value("v2", "脸色铁青", "3", 15)])

    assert plan == []


def test_multi_valued_properties_keep_every_value() -> None:
    plan = supersede_plan([
        _definition("tag", "many"),
        _value("v1", "A", "2", 0, property_id="tag"),
        _value("v2", "B", "3", 15, property_id="tag"),
    ])

    assert plan == []


def test_two_names_for_one_aspect_supersede_each_other() -> None:
    """`cultivation` 与 `cultivation_stage` 是同一方面，读者看到的是同一条线。"""
    plan = supersede_plan([
        _definition("cultivation"), _definition("cultivation_stage"),
        {"kind": "property_value", "id": "v1", "node_id": "1", "property_id": "cultivation",
         "value": "辟谷期", "event_id": "2"},
        {"kind": "property_value", "id": "v2", "node_id": "1",
         "property_id": "cultivation_stage", "value": "灵寂期", "event_id": "3"},
    ])

    assert [(item["target_id"], item["event_id"]) for item in plan] == [("v1", "3")]


def test_a_claim_does_not_supersede_the_world() -> None:
    """老周说林舟在灵寂期，不等于世界记录里的修为被它取代。"""
    plan = supersede_plan([
        _definition(),
        _value("v1", "辟谷期", "2", 0),
        _value("v2", "灵寂期", "3", 15, scope="claim", holder_id="9"),
    ])

    assert plan == []


def test_an_already_invalid_record_is_not_marked_twice() -> None:
    plan = supersede_plan([
        _definition(),
        _value("v1", "脸色铁青", "2", 0),
        _value("v2", "笑了", "3", 15),
        {"kind": "property_retraction", "id": "r1", "target_id": "v1",
         "reason": "被后一条披露取代", "event_id": "3", "basis": "audit"},
    ])

    assert plan == []


# -- 位置语义 --------------------------------------------------------------

def test_an_invalid_value_still_counts_before_the_disclosure_that_ended_it(
    tmp_path: Path,
) -> None:
    ops, source = _project(tmp_path / "cut.nproj")
    ops.property_accept(
        ops.property_propose(
            [
                _definition("height"),
                _value("v1", "矮小", "2", 0, property_id="height", source_id=source,
                       evidence={"source_id": source, "quote": "林舟脸色铁青"}),
                _value("v2", "变高了", "3", 7, property_id="height", source_id=source,
                       evidence={"source_id": source, "quote": "后来他笑了"}),
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )
    ops.property_supersede([{"target_id": "v1", "event_id": "3", "reason": "被取代"}])

    # 读到事件 2：旧值仍然是他知道的状态。
    early = ops.property_node_summary("1", cut="2")["properties"][0]
    assert [row["value"] for row in early["records"]] == ["矮小"]
    assert early["value"] == "矮小"

    # 读到事件 3：旧值不再显示，新值接手。
    late = ops.property_node_summary("1")["properties"][0]
    assert [row["value"] for row in late["records"]] == ["变高了"]
    assert late["value"] == "变高了"


# -- 模型判断 --------------------------------------------------------------

def test_the_review_round_can_end_a_value_nothing_replaced(tmp_path: Path) -> None:
    ops, source = _project(tmp_path / "review.nproj")
    ops.property_accept(
        ops.property_propose(
            [
                _definition(),
                _value("v1", "心里发怵", "2", 0, source_id=source,
                       evidence={"source_id": source, "quote": "林舟脸色铁青"}),
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )
    accepted = ops.property_records(include_retracted=True)
    data = {"supersede": [{
        "node": "林舟", "property": "state", "value": "心里发怵",
        "at_event": "他笑了", "reason": "进了房间之后没再提",
    }]}

    records = _review_supersede(
        data, [], accepted, {"林舟": "1"}, {"他笑了": "3"}
    )

    assert len(records) == 1
    assert records[0]["target_id"] == "v1"
    assert records[0]["event_id"] == "3"
    assert records[0]["basis"] == "audit"
    assert records[0]["reason"] == "进了房间之后没再提"


def test_a_judgment_that_cannot_be_located_is_dropped(tmp_path: Path) -> None:
    ops, source = _project(tmp_path / "unresolved.nproj")
    ops.property_accept(
        ops.property_propose(
            [_definition(), _value("v1", "心里发怵", "2", 0, source_id=source,
                                   evidence={"source_id": source, "quote": "林舟脸色铁青"})],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )
    accepted = ops.property_records(include_retracted=True)

    # 事件名对不上：没有可锚定的位置，就不猜，也不落记录。
    assert _review_supersede(
        {"supersede": [{"node": "林舟", "property": "state", "value": "心里发怵",
                        "at_event": "不存在的事件"}]},
        [], accepted, {"林舟": "1"}, {"他笑了": "3"},
    ) == []
    # 值对不上：同样丢掉。
    assert _review_supersede(
        {"supersede": [{"node": "林舟", "property": "state", "value": "别的写法",
                        "at_event": "他笑了"}]},
        [], accepted, {"林舟": "1"}, {"他笑了": "3"},
    ) == []


# -- 两个问题，两种回答 ----------------------------------------------------

def test_an_invalidated_value_is_not_shown_at_all(tmp_path: Path) -> None:
    """失效只有一个结果：不显示。没有并集视图，也没有失效条数、失效于哪次披露。"""
    ops, source = _project(tmp_path / "union.nproj")
    ops.property_accept(
        ops.property_propose(
            [
                _definition("height"),
                _value("v1", "矮小", "2", 0, property_id="height", source_id=source,
                       evidence={"source_id": source, "quote": "林舟脸色铁青"}),
                _value("v2", "变高了", "3", 7, property_id="height", source_id=source,
                       evidence={"source_id": source, "quote": "后来他笑了"}),
            ],
            ops.graph_project_info()["revision"],
            source="manual",
        ),
        1,
    )
    ops.property_supersede([{"target_id": "v1", "event_id": "3", "reason": "被取代"}])

    state = ops.property_node_summary("1")["properties"][0]
    # 只剩站得住的那条；被取代的那条不出现，行上也没有任何失效的派生说法。
    assert [row["value"] for row in state["records"]] == ["变高了"]
    assert set(state) & {"invalid_count", "valid_count", "retracted"} == set()
    assert all(row["valid"] for row in state["records"])

    # 记录本身还在库里：撤回不删行。
    stored = [
        record["value"] for record in ops.property_records(include_retracted=True)
        if record.get("kind") == "property_value"
    ]
    assert stored == ["矮小", "变高了"]


