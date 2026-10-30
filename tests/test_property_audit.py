"""Rule-based property audit: overlaps, contradictions, stale values, gaps."""

from __future__ import annotations

from pathlib import Path

from narrative.project_catalog import create_graph_project
from narrative.property_audit import audit_properties


def _node(node_id: str, name: str, node_type: str = "person") -> dict:
    return {"kind": "node", "id": node_id, "name": name, "node_type": node_type}


def _definition(property_id: str, value_type: str = "string") -> dict:
    return {
        "kind": "property_definition", "id": property_id, "value_type": value_type,
        "cardinality": "one",
    }


def _value(
    rid: str, node_id: str, property_id: str, value,
    *, event="1", start=0, source_id="s1", quote="原文", **extra,
) -> dict:
    return {
        "kind": "property_value", "id": rid, "node_id": node_id,
        "property_id": property_id, "value": value, "event_id": event,
        "disclosure": {"source_id": source_id, "start": start},
        "evidence": {"source_id": source_id, "quote": quote},
        **extra,
    }


def _codes(report: dict) -> list[str]:
    return [item["code"] for item in report["issues"]]


def test_identical_values_at_one_anchor_are_an_overlap() -> None:
    records = [
        _definition("height"),
        _value("v1", "1", "height", "180", start=0),
        _value("v2", "1", "height", "180", start=0),
    ]
    report = audit_properties(records, [_node("1", "林舟")])

    assert "duplicate_value" in _codes(report)
    overlap = next(item for item in report["issues"] if item["code"] == "duplicate_value")
    assert overlap["severity"] == "error"
    assert overlap["record_ids"] == ["v1", "v2"]


def test_same_value_at_different_places_is_a_restatement_not_an_error() -> None:
    records = [
        _definition("holder"),
        _value("v1", "1", "holder", "7", event="1", start=100),
        _value("v2", "1", "holder", "7", event="5", start=900),
    ]
    report = audit_properties(records, [_node("1", "火纹石", "item")])

    codes = _codes(report)
    assert "duplicate_value" not in codes
    assert "repeated_value" in codes
    assert report["counts"]["error"] == 0


def test_a_changing_value_is_not_an_audit_finding() -> None:
    """值变了是推进，不是问题：更早的那条在接受时就自动失效了，审计不必再提。"""
    records = [
        _definition("height"),
        _value("v1", "1", "height", "矮小", event="1", start=0),
        _value("v2", "1", "height", "180 公分", event="2", start=0),
    ]
    report = audit_properties(records, [_node("1", "林舟")])
    codes = _codes(report)

    assert "superseded_values" not in codes
    assert report["counts"]["error"] == 0


def test_missing_definition_evidence_and_anchor_are_reported() -> None:
    records = [
        _value("v1", "1", "undeclared", "x"),
        {"kind": "property_value", "id": "v2", "node_id": "1", "property_id": "undeclared",
         "value": "y", "disclosure": {"source_id": "s1", "start": 5}},
    ]
    report = audit_properties(records, [_node("1", "林舟")])
    codes = set(_codes(report))

    assert "definition_missing" in codes
    assert "evidence_missing" in codes
    assert "anchor_missing" in codes


def test_cognition_without_holder_is_an_error() -> None:
    records = [
        _definition("belief"),
        _value("v1", "1", "belief", "老周赢了", scope="cognition"),
    ]
    report = audit_properties(records, [_node("1", "林舟")])

    issue = next(item for item in report["issues"] if item["code"] == "holder_missing")
    assert issue["severity"] == "error"


def test_synonym_property_names_are_flagged() -> None:
    records = [
        _definition("height"),
        _definition("height_cm"),
        _value("v1", "1", "height", "180", start=0),
        _value("v2", "1", "height_cm", "180", start=10),
    ]
    report = audit_properties(records, [_node("1", "林舟")])

    issue = next(item for item in report["issues"] if item["code"] == "name_variant")
    assert "height" in issue["message"] and "height_cm" in issue["message"]


def test_unused_definition_is_an_info_not_an_error() -> None:
    records = [_definition("age")]
    report = audit_properties(records, [_node("1", "林舟")])

    issue = next(item for item in report["issues"] if item["code"] == "definition_unused")
    assert issue["severity"] == "info"
    assert report["counts"]["error"] == 0


def test_audit_can_be_limited_to_the_nodes_of_one_extraction(tmp_path: Path) -> None:
    ops = create_graph_project(tmp_path / "audit.nproj")
    ops.graph_accept(
        ops.graph_propose(
            [
                _node("1", "林舟"),
                _node("2", "老周"),
                {"kind": "node", "id": "3", "node_type": "event", "name": "交谈"},
            ],
            ops.graph_project_info()["revision"],
        ),
        1,
    )
    source_id = ops.import_source("第一章", "林舟身高一百八十公分。老周矮小。")
    revision = ops.graph_project_info()["revision"]
    ops.property_accept(
        ops.property_propose(
            [
                _definition("height"),
                _value("v1", "1", "height", "180", event="3", start=0,
                       source_id=source_id, quote="林舟身高一百八十公分。"),
                _value("v2", "1", "height", "180", event="3", start=0,
                       source_id=source_id, quote="林舟身高一百八十公分。"),
                _value("v3", "2", "height", "矮小", event="3", start=0,
                       source_id=source_id, quote="老周矮小。"),
                _value("v4", "2", "height", "矮小", event="3", start=0,
                       source_id=source_id, quote="老周矮小。"),
            ],
            revision,
            source="manual",
        ),
        1,
    )

    whole = ops.property_audit()
    limited = ops.property_audit({"2"})

    assert "duplicate_value" in _codes(whole)
    assert {item["node_id"] for item in limited["issues"]} == {"2"}
    assert limited["checked_records"] == 2


def _retraction(rid: str, target: str, reason: str = "原文否定了这条") -> dict:
    """撤回只指向一条取值记录：节点、属性、作用域都从目标读。

    生产端就是这么写的（`PropertyStore.retract`），测试也必须照这个形状，
    否则测的是一条永远不会出现的记录。
    """
    return {
        "kind": "property_retraction", "id": rid, "target_id": target, "reason": reason,
    }


def test_retraction_pointing_nowhere_is_an_error() -> None:
    records = [
        _definition("holder"),
        _value("v1", "1", "holder", "7", start=0),
        _retraction("r1", "missing"),
    ]
    report = audit_properties(records, [_node("1", "火纹石", "item")])

    issue = next(item for item in report["issues"] if item["code"] == "retraction_without_target")
    assert issue["severity"] == "error"


def test_a_retraction_takes_its_aspect_from_the_target() -> None:
    """撤回没有自己的节点和属性名，所以不能因此报「记录不完整」。"""
    records = [
        _definition("holder"),
        _definition("state"),
        _value("v1", "1", "state", "完好", start=0),
        _retraction("r1", "v1"),
    ]
    report = audit_properties(records, [_node("1", "火纹石", "item")])

    assert report["counts"]["error"] == 0
    # 它只作用于目标那条记录所在的属性，别的属性不受影响。
    issue = next(item for item in report["issues"] if item["code"] == "retracted_no_successor")
    assert issue["property_id"] == "state"


def test_retraction_without_a_later_value_leaves_no_current_value() -> None:
    records = [
        _definition("holder"),
        _value("v1", "1", "holder", "7", event="1", start=0),
        _retraction("r1", "v1"),
    ]
    report = audit_properties(records, [_node("1", "火纹石", "item")])

    issue = next(item for item in report["issues"] if item["code"] == "retracted_no_successor")
    assert issue["severity"] == "warning"


def test_a_retraction_that_leaves_a_value_standing_is_not_reported() -> None:
    """失效是常规动作：只要还剩一条站得住，就不必每次都在审核里说一遍。"""
    records = [
        _definition("holder"),
        _value("v1", "1", "holder", "7", event="1", start=0),
        _value("v2", "1", "holder", "8", event="2", start=100),
        _retraction("r1", "v2"),
    ]
    report = audit_properties(records, [_node("1", "火纹石", "item")])

    codes = _codes(report)
    assert "retracted_no_successor" not in codes
    # 撤回掉的那条不该再被当成「已被取代」的证据。
    assert "superseded_values" not in codes
    assert report["counts"]["error"] == 0


def test_values_on_a_retracted_node_are_flagged() -> None:
    node = _node("1", "火纹石", "item")
    node["retracted"] = True
    records = [
        _definition("holder"),
        _value("v1", "1", "holder", "7", start=0),
    ]
    report = audit_properties(records, [node])

    issue = next(item for item in report["issues"] if item["code"] == "node_retracted_with_values")
    assert issue["severity"] == "warning"


def test_anchor_on_a_retracted_event_is_flagged() -> None:
    event = _node("9", "被删掉的事件", "event")
    event["retracted"] = True
    records = [
        _definition("holder"),
        _value("v1", "1", "holder", "7", event="9", start=0),
    ]
    report = audit_properties(records, [_node("1", "火纹石", "item"), event])

    issue = next(item for item in report["issues"] if item["code"] == "anchor_retracted")
    assert issue["severity"] == "warning"


def test_two_names_for_one_aspect_are_flagged() -> None:
    records = [
        _definition("cultivation"),
        _definition("cultivation_stage"),
        _value("v1", "1", "cultivation", "灵寂期"),
        _value("v2", "1", "cultivation_stage", "辟谷期"),
    ]
    report = audit_properties(records, [_node("1", "林舟")])

    issue = next(item for item in report["issues"] if item["code"] == "name_variant")
    assert "cultivation" in issue["message"] and "cultivation_stage" in issue["message"]
    # 换个名字就多一条“当前值”，所以这是要人合并的警告，不是错误。
    assert issue["severity"] == "warning"


def test_a_changing_value_is_a_progression_not_a_conflict() -> None:
    """同一方面前后三个值不等于矛盾：这是一条时间线，只在同一名字同一作用域内比较。"""
    records = [
        _definition("cultivation"),
        _value("v1", "1", "cultivation", "辟谷期", event="1", start=0),
        _value("v2", "1", "cultivation", "灵寂期", event="2", start=0),
        _value("v3", "1", "cultivation", "元婴初期", event="3", start=0),
    ]
    report = audit_properties(records, [_node("1", "林舟")])
    codes = _codes(report)

    assert "name_variant" not in codes
    assert "superseded_values" not in codes
    assert report["counts"]["error"] == 0



def test_two_names_with_one_value_are_reported() -> None:
    """同一个节点上两个名字记着同一个值：多半是同一个方面被叫了两个名字。"""
    nodes = [_node("1", "铜钥匙", "item")]
    records = [
        _definition("功能"), _definition("用途"),
        _value("v1", "1", "功能", "开门"),
        _value("v2", "1", "用途", "开门"),
    ]
    codes = {item["code"] for item in audit_properties(records, nodes)["issues"]}
    assert "aspect_overlap" in codes


def test_two_names_with_different_values_are_not_reported() -> None:
    """值不同就是两件事，别报。"""
    nodes = [_node("1", "铜钥匙", "item")]
    records = [
        _definition("功能"), _definition("用途"),
        _value("v1", "1", "功能", "开门"),
        _value("v2", "1", "用途", "开门之后能锁上"),
    ]
    codes = {item["code"] for item in audit_properties(records, nodes)["issues"]}
    assert "aspect_overlap" not in codes


def test_a_value_that_points_at_its_own_node_is_reported() -> None:
    """「林舟的室友 · 关系 = 林舟的室友」说的是身份，不是关系：画到关系网上是个自环。"""
    nodes = [_node("1", "林舟的室友")]
    records = [
        _definition("关系"),
        _value("v1", "1", "关系", "林舟的室友"),
    ]
    report = audit_properties(records, nodes)
    assert "self_reference" in _codes(report)
    assert report["issues"][0]["node_name"] == "林舟的室友"


def test_a_value_pointing_at_somebody_else_is_not_reported() -> None:
    nodes = [_node("1", "林舟"), _node("2", "老周")]
    records = [
        _definition("师父"),
        _value("v1", "1", "师父", "2"),
    ]
    assert "self_reference" not in _codes(audit_properties(records, nodes))
