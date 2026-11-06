"""Shared counterpart is not evidence that relationship roles are synonyms."""
from narrative.assistant import _first_prompt
from narrative.property_audit import audit_properties
from narrative.tool_api import TOOLS


def data():
    nodes = [{"id": "P1", "node_type": "person", "name": "甲"}, {"id": "P2", "node_type": "person", "name": "乙"}]
    records = [{"kind": "property_definition", "id": name, "value_type": "node", "cardinality": "one"} for name in ["朋友", "同伴"]]
    records += [{"kind": "property_value", "id": f"PV{i}", "node_id": "P1", "property_id": name,
                 "value": "P2", "value_type": "node", "scope": "world", "category": "关系",
                 "disclosure": {"source_id": "SRC1", "start": 0}, "evidence": {"source_id": "SRC1", "quote": "甲与乙来往。"}}
                for i, name in enumerate(["朋友", "同伴"], 1)]
    return nodes, records


def test_distinct_roles_for_same_counterpart_do_not_suggest_synonym_merge():
    nodes, records = data()
    issues = audit_properties(records, nodes)["issues"]
    assert not any(row["code"] in {"aspect_overlap", "duplicate_value", "name_variant"} for row in issues)
    assert not any("合并" in row["suggestion"] for row in issues)


def test_exact_duplicate_at_same_disclosure_is_still_reported():
    nodes, records = data()
    records.append(dict(records[-1], id="PV3"))
    duplicates = [row for row in audit_properties(records, nodes)["issues"] if row["code"] == "duplicate_value"]
    assert len(duplicates) == 1
    assert set(duplicates[0]["record_ids"]) == {"PV2", "PV3"}


def test_task_purpose_balances_relationship_coverage_and_correction():
    prompt = _first_prompt("复核人物关系")
    for principle in ["人物节点齐全不等于关系齐全", "核对人物间明确的关系及其变化", "同一对人物可同时有多种关系", "复核既查漏补缺，也勘误并判断失效"]:
        assert principle in prompt
    for principle in ["不只长期身份或称谓", "原文明示已发生的人际作用角色", "不因仅发生一次就排除", "不由共现或单次作用外推长期立场", "实际完成情况以工具返回为准", "未调用或未成功的工具不得声称已完成"]:
        assert principle in prompt
    assert "相同主体或对象不是同义依据" in TOOLS["merge_properties"].summary
