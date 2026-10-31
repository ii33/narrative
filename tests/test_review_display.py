from __future__ import annotations

from narrative.operations import Operations
from narrative.property_labels import label_for
from narrative.review_display import candidate_rows, humanize_report


def test_retraction_shows_the_original_value_event_and_evidence(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "review.nproj")
    source = ops.import_source("第一章", "林舟离开书店。")
    candidate = ops.graph_propose([
        {"kind": "node", "id": "person_1a2b3c4d", "name": "林舟", "node_type": "person"},
        {"kind": "node", "id": "event_1a2b3c4d", "name": "进入书店", "node_type": "event"},
        {"kind": "node", "id": "event_ab12cd34", "name": "离开书店", "node_type": "event"},
    ], ops.graph_project_info()["revision"])
    ops.graph_accept(candidate, 1)
    candidate = ops.property_propose([
        {"kind": "property_definition", "id": "location", "value_type": "string", "cardinality": "one"},
        {"kind": "property_value", "id": "val_b75b14be4e2a", "node_id": "person_1a2b3c4d",
         "property_id": "location", "value": "书店", "event_id": "event_1a2b3c4d",
         "evidence": {"source_id": source, "quote": "林舟离开书店。"}},
    ], ops.graph_project_info()["revision"], source="manual")
    ops.property_accept(candidate, 1)
    candidate = ops.change_propose([], [
        {"kind": "property_retraction", "id": "ret_123456789abc", "target_id": "val_b75b14be4e2a",
         "event_id": "event_ab12cd34", "reason": "已经离开原地点"},
    ], ops.graph_project_info()["revision"], source="manual", note="书店状态复核")
    rows = candidate_rows(ops.graph_snapshot(), ops.change_candidate(candidate), ops.property_records())
    assert len(rows) == 1
    assert rows[0]["id"] == "ret_123456789abc"  # Used for actions, never displayed.
    assert rows[0]["label"] == f"林舟 · {label_for('location')} = 书店"
    assert "离开书店" in rows[0]["extra"]
    assert "已经离开原地点" in rows[0]["extra"]
    assert "原文：林舟离开书店。" in rows[0]["extra"]
    assert "b75b14be4e2a" not in rows[0]["label"] + rows[0]["extra"]


def test_missing_target_is_explained_without_printing_the_private_id():
    rows = candidate_rows({}, {"property_records": [
        {"kind": "property_retraction", "id": "r", "target_id": "val_ffffffffffff",
         "event_id": "event_123456789abc", "reason": "原记录 val_ffffffffffff 不可用"},
    ]})
    assert "原记录未找到" in rows[0]["label"]
    assert "ffffffffffff" not in rows[0]["extra"]
    assert "123456789abc" not in rows[0]["extra"]


def test_candidate_graph_references_use_names_and_no_hash_fallback():
    rows = candidate_rows({"sources": [{"id": "source_123456789abc", "title": "第一章"}]}, {
        "objects": [
            {"kind": "source_order", "id": "order_123456789abc", "source_id": "source_123456789abc", "ordinal": 2},
            {"kind": "context", "id": "context_123456789abc", "label": "世界叙述", "context_type": "canon"},
            {"kind": "node", "id": "person_123456789abc", "node_type": "person"},
        ],
    })
    assert rows[0]["label"] == "第一章"
    assert rows[1]["label"] == "世界叙述"
    assert rows[2]["label"] == "未命名节点"
    assert all("123456789abc" not in row["label"] + row["extra"] for row in rows)


def test_audit_messages_resolve_ids_but_action_keys_are_unchanged():
    snapshot = {"nodes": [{"id": "person_123456789abc", "name": "林舟", "kind": "node"}]}
    report = {"issues": [{"node_id": "person_123456789abc", "message": "person_123456789abc 的值冲突",
                          "suggestion": "检查 val_ffffffffffff"}]}
    shown = humanize_report(snapshot, [], report)
    assert shown["issues"][0]["node_id"] == "person_123456789abc"
    assert shown["issues"][0]["message"] == "林舟 的值冲突"
    assert shown["issues"][0]["suggestion"] == "检查 内部引用"
    assert report["issues"][0]["message"].startswith("person_")


def test_business_numbers_and_literal_evidence_are_not_sanitized():
    literal_hash = "f" * 32
    snapshot = {"nodes": [{"id": "person_123456789abc", "kind": "node", "name": "林舟"}]}
    quote = f"电话13800138000，日期20261101，校验值{literal_hash}。"
    rows = candidate_rows(snapshot, {"property_records": [
        {"kind": "property_value", "id": "val_123456789abc", "node_id": "person_123456789abc",
         "property_id": "电话", "value": "13800138000", "evidence": {"quote": quote}},
        {"kind": "property_value", "id": "val_abcdef123456", "node_id": "person_123456789abc",
         "property_id": "日期", "value": 20261101},
        {"kind": "property_value", "id": "val_abcdef654321", "node_id": "person_123456789abc",
         "property_id": "校验值", "value": literal_hash},
    ]})
    assert "13800138000" in rows[0]["label"]
    assert quote in rows[0]["extra"]
    assert "20261101" in rows[1]["label"]
    assert literal_hash in rows[2]["label"]
    report = {"issues": [{"message": "13800138000 / 20261101 / " + "9" * 40,
                          "suggestion": "检查 val_ffffffffffff 和 " + "a" * 32}]}
    shown = humanize_report(snapshot, [], report)
    assert shown["issues"][0]["message"] == report["issues"][0]["message"]
    assert "val_ffffffffffff" not in shown["issues"][0]["suggestion"]
    assert "a" * 32 not in shown["issues"][0]["suggestion"]
