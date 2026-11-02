"""Action nodes are explicit event children, with source-ordered reading anchors."""

from __future__ import annotations

import copy

import pytest
from agent_fixtures import project as project

TEXT = "甲进屋。甲停下。"
FIRST, SECOND = "甲进屋。", "甲停下。"


def base(project):
    from narrative.graph_models import Node

    sid = project.import_source("合成动作原文", TEXT)

    def obj(identifier, kind, name, quote, **fields):
        start = TEXT.index(quote)
        return Node.model_validate(
            {
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
                **fields,
            }
        ).model_dump()

    objects = [
        obj("P1", "person", "甲", "甲"),
        obj("S1", "scene", "进屋停步", TEXT, events=["E9"]),
        obj("E9", "event", "甲进屋并停下", TEXT, participants=["P1"]),
    ]
    assert (
        project.apply_changes(
            objects, [], project.graph_project_info()["revision"], reason="仅事件无动作的正常初始图"
        )["status"]
        == "accepted"
    )
    return sid, obj


def action_batch(project):
    sid, obj = base(project)
    event = copy.deepcopy(next(n for n in project.graph_snapshot()["nodes"] if n["id"] == "E9"))
    event["events"] = ["A99", "A1"]
    actions = [
        obj("A99", "action", "走进屋", FIRST, participants=["P1"]),
        obj("A1", "action", "停下", SECOND, participants=["P1"]),
    ]
    return sid, obj, [event, *actions]


def test_event_creation_does_not_implicitly_materialize_actions(project):
    base(project)
    assert not [n for n in project.graph_snapshot()["nodes"] if n["node_type"] == "action"]


def test_explicit_actions_parent_reading_anchor_and_value_intervals_follow_source_order(project):
    sid, _obj, objects = action_batch(project)
    records = [
        {
            "kind": "property_definition",
            "id": "动作状态",
            "value_type": "string",
            "cardinality": "one",
        }
    ]
    records += [
        {
            "kind": "property_value",
            "id": f"action_state_{i}",
            "node_id": "P1",
            "property_id": "动作状态",
            "value": value,
            "event_id": anchor,
            "disclosure": {"source_id": sid, "start": TEXT.index(quote)},
            "evidence": {"source_id": sid, "quote": quote},
        }
        for i, (anchor, quote, value) in enumerate([("A99", FIRST, "行走"), ("A1", SECOND, "站定")])
    ]
    result = project.apply_changes(
        objects, records, project.graph_project_info()["revision"], reason="用户明确请求的动作细化"
    )
    assert result["status"] == "accepted", result
    assert project.property_state("P1", "动作状态", at="A99")["state"]["value"] == "行走"
    assert project.property_state("P1", "动作状态", at="E9")["state"]["value"] == "站定"
    view = project.browse_view(granularity="event", cut_id="E9")
    assert view["cut"]["event_id"] == "A1"
    assert any(level["key"] == "action" for level in view["moments"]["levels"])
    raw = next(r for r in project.property_records() if r["id"] == "action_state_0")
    assert raw["valid_to"]["start"] == TEXT.index(SECOND)
    assert not [r for r in project.check_project()["issues"] if r["severity"] == "error"]


@pytest.mark.parametrize(
    "problem",
    [
        "orphan",
        "multiple_events",
        "scene_direct",
        "storyline_direct",
        "nested_action",
        "other_context",
    ],
)
def test_invalid_action_containment_is_rejected_without_writing(project, problem):
    sid, obj, objects = action_batch(project)
    event, first, second = objects
    if problem == "orphan":
        event["events"] = []
    elif problem == "multiple_events":
        extra = obj("E8", "event", "同场另事件", TEXT, events=["A99"])
        scene = copy.deepcopy(next(n for n in project.graph_snapshot()["nodes"] if n["id"] == "S1"))
        scene["events"].append("E8")
        objects.extend([extra, scene])
    elif problem == "scene_direct":
        event["events"] = ["A1"]
        scene = copy.deepcopy(next(n for n in project.graph_snapshot()["nodes"] if n["id"] == "S1"))
        scene["events"].append("A99")
        objects.append(scene)
    elif problem == "storyline_direct":
        event["events"] = ["A1"]
        objects.append(obj("T1", "storyline", "不合法动作父层", TEXT, events=["A99"]))
    elif problem == "nested_action":
        event["events"] = ["A99"]
        first["events"] = ["A1"]
    else:
        objects.append(
            {
                "kind": "context",
                "id": "other",
                "label": "假设",
                "context_type": "hypothesis",
                "parent_id": "canon",
            }
        )
        first["context_id"] = "other"
    before = copy.deepcopy(project.graph_snapshot())
    result = project.apply_changes(
        objects, [], project.graph_project_info()["revision"], reason="错误包含关系应原子拒绝"
    )
    assert result["status"] == "rejected" and any(
        r["code"].startswith("ACTION_") for r in result["diagnostics"]
    )
    assert project.graph_snapshot() == before


def test_rollback_removes_later_action_and_retains_parent_event_and_prior_action(project):
    sid, _obj, objects = action_batch(project)
    assert (
        project.apply_changes(
            objects, [], project.graph_project_info()["revision"], reason="动作种子"
        )["status"]
        == "accepted"
    )
    result = project.rollback_to(
        sid, len(FIRST) - 1, project.graph_project_info()["revision"], reason="回到第一动作之后"
    )
    assert result["status"] == "accepted"
    nodes = {n["id"]: n for n in project.graph_snapshot()["nodes"]}
    assert nodes["E9"]["events"] == ["A99"] and "A1" not in nodes
    assert not [r for r in project.check_project()["issues"] if r["severity"] == "error"]


def test_action_can_be_temporal_boundary_for_another_action(project):
    _sid, _obj, objects = action_batch(project)
    objects[1]["occurrence"] = {
        "kind": "point",
        "point": {"form": "event_boundary", "event_id": "A1", "boundary": "start"},
    }
    result = project.apply_changes(
        objects, [], project.graph_project_info()["revision"], reason="动作节点可以被时间引用"
    )
    assert result["status"] == "accepted", result


@pytest.mark.parametrize("parent", ["E9", "甲进屋并停下"])
def test_action_draft_references_existing_event_without_relisting_it(project, parent):
    sid, _obj = base(project)
    original = copy.deepcopy(next(n for n in project.graph_snapshot()["nodes"] if n["id"] == "E9"))
    payload = {
        "nodes": [{"type": "action", "name": "走进屋内", "event": parent, "quotes": [FIRST]}],
        "properties": [],
    }
    result = project.submit_extraction(sid, payload)
    assert result["status"] == "accepted", result
    nodes = project.graph_snapshot()["nodes"]
    action = next(n for n in nodes if n["node_type"] == "action")
    assert action["id"].startswith("A")
    updated = next(n for n in nodes if n["id"] == "E9")
    assert updated["events"] == [action["id"]] and updated["evidence"] == original["evidence"]
