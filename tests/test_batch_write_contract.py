"""Direct immutable batches: no staging, concurrent guards, and transaction rollback."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from agent_fixtures import project as project
from test_current_state_contract import setup

from narrative import batch_write


def prepared_update(project, name):
    snapshot = project.capture_changes()
    person = next(n for n in snapshot.reader.graph_snapshot()["nodes"] if n["id"] == "P1")
    person["name"] = name
    prepared = project.prepare_changes([person], [], snapshot.revision, snapshot=snapshot)
    assert prepared.valid, prepared.diagnostics
    return snapshot, prepared


def state(project):
    return copy.deepcopy((project.graph_snapshot(), project.property_records()))


def test_schema_has_no_staging_tables_or_candidate_apis(project):
    from narrative.graph_store import GraphStore

    store = GraphStore(project.project_info().path)
    with store.connect() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {"candidates", "property_candidates", "change_candidates"} & tables
    for name in ("change_propose", "change_accept", "property_propose", "property_accept"):
        assert not hasattr(project, name), name


def test_snapshot_consistent_and_encoded_commit_cannot_be_mutated(project):
    setup(project)
    snapshot, prepared = prepared_update(project, "甲的新称呼")
    external = prepared.graph
    next(n for n in external["nodes"] if n["id"] == "P1")["name"] = "未校验篡改"
    result = project.commit_changes(prepared, reason="合成不可变预检验证")
    assert result["status"] == "accepted"
    assert (
        next(n for n in project.graph_snapshot()["nodes"] if n["id"] == "P1")["name"]
        == "甲的新称呼"
    )
    assert (
        next(n for n in snapshot.reader.graph_snapshot()["nodes"] if n["id"] == "P1")["name"]
        == "甲"
    )
    assert snapshot.revision < project.graph_snapshot()["revision"]
    with project.project.store.connect() as db:
        actions = list(db.execute("SELECT id,revision,summary FROM actions"))
    assert all(identifier == f"r{revision}" for identifier, revision, _ in actions)
    assert all('"before"' not in summary and '"after"' not in summary for _, _, summary in actions)


def test_concurrent_prepared_batches_only_one_wins_without_partial_state(project):
    setup(project)
    _, first = prepared_update(project, "第一种称呼")
    _, second = prepared_update(project, "第二种称呼")
    barrier = Barrier(2)

    def commit(prepared):
        barrier.wait()
        try:
            return project.commit_changes(prepared, reason="并发版本保护")
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(commit, (first, second)))
    assert sum(isinstance(r, dict) and r["status"] == "accepted" for r in results) == 1
    assert sum(isinstance(r, str) and "版本" in r for r in results) == 1
    assert next(n for n in project.graph_snapshot()["nodes"] if n["id"] == "P1")["name"] in {
        "第一种称呼",
        "第二种称呼",
    }


def test_sql_exception_rolls_back_delta_revision_and_action(project, monkeypatch):
    setup(project)
    _, prepared = prepared_update(project, "绝不可部分保存")
    before = state(project)
    original = batch_write._write_delta

    def fail(db, prepared, revision):
        original(db, prepared, revision)
        raise RuntimeError("injected after SQL writes")

    monkeypatch.setattr(batch_write, "_write_delta", fail)
    with pytest.raises(RuntimeError, match="injected"):
        project.commit_changes(prepared, reason="SQL故障注入")
    assert state(project) == before
