"""Current nodes, intrinsic value intervals and physical suffix rollback."""

from __future__ import annotations

import copy
import sqlite3

import pytest
from agent_fixtures import project as project

from narrative.graph_store import GRAPH_SCHEMA_VERSION

TEXT = "😀甲先称A。后来改称B。再改称C。"
QUOTES = ["甲先称A。", "后来改称B。", "再改称C。"]


def setup(project):
    sid = project.import_source("同一人的称呼变化", TEXT)
    from narrative.graph_models import Node

    def obj(identifier, kind, name, quote, **extra):
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
                **extra,
            }
        ).model_dump()

    nodes = [
        obj("P1", "person", "甲", QUOTES[0]),
        obj("S1", "scene", "称呼变化", QUOTES[0], events=["E1", "E2", "E3"]),
    ]
    nodes += [
        obj(f"E{i + 1}", "event", q[:-1], q, participants=["P1"]) for i, q in enumerate(QUOTES)
    ]
    definition = {
        "kind": "property_definition",
        "id": "称呼",
        "value_type": "string",
        "cardinality": "one",
    }
    result = project.apply_changes(
        nodes,
        [definition],
        project.graph_project_info()["revision"],
        reason="合成当前对象与时间区间定义",
    )
    assert result["status"] == "accepted", result
    ids = {n["name"]: n["id"] for n in project.graph_snapshot()["nodes"]}
    return sid, ids


def add_value(project, sid, ids, index):
    quote = QUOTES[index]
    row = {
        "kind": "property_value",
        "id": f"v{index}",
        "node_id": ids["甲"],
        "property_id": "称呼",
        "value": "ABC"[index],
        "event_id": ids[quote[:-1]],
        "disclosure": {"source_id": sid, "start": TEXT.index(quote)},
        "evidence": {"source_id": sid, "quote": quote},
    }
    assert (
        project.apply_changes(
            [], [row], project.graph_project_info()["revision"], reason="合成值披露"
        )["status"]
        == "accepted"
    )
    return row


def raw_values(project):
    return {r["id"]: r for r in project.property_records() if r["kind"] == "property_value"}


def test_a_b_c_are_direct_value_intervals_without_retraction_objects(project):
    sid, ids = setup(project)
    for i in range(3):
        add_value(project, sid, ids, i)
    rows = raw_values(project)
    for i in range(3):
        assert rows[f"v{i}"]["valid_from"]["source_id"] == sid
        assert rows[f"v{i}"]["valid_from"]["start"] == TEXT.index(QUOTES[i])
        end = rows[f"v{i}"].get("valid_to")
        assert (
            (end is None)
            if i == 2
            else (end["source_id"] == sid and end["start"] == TEXT.index(QUOTES[i + 1]))
        )
        state = project.property_state(ids["甲"], "称呼", at=ids[QUOTES[i][:-1]])
        assert state["state"]["value"] == "ABC"[i]
    assert not any(r["kind"] == "property_retraction" for r in project.property_records())


def test_node_metadata_updates_replace_current_object_without_revision_replay_storage(project):
    _sid, ids = setup(project)
    before = project.graph_snapshot()
    node = copy.deepcopy(next(n for n in before["nodes"] if n["id"] == ids["甲"]))
    node["identity_note"] = "当前元数据更新"
    assert (
        project.apply_changes(
            [node], [], project.graph_project_info()["revision"], reason="更新当前对象"
        )["status"]
        == "accepted"
    )
    after = project.graph_snapshot()
    assert len(after["nodes"]) == len(before["nodes"])
    assert (
        next(n for n in after["nodes"] if n["id"] == node["id"])["identity_note"]
        == "当前元数据更新"
    )
    with sqlite3.connect(project.project.store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == GRAPH_SCHEMA_VERSION
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "revisions" not in names
        assert not any("before" in name or "history" in name or "version" in name for name in names)
    with pytest.raises((TypeError, ValueError)):
        project.graph_snapshot(as_of_revision=before["revision"])


def test_suffix_rollback_deletes_only_later_facts_and_reopens_previous_boundary(project):
    sid, ids = setup(project)
    for i in range(3):
        add_value(project, sid, ids, i)
    before = copy.deepcopy(project.graph_snapshot())
    revision = project.graph_project_info()["revision"]
    cut = TEXT.index(QUOTES[1])
    preview = project.rollback_preview(sid, cut, revision)
    assert (
        project.graph_snapshot() == before and project.graph_project_info()["revision"] == revision
    )
    assert preview
    project.rollback_to(sid, cut, revision, reason="隔离回退到B披露点")
    values = raw_values(project)
    assert set(values) == {"v0", "v1"}
    assert values["v0"]["valid_to"]["start"] == cut and values["v1"].get("valid_to") is None
    assert project.property_state(ids["甲"], "称呼")["state"]["value"] == "B"
    snapshot = project.graph_snapshot()
    assert ids[QUOTES[2][:-1]] not in {n["id"] for n in snapshot["nodes"]}
    assert all(
        child in {n["id"] for n in snapshot["nodes"]}
        for n in snapshot["nodes"]
        for child in n.get("events", [])
    )
    assert not [r for r in project.check_project()["issues"] if r["severity"] == "error"]


@pytest.mark.parametrize("bad_start", [-1, len(TEXT) + 1, True])
def test_invalid_unicode_boundary_rollback_is_atomic(project, bad_start):
    sid, ids = setup(project)
    add_value(project, sid, ids, 0)
    before = copy.deepcopy(project.graph_snapshot())
    records = copy.deepcopy(project.property_records())
    with pytest.raises((ValueError, TypeError)):
        project.rollback_to(
            sid, bad_start, project.graph_project_info()["revision"], reason="非法位置不允许写"
        )
    assert project.graph_snapshot() == before and project.property_records() == records


def test_stale_expected_revision_rollback_is_atomic(project):
    sid, ids = setup(project)
    revision = project.graph_project_info()["revision"]
    add_value(project, sid, ids, 0)
    before = copy.deepcopy(project.graph_snapshot())
    with pytest.raises(ValueError):
        project.rollback_to(sid, 1, revision, reason="过期预期不能回退")
    assert project.graph_snapshot() == before


def test_physical_event_merge_deletes_source_and_rewrites_references_without_alias_chain(project):
    sid, ids = setup(project)
    snapshot = project.graph_snapshot()
    target = copy.deepcopy(next(n for n in snapshot["nodes"] if n["id"] == ids[QUOTES[0][:-1]]))
    duplicate = target | {"id": "E99"}
    scene = copy.deepcopy(next(n for n in snapshot["nodes"] if n["node_type"] == "scene"))
    scene["events"] = [*scene["events"], "E99"]
    prop = {
        "kind": "property_definition",
        "id": "触发点",
        "value_type": "string",
        "cardinality": "one",
    }
    value = {
        "kind": "property_value",
        "id": "merge_ref",
        "node_id": ids["甲"],
        "property_id": "触发点",
        "value": "A",
        "event_id": "E99",
        "disclosure": {"source_id": sid, "start": TEXT.index(QUOTES[0])},
        "evidence": {"source_id": sid, "quote": QUOTES[0]},
    }
    assert (
        project.apply_changes(
            [duplicate, scene],
            [prop, value],
            project.graph_project_info()["revision"],
            reason="隔离重复种子",
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
    project.merge_events(
        "E99",
        target["id"],
        project.graph_project_info()["revision"],
        proof,
        reason="明确同次事件物理合并",
    )
    after = project.graph_snapshot()
    assert "E99" not in {n["id"] for n in after["nodes"]}
    assert not after.get("canonical_ids")
    assert not any(n.get("merged_into") for n in after["nodes"])
    assert (
        next(r for r in project.property_records() if r["id"] == "merge_ref")["event_id"]
        == target["id"]
    )
    assert all("E99" not in n.get("events", []) for n in after["nodes"])
    with project.project.store.connect() as db:
        assert db.execute("SELECT 1 FROM objects WHERE id='E99'").fetchone() is None


def test_source_order_precedes_unicode_offset_for_intervals_and_suffix_rollback(project):
    sid, ids = setup(project)
    for i in range(3):
        add_value(project, sid, ids, i)
    later_text = "😀甲改称D。"
    later = project.import_source("后导入原文", later_text)
    payload = {
        "nodes": [
            {"type": "person", "name": "甲", "existing_id": ids["甲"], "quotes": [later_text]},
            {"type": "scene", "name": "后来称呼", "quotes": [later_text]},
            {"type": "event", "name": "改称D", "scene": "后来称呼", "quotes": [later_text]},
        ],
        "properties": [
            {
                "node": "甲",
                "property": "称呼",
                "value": "D",
                "value_type": "string",
                "at_event": "改称D",
                "quotes": [later_text],
            }
        ],
    }
    result = project.submit_extraction(later, payload)
    assert result["status"] == "accepted", str(result.get("diagnostics"))
    assert project.property_state(ids["甲"], "称呼")["state"]["value"] == "D"
    assert (
        project.property_state(ids["甲"], "称呼", at={"source_id": sid, "start": len(TEXT)})[
            "state"
        ]["value"]
        == "C"
    )
    assert (
        project.property_state(ids["甲"], "称呼", at={"source_id": later, "start": 0})["state"][
            "value"
        ]
        == "D"
    )
    project.rollback_to(
        sid, len(TEXT), project.graph_project_info()["revision"], reason="源顺序优先于第二源偏移0"
    )
    assert project.property_state(ids["甲"], "称呼")["state"]["value"] == "C"
    assert raw_values(project)["v2"].get("valid_to") is None
    assert not any(n["name"] == "改称D" for n in project.graph_snapshot()["nodes"])


def test_property_rename_and_merge_physically_rewrite_names_without_alias_rows(project):
    sid, ids = setup(project)
    for i in range(3):
        add_value(project, sid, ids, i)
    project.rename_property(
        "称呼", "临时称呼", "当前定义物理改名", project.graph_project_info()["revision"]
    )
    target = {
        "kind": "property_definition",
        "id": "正式称呼",
        "value_type": "string",
        "cardinality": "one",
    }
    assert (
        project.apply_changes(
            [], [target], project.graph_project_info()["revision"], reason="合并目标定义"
        )["status"]
        == "accepted"
    )
    project.merge_properties(
        "临时称呼", "正式称呼", "同类定义物理合并", project.graph_project_info()["revision"]
    )
    rows = project.property_records()
    assert not any(
        row["kind"] in {"property_merge", "property_rename", "property_retraction"} for row in rows
    )
    assert not any(
        row["kind"] == "property_definition" and row["id"] in {"称呼", "临时称呼"} for row in rows
    )
    assert {row["property_id"] for row in rows if row["kind"] == "property_value"} == {"正式称呼"}
    assert project.property_state(ids["甲"], "正式称呼")["state"]["value"] == "C"


def test_default_world_and_explicit_world_share_the_same_physical_interval_group(project):
    sid, ids = setup(project)
    old = add_value(project, sid, ids, 0)
    quote = QUOTES[1]
    new = old | {
        "id": "world_explicit",
        "value": "B",
        "scope": "world",
        "event_id": ids[quote[:-1]],
        "disclosure": {"source_id": sid, "start": TEXT.index(quote)},
        "evidence": {"source_id": sid, "quote": quote},
    }
    assert (
        project.apply_changes(
            [], [new], project.graph_project_info()["revision"], reason="显式world与默认world同组"
        )["status"]
        == "accepted"
    )
    assert raw_values(project)["v0"]["valid_to"]["start"] == TEXT.index(quote)
    assert raw_values(project)["v0"]["valid_to"]["source_id"] == sid


@pytest.mark.parametrize("shape", ["one_to_many", "node_to_either"])
def test_same_batch_updated_definition_controls_real_interval_expiry(project, shape):
    sid, ids = setup(project)
    if shape == "one_to_many":
        initial = {
            "kind": "property_definition",
            "id": "标签",
            "value_type": "any",
            "cardinality": "one",
        }
        old = {
            "kind": "property_value",
            "id": "list_payload_old",
            "node_id": ids["甲"],
            "property_id": "标签",
            "value": ["A"],
            "event_id": ids[QUOTES[0][:-1]],
            "disclosure": {"source_id": sid, "start": TEXT.index(QUOTES[0])},
            "evidence": {"source_id": sid, "quote": QUOTES[0]},
        }
        assert (
            project.apply_changes(
                [],
                [initial, old],
                project.graph_project_info()["revision"],
                reason="one型any合法列表载荷",
            )["status"]
            == "accepted"
        )
        definition = initial | {"cardinality": "many"}
        expected_expiry = False
    else:
        definition = {
            "kind": "property_definition",
            "id": "指向",
            "value_type": "node",
            "cardinality": "one",
        }
        old = {
            "kind": "property_value",
            "id": "node_typed_old",
            "node_id": ids["称呼变化"],
            "property_id": "指向",
            "value": ids["甲"],
            "event_id": ids[QUOTES[0][:-1]],
            "disclosure": {"source_id": sid, "start": TEXT.index(QUOTES[0])},
            "evidence": {"source_id": sid, "quote": QUOTES[0]},
        }
        assert (
            project.apply_changes(
                [], [definition, old], project.graph_project_info()["revision"], reason="具名旧值"
            )["status"]
            == "accepted"
        )
        definition = definition | {"value_type": "either"}
        expected_expiry = True
    quote = QUOTES[1]
    new = old | {
        "id": "new_shape_value",
        "value": ["B"] if shape == "one_to_many" else "无",
        "event_id": ids[quote[:-1]],
        "disclosure": {"source_id": sid, "start": TEXT.index(quote)},
        "evidence": {"source_id": sid, "quote": quote},
    }
    result = project.apply_changes(
        [],
        [definition, new],
        project.graph_project_info()["revision"],
        reason="本批新shape必须驱动物理expiry",
    )
    assert result["status"] == "accepted", result
    rows = raw_values(project)
    if expected_expiry:
        assert rows[old["id"]]["valid_to"]["start"] == TEXT.index(quote)
    else:
        assert rows[old["id"]].get("valid_to") is None
    assert rows[new["id"]].get("valid_to") is None


def test_expiry_without_event_anchor_is_respected_by_source_point_and_event_cut_reads(project):
    sid, ids = setup(project)
    add_value(project, sid, ids, 0)
    boundary = TEXT.index(QUOTES[1])
    project.expire_property("v0", sid, boundary, reason="只有明确原文坐标的失效")
    assert raw_values(project)["v0"]["valid_to"] == {"source_id": sid, "start": boundary}
    at = project.property_state(ids["甲"], "称呼", at={"source_id": sid, "start": boundary})
    assert at["state"].get("value") != "A"
    current = project.property_node_summary(ids["甲"], cut=ids[QUOTES[1][:-1]])
    assert not any(
        row["property_id"] == "称呼" and row["value"] == "A" for row in current["properties"]
    )
    previous = project.property_node_summary(ids["甲"], cut=ids[QUOTES[0][:-1]])
    assert any(
        row["property_id"] == "称呼" and row["value"] == "A" for row in previous["properties"]
    )


def item_transfer(project, *, missing_early_holder=False):
    from narrative.graph_models import Node

    text = "甲拿石头。甲把石头交给乙。"
    first, second = "甲拿石头。", "甲把石头交给乙。"
    sid = project.import_source("合成物体换手", text)

    def obj(identifier, kind, name, quote, **extra):
        start = text.index(quote)
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
                **extra,
            }
        ).model_dump()

    nodes = [
        obj("P1", "person", "甲", "甲"),
        obj("P2", "person", "乙", "乙"),
        obj("I1", "item", "石头", "石头"),
        obj("S1", "scene", "换手", first, events=["E1", "E2"]),
        obj("E1", "event", "拿石头", first, participants=["P1"]),
        obj("E2", "event", "交石头", second, participants=["P1", "P2"]),
    ]
    definition = {
        "kind": "property_definition",
        "id": "持有者",
        "value_type": "node",
        "cardinality": "one",
    }
    values = []
    for i, (quote, holder) in enumerate([(first, "P1"), (second, "P2")]):
        if missing_early_holder and i == 0:
            continue
        values.append(
            {
                "kind": "property_value",
                "id": f"holder{i}",
                "node_id": "I1",
                "property_id": "持有者",
                "value": holder,
                "event_id": f"E{i + 1}",
                "disclosure": {"source_id": sid, "start": text.index(quote)},
                "evidence": {"source_id": sid, "quote": quote},
            }
        )
    result = project.apply_changes(
        nodes,
        [definition, *values],
        project.graph_project_info()["revision"],
        reason="合法当前物体流转",
    )
    assert result["status"] == "accepted", result
    return sid, len(first) - 1


def test_rollback_reopens_prior_holder_and_retains_scene_consistency(project):
    sid, cut = item_transfer(project)
    assert project.property_state("I1", "持有者")["state"]["value"] == "P2"
    result = project.rollback_to(
        sid, cut, project.graph_project_info()["revision"], reason="回到甲持有的句末"
    )
    assert result["status"] == "accepted"
    assert project.property_state("I1", "持有者")["state"]["value"] == "P1"
    assert set(raw_values(project)) == {"holder0"}
    assert raw_values(project)["holder0"].get("valid_to") is None
    nodes = {n["id"]: n for n in project.graph_snapshot()["nodes"]}
    assert "P2" not in nodes and "E2" not in nodes and nodes["S1"]["events"] == ["E1"]
    assert not [r for r in project.check_project()["issues"] if r["severity"] == "error"]


def test_rollback_that_would_leave_item_without_prior_holder_rejects_atomically(project):
    sid, cut = item_transfer(project, missing_early_holder=True)
    before = copy.deepcopy(project.graph_snapshot())
    records = copy.deepcopy(project.property_records())
    revision = project.graph_project_info()["revision"]
    preview = project.rollback_preview(sid, cut, revision)
    assert not preview["valid"] and any(
        r["code"] == "ITEM_HOLDER_REQUIRED" for r in preview["diagnostics"]
    )
    result = project.rollback_to(sid, cut, revision, reason="不能伪填未知以强行回退")
    assert result["status"] == "rejected"
    assert project.graph_snapshot() == before and project.property_records() == records
