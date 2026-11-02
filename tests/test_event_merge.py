"""Schema-8 physical event merge preserves facts while replacing all typed IDs."""

from __future__ import annotations

import copy

import pytest
from agent_fixtures import project as project
from test_current_state_contract import QUOTES, TEXT, setup


def seed(ops):
    sid, ids = setup(ops)
    snapshot = ops.graph_snapshot()
    target = copy.deepcopy(next(n for n in snapshot["nodes"] if n["id"] == ids[QUOTES[0][:-1]]))
    source = target | {
        "id": "E99",
        "name": "首句同次动作",
        "participants": [ids["甲"]],
        "source_marks": [{"source_id": sid, "role": "end", "label": "E99是文字"}],
    }
    scene = copy.deepcopy(next(n for n in snapshot["nodes"] if n["node_type"] == "scene"))
    scene["events"].append(source["id"])
    definitions = [
        {"kind": "property_definition", "id": name, "value_type": kind, "cardinality": "one"}
        for name, kind in [
            ("标注", "string"),
            ("指向", "node"),
            ("或指向", "either"),
            ("结构", "any"),
            ("认知", "string"),
        ]
    ]
    common = {
        "kind": "property_value",
        "node_id": ids["甲"],
        "event_id": "E99",
        "evidence": {"source_id": sid, "quote": QUOTES[0]},
        "disclosure": {"source_id": sid, "start": TEXT.index(QUOTES[0])},
    }
    values = [
        common
        | {
            "id": "subject",
            "node_id": "E99",
            "property_id": "标注",
            "value": "E99",
            "applies_to": "E99",
            "related_nodes": ["E99", ids["甲"]],
        },
        common | {"id": "node_value", "property_id": "指向", "value": "E99"},
        common | {"id": "either_value", "property_id": "或指向", "value": "E99"},
        common
        | {
            "id": "json_value",
            "property_id": "结构",
            "value": {"event_id": "E99", "events": ["E99"], "nested": {"node_id": "E99"}},
        },
        common
        | {
            "id": "mind",
            "property_id": "认知",
            "value": "E99仅为文字",
            "scope": "cognition",
            "holder_id": ids["甲"],
        },
        common
        | {
            "id": "ended",
            "property_id": "标注",
            "value": "结束的文字E99",
            "node_id": ids["甲"],
            "valid_to": {"source_id": sid, "start": TEXT.index(QUOTES[1]), "event_id": "E99"},
        },
    ]
    assert (
        ops.apply_changes(
            [source, scene],
            definitions + values,
            ops.graph_project_info()["revision"],
            reason="隔离物理合并fixture",
        )["status"]
        == "accepted"
    )
    proof = [
        {
            "source_id": sid,
            "start": TEXT.index(QUOTES[0]),
            "end": TEXT.index(QUOTES[0]) + len(QUOTES[0]),
            "quote": QUOTES[0],
        }
    ]
    return sid, ids, target, source, proof


def test_physical_merge_rewrites_current_and_ended_typed_references_not_literals(project):
    _sid, ids, target, source, proof = seed(project)
    before = copy.deepcopy(project.graph_snapshot())
    records = copy.deepcopy(project.property_records())
    revision = project.graph_project_info()["revision"]
    preview = project.preview_event_merge(
        source["id"], target["id"], revision, proof, "同一次原文动作"
    )
    assert preview["valid"] and project.graph_snapshot() == before
    assert project.property_records() == records
    result = project.merge_events(source["id"], target["id"], revision, proof, "同一次原文动作")
    assert result["status"] == "accepted" and result["physical_merge"]
    nodes = project.graph_snapshot()["nodes"]
    assert source["id"] not in {n["id"] for n in nodes}
    merged = next(n for n in nodes if n["id"] == target["id"])
    assert merged["evidence"] == target["evidence"] and merged["disclosure"] == target["disclosure"]
    assert ids["甲"] in merged["participants"]
    assert source["source_marks"][0] in merged["source_marks"]
    assert not any(n.get("merged_into") for n in nodes)
    rows = {r["id"]: r for r in project.property_records()}
    assert (
        rows["subject"]["node_id"] == target["id"] and rows["subject"]["applies_to"] == target["id"]
    )
    assert rows["subject"]["related_nodes"] == [target["id"], ids["甲"]]
    assert rows["subject"]["value"] == "E99"
    assert rows["node_value"]["value"] == target["id"] == rows["either_value"]["value"]
    assert rows["json_value"]["value"] == next(
        r["value"] for r in records if r["id"] == "json_value"
    )
    assert rows["mind"]["value"] == "E99仅为文字" and rows["mind"]["holder_id"] == ids["甲"]
    for row in rows.values():
        if row.get("event_id"):
            assert row["event_id"] != "E99"
        for field in ("valid_from", "valid_to"):
            if row.get(field):
                assert row[field].get("event_id") != "E99"
        assert "original_references" not in row
    assert rows["ended"]["valid_to"]["start"] == TEXT.index(QUOTES[1])
    with pytest.raises(ValueError):
        project.merge_events(
            source["id"],
            target["id"],
            project.graph_project_info()["revision"],
            proof,
            "不存在源不能留下别名链",
        )


def test_failure_inside_physical_merge_transaction_restores_all_rows_and_revision(project):
    _sid, _ids, target, source, proof = seed(project)
    before = copy.deepcopy(project.graph_snapshot())
    records = copy.deepcopy(project.property_records())
    revision = project.graph_project_info()["revision"]
    with project.project.store.connect(write=True) as db:
        db.execute(
            "CREATE TRIGGER qa_fail_delete BEFORE DELETE ON objects WHEN OLD.id='E99' BEGIN SELECT RAISE(ABORT,'isolated write failure'); END;"
        )
    with pytest.raises(Exception, match="isolated write failure"):
        project.merge_events(source["id"], target["id"], revision, proof, "模拟存储失败原子回滚")
    assert project.graph_snapshot() == before
    assert project.property_records() == records
    assert project.graph_project_info()["revision"] == revision


@pytest.mark.parametrize(
    "problem",
    [
        "revision",
        "proof",
        "self",
        "type",
        "occurrence",
        "context",
        "scene",
        "source",
        "direct_order",
    ],
)
def test_physical_merge_rejects_conflicts_without_writing(project, problem):
    sid, ids, target, source, proof = seed(project)
    expected = project.graph_project_info()["revision"]
    source_id, target_id = source["id"], target["id"]
    if problem == "revision":
        expected -= 1
    elif problem == "proof":
        proof = [proof[0] | {"quote": "原文不符"}]
    elif problem == "self":
        target_id = source_id
    elif problem == "type":
        target_id = ids["甲"]
    else:
        updated = copy.deepcopy(source)
        objects = []
        if problem == "occurrence":
            updated["occurrence"] = {
                "kind": "point",
                "point": {"timeline_id": "story", "form": "number", "number": 2},
            }
        elif problem == "context":
            objects.append(
                {
                    "kind": "context",
                    "id": "other",
                    "label": "假设",
                    "context_type": "hypothesis",
                    "parent_id": "canon",
                }
            )
            updated["context_id"] = "other"
            alternate = copy.deepcopy(
                next(n for n in project.graph_snapshot()["nodes"] if n["node_type"] == "scene")
            )
            alternate.update(id="S99", context_id="other", events=[source_id])
            objects.append(alternate)
        elif problem == "source":
            other = project.import_source("另一来源", TEXT)
            updated["disclosure"] = updated["disclosure"] | {"source_id": other}
            updated["evidence"] = updated["evidence"] | {"source_id": other}
            updated["evidence_spans"] = [
                span | {"source_id": other} for span in updated["evidence_spans"]
            ]
            proof = [*proof, proof[0] | {"source_id": other}]
        elif problem == "scene":
            original = copy.deepcopy(
                next(n for n in project.graph_snapshot()["nodes"] if n["node_type"] == "scene")
            )
            original["events"].remove(source_id)
            objects.extend(
                [original, original | {"id": "S99", "name": "另一场景", "events": [source_id]}]
            )
        else:
            updated["predecessors"] = [target_id]
        objects.append(updated)
        result = project.apply_changes(
            objects, [], project.graph_project_info()["revision"], reason="明确不同身份或结构"
        )
        assert result["status"] == "accepted", result
        expected = project.graph_project_info()["revision"]
    before = copy.deepcopy(project.graph_snapshot())
    records = copy.deepcopy(project.property_records())
    with pytest.raises(ValueError):
        project.merge_events(source_id, target_id, expected, proof, "冲突不合并")
    assert (
        project.graph_snapshot() == before
        and project.property_records() == records
    )
