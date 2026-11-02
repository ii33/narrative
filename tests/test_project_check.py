"""Independent read-only project checking, including isolated legacy corruption."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from agent_fixtures import project as project
from test_item_possession import SENTENCES, TEXT, install


def files(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in Path(root).rglob("*")
        if p.is_file()
    }


def test_check_is_readonly_and_healthy_graph_has_no_current_errors(project):
    install(project)
    before = copy.deepcopy(project.graph_snapshot())
    raw = copy.deepcopy(project.property_records())
    root = Path(project.project_info().path).parent
    before_files = files(root)
    result = project.check_project()
    assert result["revision"] == before["revision"]
    assert not [r for r in result["issues"] if r["severity"] == "error" and not r.get("history")]
    assert project.graph_snapshot() == before
    assert project.property_records() == raw
    assert files(root) == before_files


def test_legacy_active_corruption_reports_specific_targets_without_mutation(project):
    sid, ids, _item = install(project)
    with project.project.store.connect(write=True) as db:
        scene = next(n for n in project.graph_snapshot()["nodes"] if n["node_type"] == "scene")
        scene["events"] = []
        db.execute(
            "UPDATE objects SET data=? WHERE id=?",
            (json.dumps(scene, ensure_ascii=False), scene["id"]),
        )
        item = next(n for n in project.graph_snapshot()["nodes"] if n["id"] == "I1")
        item["evidence"]["quote"] = "原文没有的引文"
        item["evidence_spans"][0]["quote"] = "另一条不存在的引文"
        db.execute(
            "UPDATE objects SET data=? WHERE id=?",
            (json.dumps(item, ensure_ascii=False), item["id"]),
        )
        # Legacy corruption is explicit test-only SQL, not a runtime bypass API.
        holder = db.execute("SELECT data FROM property_records WHERE id='holder0'").fetchone()
        old = json.loads(holder["data"])
        old["valid_to"] = {
            "source_id": sid,
            "start": TEXT.index(SENTENCES[3]),
            "event_id": ids[SENTENCES[3]],
        }
        db.execute(
            "UPDATE property_records SET data=? WHERE id=?",
            (json.dumps(old, ensure_ascii=False), old["id"]),
        )
    before = copy.deepcopy(project.graph_snapshot())
    result = project.check_project()
    assert any(
        r["code"] == "EVENT_SCENE" and r.get("object_id") in set(ids.values())
        for r in result["issues"]
    )
    assert any(
        r["code"] == "ITEM_HOLDER_REQUIRED" and (r.get("node_id") or r.get("object_id")) == "I1"
        for r in result["issues"]
    )
    assert any(
        r["severity"] == "error"
        and (r.get("node_id") or r.get("object_id")) == "I1"
        and r["code"] != "ITEM_HOLDER_REQUIRED"
        for r in result["issues"]
    )
    assert any(r.get("source_id") == sid for r in result["issues"])
    assert project.graph_snapshot() == before


def test_ended_historical_bad_record_is_not_current_dangling_error(project):
    sid, ids, _item = install(project)
    position = {"source_id": sid, "start": 0}
    bad = {
        "kind": "property_value",
        "id": "legacy_bad",
        "node_id": "missing_old_node",
        "property_id": "持有者",
        "value": "missing_old_holder",
        "event_id": ids[SENTENCES[0]],
        "disclosure": position,
        "evidence": {"source_id": sid, "quote": SENTENCES[0] + "。"},
    }
    bad["valid_from"] = {"source_id": sid, "start": 0, "event_id": ids[SENTENCES[0]]}
    bad["valid_to"] = {
        "source_id": sid,
        "start": TEXT.index(SENTENCES[1]),
        "event_id": ids[SENTENCES[1]],
    }
    with project.project.store.connect(write=True) as db:
        db.execute(
            "INSERT INTO property_records(id,kind,data,accepted_revision) VALUES (?,?,?,?)",
            (
                bad["id"],
                bad["kind"],
                json.dumps(bad, ensure_ascii=False),
                project.graph_project_info()["revision"],
            ),
        )
    result = project.check_project()
    offending = [
        r
        for r in result["issues"]
        if r.get("record_id") == "legacy_bad" or "legacy_bad" in r.get("record_ids", [])
    ]
    assert not [r for r in offending if r["severity"] == "error" and not r.get("history")]
    assert project.property_state("I1", "持有者")["state"]["value"] == "未知"


def test_checker_rejects_obsolete_merged_fields_without_following_redirects(project):
    install(project)
    people = [
        copy.deepcopy(n) for n in project.graph_snapshot()["nodes"] if n["node_type"] == "person"
    ]
    with project.project.store.connect(write=True) as db:
        for i, person in enumerate(people):
            person.update(merged_into=people[1 - i]["id"])
            db.execute(
                "UPDATE objects SET data=? WHERE id=?",
                (json.dumps(person, ensure_ascii=False), person["id"]),
            )
    result = project.check_project()
    assert any(row["code"] == "FORMAT" for row in result["issues"]), result["issues"]


def test_checker_reports_current_value_with_missing_definition(project):
    sid, ids, _item = install(project)
    row = {
        "kind": "property_value",
        "id": "legacy_missing_definition",
        "node_id": ids["甲"],
        "property_id": "未定义的属性",
        "value": "原文支持的文字",
        "event_id": ids[SENTENCES[1]],
        "disclosure": {"source_id": sid, "start": TEXT.index(SENTENCES[1])},
        "evidence": {"source_id": sid, "quote": SENTENCES[1] + "。"},
    }
    with project.project.store.connect(write=True) as db:
        db.execute(
            "INSERT INTO property_records(id,kind,data,accepted_revision) VALUES (?,?,?,?)",
            (
                row["id"],
                row["kind"],
                json.dumps(row, ensure_ascii=False),
                project.graph_project_info()["revision"],
            ),
        )
    result = project.check_project()
    assert any(
        r["code"] in {"DEFINITION", "definition_missing"}
        and (r.get("record_id") == row["id"] or row["id"] in r.get("record_ids", []))
        for r in result["issues"]
    )


def test_deleting_definition_still_used_by_current_value_is_rejected_without_writing(project):
    sid, ids, _item = install(project)
    records = [
        {"kind": "property_definition", "id": "颜色", "value_type": "string", "cardinality": "one"},
        {
            "kind": "property_value",
            "id": "stone_color",
            "node_id": "I1",
            "property_id": "颜色",
            "value": "灰",
            "event_id": ids[SENTENCES[0]],
            "evidence": {"source_id": sid, "quote": SENTENCES[0] + "。"},
            "disclosure": {"source_id": sid, "start": 0},
        },
    ]
    assert (
        project.apply_changes(
            [], records, project.graph_project_info()["revision"], reason="合法属性"
        )["status"]
        == "accepted"
    )
    before = copy.deepcopy(project.property_records())
    import pytest

    with pytest.raises(ValueError):
        project.delete_property_records(["颜色"], reason="不能留下当前取值缺定义")
    assert project.property_records() == before


def test_definition_and_all_values_can_be_deleted_atomically_without_alias_or_retirement_rows(
    project,
):
    sid, ids, _item = install(project)
    records = [
        {
            "kind": "property_definition",
            "id": "旧颜色",
            "value_type": "string",
            "cardinality": "one",
        },
        {
            "kind": "property_value",
            "id": "old_color",
            "node_id": "I1",
            "property_id": "旧颜色",
            "value": "灰",
            "event_id": ids[SENTENCES[0]],
            "evidence": {"source_id": sid, "quote": SENTENCES[0] + "。"},
            "disclosure": {"source_id": sid, "start": 0},
        },
    ]
    assert (
        project.apply_changes(
            [], records, project.graph_project_info()["revision"], reason="物理删除fixture"
        )["status"]
        == "accepted"
    )
    project.delete_property_records(["旧颜色", "old_color"], reason="确认错误定义及记录物理删除")
    assert not {"旧颜色", "old_color"}.intersection(r["id"] for r in project.property_records())
    result = project.check_project()
    assert not [r for r in result["issues"] if r["severity"] == "error"]
