"""Existing scene references in incremental extraction, through Operations only."""

from __future__ import annotations

import copy
import json

import pytest
from agent_fixtures import project as project
from test_extraction_identity import FIRST_DAY_END, Q3, draft, node, seed


def incremental(scene=None):
    event = {"type": "event", "name": "甲向乙点头", "quotes": [Q3], "participants": ["甲", "乙"]}
    if scene is not None:
        event["scene"] = scene
    return {"nodes": [event], "properties": []}


@pytest.mark.parametrize("reference", ["id", "name"])
def test_new_event_links_existing_scene_without_relisting_it(project, reference):
    sid = seed(project, draft())
    original = copy.deepcopy(node(project, "会面"))
    result = project.submit_extraction(
        sid,
        incremental(original["id"] if reference == "id" else original["name"]),
        end=FIRST_DAY_END,
    )
    assert result["status"] == "accepted"
    scene = node(project, "会面")
    new = node(project, "甲向乙点头")
    assert set(new["participants"]) == {node(project, "甲")["id"], node(project, "乙")["id"]}
    assert set(scene["events"]) == set(original["events"]) | {new["id"]}
    assert scene["evidence"] == original["evidence"]
    assert scene["disclosure"] == original["disclosure"]
    assert scene["evidence_spans"] == original["evidence_spans"]
    assert len([n for n in project.graph_snapshot()["nodes"] if n["id"] == scene["id"]]) == 1


@pytest.mark.parametrize(
    "problem", ["missing", "ambiguous", "wrong_type", "wrong_context", "retracted"]
)
def test_invalid_scene_reference_rejects_entire_increment_without_writing(project, problem):
    sid = seed(project)
    scene = copy.deepcopy(node(project, "会面"))
    reference = scene["id"]
    payload = incremental(reference)
    if problem == "missing":
        payload["nodes"][0]["scene"] = "不存在的场景"
    elif problem == "wrong_type":
        payload["nodes"][0]["scene"] = node(project, "甲")["id"]
    elif problem == "wrong_context":
        assert (
            project.apply_changes(
                [
                    {
                        "kind": "context",
                        "id": "other-context",
                        "label": "合成假设",
                        "context_type": "hypothesis",
                        "parent_id": "canon",
                    }
                ],
                [],
                project.graph_project_info()["revision"],
                reason="合成工程修订",
            )["status"]
            == "accepted"
        )
        payload["nodes"][0]["context_id"] = "other-context"
    else:
        if problem == "ambiguous":
            scene["id"] = "S99"
            payload["nodes"][0]["scene"] = scene["name"]
        else:
            scene["retracted"] = True
        objects = [scene]
        if problem == "retracted":
            objects.append(scene | {"id": "S98", "name": "另一个合法场景", "retracted": False})
        assert (
            project.apply_changes(
                objects, [], project.graph_project_info()["revision"], reason="合成工程修订"
            )["status"]
            == "accepted"
        )
    before = copy.deepcopy(project.graph_snapshot())
    assert not project.preview_extraction(sid, payload, end=FIRST_DAY_END)["valid"]
    assert project.submit_extraction(sid, payload, end=FIRST_DAY_END)["status"] == "rejected"
    assert project.graph_snapshot() == before


def test_event_without_scene_is_rejected_without_writing(project):
    sid = seed(project)
    before = copy.deepcopy(project.graph_snapshot())
    assert not project.preview_extraction(sid, incremental(), end=FIRST_DAY_END)["valid"]
    assert project.submit_extraction(sid, incremental(), end=FIRST_DAY_END)["status"] == "rejected"
    assert project.graph_snapshot() == before


PARENT_TEXT = "甲遇见乙。甲向乙点头。甲告别乙。甲挥手。"


def seed_adjacent_big_events(project, *, same_name=False):
    """Two valid adjacent B containers; each owns distinct located scene/event."""
    sid = project.import_source("合成连续大事件", PARENT_TEXT)

    def make(identifier, kind, name, quote, **extra):
        start = PARENT_TEXT.index(quote)
        return {
            "kind": "node",
            "id": identifier,
            "node_type": kind,
            "name": name,
            "context_id": "canon",
            "disclosure": {"source_id": sid, "start": start},
            "evidence": {"source_id": sid, "quote": quote},
            "evidence_spans": [
                {"source_id": sid, "start": start, "end": start + len(quote), "quote": quote}
            ],
            **extra,
        }

    objects = [make("P1", "person", "甲", "甲遇见乙。"), make("P2", "person", "乙", "甲遇见乙。")]
    for number, name, quote in [
        (1, "相遇经过", "甲遇见乙。"),
        (2, "相遇经过" if same_name else "告别经过", "甲告别乙。"),
    ]:
        objects.extend(
            [
                make(f"B{number}", "big_event", name, quote, events=[f"S{number}"]),
                make(f"S{number}", "scene", f"阶段场景{number}", quote, events=[f"E{number}"]),
                make(f"E{number}", "event", quote[:-1], quote, participants=["P1", "P2"]),
            ]
        )
    result = project.apply_changes(
        objects,
        [],
        project.graph_project_info()["revision"],
        reason="合成两个相邻而不共享成员的大事件",
    )
    assert result["status"] == "accepted", result
    assert not [r for r in project.check_project()["issues"] if r["severity"] == "error"]
    ranges = project.graph_event_lines()["big_events"]
    assert ranges[0]["end"] == ranges[1]["start"] and ranges[0]["start"] != ranges[1]["start"]
    return sid


def child_scene_payload(parent):
    return {
        "nodes": [
            {"type": "scene", "name": "点头回应", "big_event": parent, "quotes": ["甲向乙点头。"]},
            {
                "type": "event",
                "name": "甲向乙点头",
                "scene": "@1",
                "participants": ["P1", "P2"],
                "quotes": ["甲向乙点头。"],
            },
        ],
        "properties": [],
    }


def test_new_scene_references_existing_big_event_without_relisting_parent(project):
    sid = seed_adjacent_big_events(project)
    parent = copy.deepcopy(node(project, "相遇经过"))
    other = copy.deepcopy(node(project, "告别经过"))
    assert (
        project.submit_extraction(sid, child_scene_payload(parent["name"]))["status"] == "accepted"
    )
    updated = node(project, "相遇经过")
    assert set(updated["events"]) == set(parent["events"]) | {node(project, "点头回应")["id"]}
    assert all(
        updated.get(k) == parent.get(k) for k in ("evidence", "disclosure", "evidence_spans")
    )
    assert node(project, "告别经过") == other
    assert len([n for n in project.graph_snapshot()["nodes"] if n["node_type"] == "big_event"]) == 2


def test_ambiguous_adjacent_existing_big_events_need_explicit_id(project):
    sid = seed_adjacent_big_events(project, same_name=True)
    parents = {
        n["id"]: copy.deepcopy(n)
        for n in project.graph_snapshot()["nodes"]
        if n["node_type"] == "big_event"
    }
    payload = child_scene_payload("相遇经过")
    before = copy.deepcopy(project.graph_snapshot())
    result = project.submit_extraction(sid, payload)
    assert result["status"] == "rejected" and result["diagnostics"]
    assert project.graph_snapshot() == before
    payload["nodes"][0]["big_event"] = "B1"
    assert project.submit_extraction(sid, payload)["status"] == "accepted"
    current = {n["id"]: n for n in project.graph_snapshot()["nodes"]}
    assert set(current["B1"]["events"]) == set(parents["B1"]["events"]) | {
        node(project, "点头回应")["id"]
    }
    assert current["B2"] == parents["B2"]
    assert all(
        current["B1"].get(k) == parents["B1"].get(k)
        for k in ("evidence", "disclosure", "evidence_spans")
    )
    assert not [r for r in project.check_project()["issues"] if r["severity"] == "error"]


def test_explicit_existing_orphan_can_be_attached_without_duplicate_or_property_changes(project):
    sid = seed(project)
    assert (
        project.submit_extraction(sid, incremental(node(project, "会面")["id"]), end=FIRST_DAY_END)[
            "status"
        ]
        == "accepted"
    )
    orphan = copy.deepcopy(node(project, "甲向乙点头"))
    definition = {
        "kind": "property_definition",
        "id": "原有动作",
        "value_type": "string",
        "cardinality": "one",
    }
    value = {
        "kind": "property_value",
        "id": "orphan_value",
        "node_id": orphan["id"],
        "event_id": orphan["id"],
        "property_id": "原有动作",
        "value": "点头",
        "evidence": orphan["evidence"],
        "disclosure": orphan["disclosure"],
    }
    assert (
        project.apply_changes(
            [], [definition, value], project.graph_project_info()["revision"], reason="合成工程修订"
        )["status"]
        == "accepted"
    )
    # Authorized isolated legacy fixture: emulate old writer's dropped scene edge.
    # This SQL is test-only historical corruption, never a product creation path.
    scene = copy.deepcopy(node(project, "会面"))
    scene["events"].remove(orphan["id"])
    with project.project.store.connect(write=True) as db:
        db.execute(
            "UPDATE objects SET data=? WHERE id=?",
            (json.dumps(scene, ensure_ascii=False), scene["id"]),
        )
    records = copy.deepcopy(project.property_records())
    payload = incremental(node(project, "会面")["id"])
    payload["nodes"][0]["existing_id"] = orphan["id"]
    assert project.submit_extraction(sid, payload, end=FIRST_DAY_END)["status"] == "accepted"
    assert node(project, "甲向乙点头")["id"] == orphan["id"]
    assert orphan["id"] in node(project, "会面")["events"]
    assert len([n for n in project.graph_snapshot()["nodes"] if n["node_type"] == "event"]) == 2
    assert project.property_records() == records
    before = copy.deepcopy(project.graph_snapshot())
    assert project.submit_extraction(sid, payload, end=FIRST_DAY_END)["status"] == "unchanged"
    assert project.graph_snapshot() == before


def test_cached_submission_does_not_silently_ignore_changed_declared_parent(project):
    sid = seed(project)
    payload = incremental(node(project, "会面")["id"])
    assert project.submit_extraction(sid, payload, end=FIRST_DAY_END)["status"] == "accepted"
    event = node(project, "甲向乙点头")
    old = copy.deepcopy(node(project, "会面"))
    old["events"].remove(event["id"])
    other = copy.deepcopy(old) | {"id": "S98", "name": "另一场景", "events": [event["id"]]}
    assert (
        project.apply_changes(
            [old, other], [], project.graph_project_info()["revision"], reason="合成工程修订"
        )["status"]
        == "accepted"
    )
    before = copy.deepcopy(project.graph_snapshot())
    result = project.submit_extraction(sid, payload, end=FIRST_DAY_END)
    assert result["status"] == "rejected" and not result.get("reused")
    assert result["diagnostics"]
    assert project.graph_snapshot() == before
