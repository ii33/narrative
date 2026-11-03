"""Storage resets are explicit, atomic, and confined to isolated screenplay tables."""
from __future__ import annotations

import json
import sqlite3

import pytest
from agent_fixtures import TEXT, draft_result

from narrative.operations import Operations
from narrative.screenplay_store import TABLES


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "storage-boundaries.nproj")
    source = ops.import_source("边界合成原文", TEXT)
    assert ops.submit_extraction(source, draft_result(ended=False))["status"] == "accepted"
    ops.sync_adaptation()
    ops._store.set_meta("storage_boundary_sentinel", "keep-meta")
    with ops._store.connect(write=True) as db:
        db.execute("INSERT INTO provider_settings(key,value) VALUES(?,?)", ("boundary-profile", "keep-local-profile"))
    ops._provider_store.put("boundary-scope", "boundary-key", "keep-application-profile")
    return ops


def _snapshot(ops):
    """All schema and all rows, for checking failure rollback, including audits."""
    with ops._store.connect() as db:
        return tuple(db.iterdump())


def _outside(ops):
    with ops._store.connect() as db:
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                for table in ("objects", "sources", "property_records", "meta", "provider_settings")}


def _old_ten_tables(ops, *, empty=False):
    document = ops.create_screenplay("旧结构空壳", format="series", reason="隔离测试旧结构构造")
    if empty:
        with ops._store.connect(write=True) as db:
            for table in reversed(TABLES):
                db.execute(f'DELETE FROM "{table}"')
    # Deliberately malformed isolated test fixture, never a user database.
    with ops._store.connect(write=True) as db:
        db.execute("DROP TABLE script_timelines")
    return document["screenplay"]["id"]


def _reset(ops, report=None, **kwargs):
    report = report or ops.inspect_screenplay_storage()
    return ops.reset_screenplay_storage(reason="明确重建隔离测试剧本域", scope="screenplay",
        expected_storage_fingerprint=report["expected_storage_fingerprint"], confirm_discard=True, **kwargs)


def test_inspection_never_initializes_or_rewrites_uninitialized_or_old_storage(project):
    ops = project
    path = ops._store.path
    before = path.read_bytes()
    report = ops.inspect_screenplay_storage()
    assert report["status"] == "uninitialized" and report["total_rows"] == 0
    assert set(report["missing_tables"]) == set(TABLES)
    assert path.read_bytes() == before
    _old_ten_tables(ops)
    before, outside = path.read_bytes(), _outside(ops)
    report = ops.inspect_screenplay_storage()
    assert report["status"] == "incomplete" and report["reset_required"]
    assert report["missing_tables"] == ["script_timelines"]
    assert report["row_counts"]["screenplays"] == 1 and report["row_counts"]["script_episodes"] == 1
    assert "旧结构空壳" not in json.dumps(report, ensure_ascii=False)
    with pytest.raises(ValueError, match="不完整"):
        ops.list_screenplays()
    assert path.read_bytes() == before and _outside(ops) == outside


@pytest.mark.parametrize("has_external_row", [False, True])
def test_external_foreign_key_refuses_reset_atomically_even_when_empty(project, has_external_row):
    ops = project
    screenplay_id = _old_ten_tables(ops)
    with ops._store.connect(write=True) as db:
        db.execute("CREATE TABLE external_assets(id TEXT PRIMARY KEY, screenplay_id TEXT REFERENCES screenplays(id))")
        if has_external_row:
            db.execute("INSERT INTO external_assets VALUES(?,?)", ("external-keep", screenplay_id))
    report = ops.inspect_screenplay_storage()
    assert report["external_references"] == [{"table": "external_assets", "from": "screenplay_id", "target_table": "screenplays", "to": "id"}]
    assert not report["can_initialize_on_create"]
    before, raw = _snapshot(ops), ops._store.path.read_bytes()
    with pytest.raises(ValueError, match="域外"):
        _reset(ops, report)
    with pytest.raises(ValueError):
        ops.create_screenplay("不得暗中清理", reason="测试拒绝外域引用")
    assert _snapshot(ops) == before and ops._store.path.read_bytes() == raw


def test_external_foreign_key_detection_respects_sqlite_case_insensitive_table_names(project):
    ops = project
    _old_ten_tables(ops)
    with ops._store.connect(write=True) as db:
        db.execute('CREATE TABLE external_case_reference(id TEXT PRIMARY KEY, owner TEXT REFERENCES "SCREENPLAYS"(id))')
    report = ops.inspect_screenplay_storage()
    before = _snapshot(ops)
    with pytest.raises(ValueError, match="域外"):
        _reset(ops, report)
    assert _snapshot(ops) == before


def test_case_variant_of_domain_table_is_counted_and_explicitly_discarded(project):
    ops = project
    _old_ten_tables(ops)
    with ops._store.connect(write=True) as db:
        db.execute('ALTER TABLE screenplays RENAME TO boundary_case_temporary')
        db.execute('ALTER TABLE boundary_case_temporary RENAME TO "SCREENPLAYS"')
    report = ops.inspect_screenplay_storage()
    assert report["row_counts"]["screenplays"] == 1
    assert "screenplays" not in report["missing_tables"]
    result = _reset(ops, report)
    assert result["storage"]["status"] == "ready" and result["storage"]["total_rows"] == 0


@pytest.mark.parametrize("scope,confirmation", [("screenplay", False), ("all", True), ("screenplay", "true")])
def test_reset_requires_explicit_exact_scope_and_boolean_confirmation(project, scope, confirmation):
    ops = project
    _old_ten_tables(ops)
    report = ops.inspect_screenplay_storage()
    before, raw = _snapshot(ops), ops._store.path.read_bytes()
    with pytest.raises(ValueError, match="scope"):
        ops.reset_screenplay_storage(reason="测试拒绝", scope=scope,
            expected_storage_fingerprint=report["expected_storage_fingerprint"], confirm_discard=confirmation)
    assert _snapshot(ops) == before and ops._store.path.read_bytes() == raw


def test_stale_inspection_cannot_discard_new_data(project):
    ops = project
    _old_ten_tables(ops)
    old = ops.inspect_screenplay_storage()
    with ops._store.connect(write=True) as db:
        row = db.execute("SELECT id,data FROM screenplays").fetchone()
        data = json.loads(row["data"])
        data["title"] = "检查后新增的修改"
        db.execute("UPDATE screenplays SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), row["id"]))
    assert ops.inspect_screenplay_storage()["expected_storage_fingerprint"] != old["expected_storage_fingerprint"]
    before, raw = _snapshot(ops), ops._store.path.read_bytes()
    with pytest.raises(ValueError, match="已变化"):
        _reset(ops, old)
    assert _snapshot(ops) == before and ops._store.path.read_bytes() == raw


def test_successful_reset_rebuilds_only_fixed_domain_and_preserves_source_meta_and_profiles(project):
    ops = project
    _old_ten_tables(ops)
    outside, adaptation = _outside(ops), ops.adaptation_snapshot()
    provider = ops._provider_store.path.read_bytes()
    before_report = ops.inspect_screenplay_storage()
    result = _reset(ops, before_report)
    assert result["status"] == "reset"
    assert result["discarded_total_rows"] == before_report["total_rows"] > 0
    assert result["discarded_row_counts"] == before_report["row_counts"]
    assert result["storage"]["status"] == "ready" and result["storage"]["total_rows"] == 0
    assert _outside(ops) == outside and ops.adaptation_snapshot() == adaptation
    assert ops._provider_store.path.read_bytes() == provider
    with ops._store.connect() as db:
        present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(TABLES) <= present
        assert list(db.execute("PRAGMA foreign_key_list(adaptation_spans)"))[0]["table"] == "script_timelines"
        audit = db.execute("SELECT action,reason,summary FROM actions ORDER BY rowid DESC LIMIT 1").fetchone()
    assert audit["action"] == "screenplay_storage_reset"
    assert json.loads(audit["summary"])["discarded_total_rows"] == before_report["total_rows"]
    created = ops.create_screenplay("显式重建后新建", reason="新当前结构")
    assert created["screenplay"]["title"] == "显式重建后新建"


def test_partial_ddl_failure_restores_dropped_tables_rows_audit_and_source(project, monkeypatch):
    from narrative import screenplay_store

    ops = project
    _old_ten_tables(ops)
    report = ops.inspect_screenplay_storage()
    before, raw = _snapshot(ops), ops._store.path.read_bytes()
    provider = ops._provider_store.path.read_bytes()
    observed = []

    def fail_after_partial_ddl(db, **_kwargs):
        assert not set(TABLES) & {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.execute("CREATE TABLE screenplays(id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        observed.append("partial ddl executed")
        raise sqlite3.OperationalError("injected DDL failure")

    monkeypatch.setattr(screenplay_store, "initialize_tables", fail_after_partial_ddl)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        _reset(ops, report)
    assert observed == ["partial ddl executed"]
    assert _snapshot(ops) == before and ops._store.path.read_bytes() == raw
    assert ops._provider_store.path.read_bytes() == provider
    assert ops.inspect_screenplay_storage()["expected_storage_fingerprint"] == report["expected_storage_fingerprint"]


def test_only_explicit_create_can_reinitialize_empty_old_domain(project):
    ops = project
    _old_ten_tables(ops, empty=True)
    report = ops.inspect_screenplay_storage()
    assert report["status"] == "incomplete" and report["total_rows"] == 0
    assert report["can_initialize_on_create"] and not report["reset_required"]
    before = ops._store.path.read_bytes()
    with pytest.raises(ValueError, match="不完整"):
        ops.list_screenplays()
    assert ops._store.path.read_bytes() == before
    document = ops.create_screenplay("空旧域显式新建", reason="明确新建")
    assert document["screenplay"]["title"] == "空旧域显式新建"
    assert ops.inspect_screenplay_storage()["status"] == "ready"
