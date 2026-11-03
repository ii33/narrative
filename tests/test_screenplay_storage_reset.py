"""Explicit scoped rebuild of obsolete screenplay storage, never migration."""
from __future__ import annotations

import json

import pytest
from agent_fixtures import project as project
from test_screenplay_core import seed

from narrative.project_package import layout
from narrative.screenplay_store import TABLES, initialize_tables


def legacy_storage(project, *, populated):
    if populated:
        project.create_screenplay("旧剧本", format="series", reason="隔离旧结构fixture")
    else:
        with project._store.connect(write=True) as db:
            initialize_tables(db, persist_timeline_identity=False)
    # Historical fixture construction only: user/business writes still use Ops.
    with project._store.connect(write=True) as db:
        db.execute("DROP TABLE script_timelines")


def protected_data(project):
    with project._store.connect() as db:
        meta = {row[0]: row[1] for row in db.execute("SELECT key,value FROM meta")}
    return project.graph_snapshot(), project.property_records(), project.adaptation_snapshot(), meta


def reset(project, inspection, **overrides):
    params = {"reason": "明确丢弃隔离旧剧本域并重建", "scope": "screenplay",
              "expected_storage_fingerprint": inspection["expected_storage_fingerprint"], "confirm_discard": True}
    return project.reset_screenplay_storage(**(params | overrides))


def test_inspection_is_read_only_and_reports_populated_ten_table_storage(project):
    seed(project)
    legacy_storage(project, populated=True)
    before = protected_data(project)
    result = project.inspect_screenplay_storage()
    assert result["status"] == "incomplete"
    assert result["missing_tables"] == ["script_timelines"]
    assert result["row_counts"]["screenplays"] == result["row_counts"]["script_episodes"] == result["row_counts"]["script_structure_entries"] == 1
    assert result["total_rows"] == 3 and result["reset_required"]
    assert not result["can_initialize_on_create"]
    with pytest.raises(ValueError, match="显式重建"):
        project.list_screenplays()
    with pytest.raises(ValueError, match="含已有数据.*显式重建"):
        project.create_screenplay("新建", reason="不能暗中删除旧剧本")
    assert project.inspect_screenplay_storage() == result
    assert protected_data(project) == before


def test_explicit_reset_discards_only_script_domain_and_new_create_succeeds(project):
    seed(project)
    legacy_storage(project, populated=True)
    before = protected_data(project)
    canary = layout(project.project_info().path).material / "Library" / "fixture-attachment.bin"
    canary.parent.mkdir(parents=True, exist_ok=True)
    canary.write_bytes(b"untouched Library attachment")
    inspection = project.inspect_screenplay_storage()
    result = reset(project, inspection)
    assert result["status"] == "reset" and result["discarded_total_rows"] == 3
    assert result["discarded_row_counts"] == inspection["row_counts"]
    assert result["storage"]["status"] == "ready" and result["storage"]["total_rows"] == 0
    assert result["storage"]["missing_tables"] == [] and len(result["storage"]["present_tables"]) == len(TABLES)
    assert protected_data(project) == before
    assert canary.read_bytes() == b"untouched Library attachment"
    assert project.list_screenplays() == []
    with project._store.connect() as db:
        action = db.execute("SELECT action,summary FROM actions ORDER BY rowid DESC LIMIT 1").fetchone()
    assert action[0] == "screenplay_storage_reset"
    assert json.loads(action[1])["discarded_total_rows"] == 3
    created = project.create_screenplay("新剧本", format="series", reason="显式重建后新建")
    assert created["screenplay"]["format"] == "series"
    assert len(created["episodes"]) == 1
    assert created["timeline"]["nodes"] == [] and created["timeline_revision"] == 0
    after = protected_data(project)
    assert after[:3] == before[:3]
    # New screenplay IDs advance their own program-owned counters in meta.
    def outside_metadata(meta):
        return {key: value for key, value in meta.items() if not key.startswith("readable_id:screenplay:")}
    assert outside_metadata(after[3]) == outside_metadata(before[3])


def test_empty_legacy_domain_can_initialize_on_explicit_create_but_failed_create_rolls_back(project):
    seed(project)
    legacy_storage(project, populated=False)
    before = project.inspect_screenplay_storage()
    protected = protected_data(project)
    assert before["status"] == "incomplete" and before["total_rows"] == 0
    assert before["can_initialize_on_create"] and not before["reset_required"]
    with pytest.raises(ValueError):
        project.create_screenplay("非法格式", format="episode", reason="验证原子创建")
    assert project.inspect_screenplay_storage() == before
    assert protected_data(project) == protected
    project.create_screenplay("正常新建", reason="显式创建初始化空旧域")
    assert project.inspect_screenplay_storage()["status"] == "ready"
    assert len(project.list_screenplays()) == 1
    assert project.adaptation_snapshot() == protected[2]


def test_reset_requires_explicit_scope_confirmation_and_fresh_storage_fingerprint(project):
    first = project.create_screenplay("第一份", reason="当前完整结构")
    inspection = project.inspect_screenplay_storage()
    for override in ({"scope": "project"}, {"confirm_discard": False}, {"expected_storage_fingerprint": ""}):
        with pytest.raises(ValueError):
            reset(project, inspection, **override)
        assert project.inspect_screenplay_storage() == inspection
    project.create_screenplay("并发新增", reason="检查后发生新写入")
    current = project.inspect_screenplay_storage()
    assert current["expected_storage_fingerprint"] != inspection["expected_storage_fingerprint"]
    with pytest.raises(ValueError, match="存储已变化"):
        reset(project, inspection)
    assert project.inspect_screenplay_storage() == current
    assert {row["title"] for row in project.list_screenplays()} == {"第一份", "并发新增"}
    assert first["screenplay"]["id"] in {row["id"] for row in project.list_screenplays()}
    result = reset(project, current)
    assert result["storage"]["status"] == "ready" and result["storage"]["total_rows"] == 0


def test_reset_failure_rolls_back_ddl_old_rows_and_audit(project, monkeypatch):
    from narrative import screenplay_store
    legacy_storage(project, populated=True)
    before = project.inspect_screenplay_storage()
    with project._store.connect() as db:
        audit_count = db.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
    original = screenplay_store.initialize_tables
    def fail_after_create(db, **kwargs):
        original(db, **kwargs)
        raise RuntimeError("模拟重建中断")
    monkeypatch.setattr(screenplay_store, "initialize_tables", fail_after_create)
    with pytest.raises(RuntimeError, match="模拟重建中断"):
        reset(project, before)
    assert project.inspect_screenplay_storage() == before
    with project._store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == audit_count


def test_reset_refuses_external_foreign_keys_instead_of_cascading_outside_domain(project):
    screenplay_id = project.create_screenplay("被域外引用", reason="当前剧本")["screenplay"]["id"]
    with project._store.connect(write=True) as db:
        db.execute("CREATE TABLE external_fixture(id TEXT PRIMARY KEY,screenplay_id TEXT REFERENCES screenplays(id))")
        db.execute("INSERT INTO external_fixture VALUES('external',?)", (screenplay_id,))
    inspection = project.inspect_screenplay_storage()
    assert inspection["external_references"]
    with pytest.raises(ValueError, match="域外数据表"):
        reset(project, inspection)
    assert project.inspect_screenplay_storage() == inspection
    with project._store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM external_fixture").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM screenplays").fetchone()[0] == 1


def test_inspecting_uninitialized_domain_never_creates_business_tables(project):
    before = protected_data(project)
    inspection = project.inspect_screenplay_storage()
    assert inspection["status"] == "uninitialized" and inspection["present_tables"] == []
    assert inspection["total_rows"] == 0 and inspection["can_initialize_on_create"]
    assert project.inspect_screenplay_storage() == inspection
    assert protected_data(project) == before

def test_reset_fingerprint_detects_structure_change_even_when_rows_are_unchanged(project):
    project.create_screenplay("结构指纹", reason="当前完整结构")
    before = project.inspect_screenplay_storage()
    with project._store.connect(write=True) as db:
        db.execute("CREATE INDEX fixture_screenplay_timeline_index ON screenplays(timeline_id)")
    after = project.inspect_screenplay_storage()
    assert after["row_counts"] == before["row_counts"]
    assert after["expected_storage_fingerprint"] != before["expected_storage_fingerprint"]
    with pytest.raises(ValueError, match="存储已变化"):
        reset(project, before)
    assert project.inspect_screenplay_storage() == after
