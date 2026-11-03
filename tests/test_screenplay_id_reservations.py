"""Explicit structure IDs reserve numeric identities without burning a spare ID."""
from __future__ import annotations

import sqlite3

import pytest
from agent_fixtures import project as project

from narrative.readable_ids import allocate_id, reserve_id
from narrative.screenplay_store import new_id, read_context, write_transaction


def apply(project, screenplay_id, actions):
    revision = project.read_screenplay(screenplay_id)["screenplay"]["structure_revision"]
    return project.apply_screenplay_structure(screenplay_id, actions, revision, "显式结构编号回归")


def test_reserve_only_advances_numeric_high_water_and_obeys_transaction_rollback():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    with pytest.raises(ValueError, match="写事务"):
        reserve_id(db, "test", "act-", "act-7")
    db.execute("BEGIN IMMEDIATE")
    assert reserve_id(db, "test", "act-", "act-7") == "act-7"
    reserve_id(db, "test", "act-", "act-3")
    assert allocate_id(db, "test", "act-") == "act-8"
    reserve_id(db, "test", "episode-", "special-episode")
    assert allocate_id(db, "test", "episode-") == "episode-1"
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    reserve_id(db, "test", "act-", "act-100")
    db.rollback()
    db.execute("BEGIN IMMEDIATE")
    assert allocate_id(db, "test", "act-") == "act-9"
    db.rollback()
    db.close()


def test_explicit_act_then_automatic_act_in_one_batch_never_collides(project):
    screenplay_id = project.create_screenplay("同批创建", reason="编号fixture")["screenplay"]["id"]
    result = apply(project, screenplay_id, [
        {"op": "create_act", "id": "act-2", "title": "显式幕"},
        {"op": "create_act", "title": "自动幕"}])
    assert {row["id"] for row in result["acts"]} == {"act-2", "act-3"}
    assert len(result["structure_entries"]) == 2
    assert project.check_screenplay(screenplay_id)["ok"]


def test_custom_non_numeric_id_does_not_burn_an_automatic_number(project):
    screenplay_id = project.create_screenplay("自定义命名", reason="编号fixture")["screenplay"]["id"]
    result = apply(project, screenplay_id, [
        {"op": "create_act", "id": "opening-act", "title": "自定义幕"},
        {"op": "create_act", "title": "自动幕"}])
    assert {row["id"] for row in result["acts"]} == {"opening-act", "act-1"}


def test_explicit_number_created_then_deleted_remains_reserved_in_and_across_transactions(project):
    screenplay_id = project.create_screenplay("稳定身份", reason="编号fixture")["screenplay"]["id"]
    result = apply(project, screenplay_id, [
        {"op": "create_act", "id": "act-7", "title": "临时幕"},
        {"op": "delete_act", "act_id": "act-7", "expected_revision": 1},
        {"op": "create_act", "title": "保留幕"}])
    assert [row["id"] for row in result["acts"]] == ["act-8"]
    apply(project, screenplay_id, [{"op": "delete_act", "act_id": "act-8", "expected_revision": 1}])
    result = apply(project, screenplay_id, [{"op": "create_act", "title": "后续幕"}])
    assert [row["id"] for row in result["acts"]] == ["act-9"]


def test_failed_batch_rolls_back_reservations_and_restores_context(project):
    screenplay_id = project.create_screenplay("事务回滚", reason="编号fixture")["screenplay"]["id"]
    before = read_context(project)
    with pytest.raises(ValueError):
        apply(project, screenplay_id, [
            {"op": "create_act", "id": "act-100", "title": "不能提交"},
            {"op": "create_episode", "title": "单片不允许有集"}])
    assert read_context(project) == before
    with pytest.raises(ValueError, match="写入事务"):
        new_id("act")
    result = apply(project, screenplay_id, [{"op": "create_act", "title": "合法幕"}])
    assert [row["id"] for row in result["acts"]] == ["act-1"]


def test_allocator_sees_id_inserted_in_mutable_state_after_transaction_started(project):
    project.create_screenplay("动态上下文", reason="编号fixture")
    def mutate(state, _adaptation, _db):
        state["script_acts"]["act-2"] = {"id": "act-2"}
        assert new_id("act") == "act-3"
        raise RuntimeError("结束隔离allocator探测，不写业务对象")
    with pytest.raises(RuntimeError, match="allocator探测"):
        write_transaction(project, "验证动态事务状态", mutate)
    with pytest.raises(ValueError, match="写入事务"):
        new_id("act")


def test_numeric_custom_identity_reserves_its_actual_family_after_deletion(project):
    screenplay_id = project.create_screenplay("任意自定义ID", reason="编号fixture")["screenplay"]["id"]
    apply(project, screenplay_id, [
        {"op": "create_act", "id": "episode-9", "title": "保留自定义合同"},
        {"op": "delete_act", "act_id": "episode-9", "expected_revision": 1}])
    series = project.create_screenplay("后续剧集", format="series", reason="跨事务不复用")
    assert series["episodes"][0]["id"] == "episode-10"
