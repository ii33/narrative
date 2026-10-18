import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from narrative.extraction import chunk_ranges, create_run, export_run, ingest_result
from narrative.models import Assertion, ChangeSet, Entity
from narrative.service import Conflict, Service
from narrative.store import Store


@pytest.fixture
def service(tmp_path):
    return Service(Store(tmp_path / "project.sqlite3"))


def accept(service, *operations):
    change = ChangeSet(expected_revision=service.store.revision, operations=list(operations))
    service.propose(change)
    service.accept(change.id, 1)
    return change


def setup_fact(service):
    source = service.import_text(
        "第一章", "沈砚把铜钥匙藏进左袖，却说钥匙丢了。\r\n门锁着。\r\n门锁着。🙂"
    )
    person = Entity(name="沈砚", aliases=["阿砚"])
    accept(service, person)
    evidence = service.locate(source, "沈砚把铜钥匙藏进左袖，却说钥匙丢了。")
    return source, person, evidence


def test_exact_unicode_duplicate_and_unknown(service):
    source, person, evidence = setup_fact(service)
    source_text = service.store.source(source)["text"]
    assert "\r\n" in source_text and source_text.endswith("🙂")
    duplicates = service.locate(source, "门锁着。")
    assert len(duplicates) == 2 and duplicates[0].start != duplicates[1].start
    assertion = Assertion(subject_id=person.id, predicate="存活", value=None, evidence=evidence)
    accept(service, assertion)
    saved = service.store.objects("assertions")[0]
    assert saved["value"] is None and saved["certainty"] == "unknown"
    assert "alive" not in service.store.objects("entities")[0]
    assert service.store.objects("entities")[0]["aliases"] == ["阿砚"]


def test_claim_stays_claim_after_restart(service):
    _, person, evidence = setup_fact(service)
    assertion = Assertion(
        subject_id=person.id,
        predicate="钥匙状态",
        value="丢了",
        scope="claim",
        holder_id=person.id,
        evidence=evidence,
    )
    accept(service, assertion)
    reopened = Service(Store(service.store.path))
    assert reopened.store.objects("assertions")[0]["scope"] == "claim"
    assert not [a for a in reopened.store.objects("assertions") if a["scope"] == "world"]


@pytest.mark.parametrize("damage", ["hash", "offset", "quote", "missing"])
def test_invalid_evidence_cannot_commit(service, damage):
    _, person, evidence = setup_fact(service)
    if damage == "hash":
        evidence[0].source_hash = "wrong"
    elif damage == "offset":
        evidence[0].end += 1
    elif damage == "quote":
        evidence[0].quote = "没有这句话"
    else:
        evidence = []
    change = ChangeSet(
        expected_revision=service.store.revision,
        operations=[
            Assertion(subject_id=person.id, predicate="状态", value="有钥匙", evidence=evidence)
        ],
    )
    service.propose(change)
    revision = service.store.revision
    with pytest.raises(Conflict, match="证据|原著"):
        service.accept(change.id, 1)
    assert service.store.revision == revision and service.store.objects("assertions") == []


def test_stale_revision_and_duplicate_accept(service):
    first = ChangeSet(expected_revision=0, operations=[Entity(name="甲")])
    second = ChangeSet(expected_revision=0, operations=[Entity(name="乙")])
    service.propose(first)
    service.propose(second)
    assert service.accept(first.id, 1) == 1
    assert service.accept(first.id, 1) == 1
    with pytest.raises(Conflict, match="旧修订"):
        service.accept(second.id, 1)
    assert len(service.store.objects("entities")) == 1


def test_edit_version_and_rejection_isolation(service):
    change = ChangeSet(expected_revision=0, operations=[Entity(name="甲")])
    service.propose(change)
    service.edit(change, 1)
    with pytest.raises(Conflict):
        service.accept(change.id, 1)
    service.reject(change.id, 2)
    service.reject(change.id, 2)
    with pytest.raises(Conflict):
        service.accept(change.id, 2)
    assert service.store.revision == 0 and service.store.objects("entities") == []


def test_correction_audit_and_atomic_rollback(service):
    _, person, evidence = setup_fact(service)
    fact = Assertion(subject_id=person.id, predicate="状态", value="未知", evidence=evidence)
    accept(service, fact)
    fact.value = "持有钥匙"
    accept(service, fact)
    revision = json.loads(service.store.rows("revisions")[-1]["data"])
    assert revision[0]["before"]["value"] == "未知"
    assert revision[0]["after"]["value"] == "持有钥匙"
    new_entity = Entity(name="不应留下")
    invalid = Assertion(subject_id="missing", predicate="状态", value=None, evidence=evidence)
    with pytest.raises(Conflict):
        accept(service, new_entity, invalid)
    assert new_entity.id not in {e["id"] for e in service.store.objects("entities")}


def test_character_reference_survives_entity_edit(service):
    _, person, evidence = setup_fact(service)
    accept(
        service,
        Assertion(
            subject_id=person.id,
            predicate="钥匙",
            value="丢了",
            scope="claim",
            holder_id=person.id,
            evidence=evidence,
        ),
    )
    person.entity_type = "item"
    with pytest.raises(Conflict, match="人物引用"):
        accept(service, person)
    assert service.store.objects("entities")[0]["entity_type"] == "person"


def test_scope_and_executable_extra_rejected():
    with pytest.raises(ValidationError):
        Assertion(subject_id="a", predicate="p", value=None, scope="claim")
    with pytest.raises(ValidationError):
        ChangeSet.model_validate(
            {
                "expected_revision": 0,
                "operations": [{"kind": "entity", "name": "a", "sql": "DROP TABLE sources"}],
            }
        )


def test_import_is_idempotent_versions_immutable(service):
    first = service.import_text("一", "旧原文")
    assert service.import_text("一", "旧原文") == first
    assert service.store.revision == 1
    second = service.import_text("一", "新原文")
    assert first != second and service.store.source(first)["text"] == "旧原文"


def test_concurrent_duplicate_accept(service):
    change = ChangeSet(expected_revision=0, operations=[Entity(name="甲")])
    service.propose(change)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: service.accept(change.id, 1), range(2))) == [1, 1]
    assert service.store.revision == 1


def test_long_text_full_coverage_and_tail(service):
    text = "普通内容" * 25000 + "末尾事实：钥匙在窗台。"
    source = service.import_text("长章", text)
    run_id = create_run(service, source)
    pack = export_run(service, run_id)
    assert pack["chunks"][0]["start"] == 0
    assert pack["chunks"][-1]["end"] == len(text)
    assert pack["chunks"][-1]["text"].endswith("钥匙在窗台。")
    for left, right in zip(pack["chunks"], pack["chunks"][1:], strict=False):
        assert right["start"] <= left["end"]
    assert all(text[c["start"] : c["end"]] == c["text"] for c in pack["chunks"])
    with pytest.raises(ValueError):
        chunk_ranges(10, 2, 2)


def test_ambiguous_result_failed_then_explicit_location(service):
    source, person, _ = setup_fact(service)
    run_id = create_run(service, source)
    chunk = export_run(service, run_id)["chunks"][0]
    result = {
        "assertions": [
            {"subject_ref": person.id, "predicate": "门", "value": "锁着", "quote": "门锁着。"}
        ]
    }
    with pytest.raises(ValueError, match="多个位置"):
        ingest_result(service, chunk["chunk_id"], json.dumps(result, ensure_ascii=False))
    assert service.store.rows("chunks")[0]["status"] == "failed"
    assert not [c for c in service.store.rows("changes") if c["status"] == "pending"]
    result["assertions"][0]["quote_start"] = service.locate(source, "门锁着。")[1].start
    raw = json.dumps(result, ensure_ascii=False)
    candidate_id = ingest_result(service, chunk["chunk_id"], raw)
    assert ingest_result(service, chunk["chunk_id"], raw) == candidate_id
    assert service.store.objects("assertions") == []
    change, _ = service.candidate(candidate_id)
    assert change.operations[0].evidence[0].start == result["assertions"][0]["quote_start"]
    with pytest.raises(Conflict):
        ingest_result(service, chunk["chunk_id"], raw + " ")


def test_overlap_keeps_occurrences_and_warns_identity(service):
    source = service.import_text("重复事件", "甲开门。甲开门。")
    run_id = create_run(service, source, size=6, overlap=2)
    pack = export_run(service, run_id)
    assert len(pack["chunks"]) == 2
    ids = []
    for chunk in pack["chunks"]:
        result = {
            "entities": [{"ref": "a", "name": "甲"}],
            "assertions": [
                {
                    "subject_ref": "a",
                    "predicate": "动作",
                    "value": "开门",
                    "quote": "甲开门。",
                    "quote_start": 0,
                }
            ],
        }
        ids.append(
            ingest_result(service, chunk["chunk_id"], json.dumps(result, ensure_ascii=False))
        )
    first, _ = service.candidate(ids[0])
    service.accept(first.id, 1)
    second, _ = service.candidate(ids[1])
    assert first.operations[1].evidence[0].start != second.operations[1].evidence[0].start
    assert any(d.rule_id == "IDENTITY" for d in service.diagnostics(second))
    assert any(d.rule_id == "REV" for d in service.diagnostics(second))


def test_empty_response_is_coverage_only(service):
    source = service.import_text("章", "还有未提取的事实。")
    run_id = create_run(service, source)
    chunk_id = export_run(service, run_id)["chunks"][0]["chunk_id"]
    assert ingest_result(service, chunk_id, "{}") is None
    assert service.store.rows("runs")[0]["status"] == "complete"
    assert service.store.objects("assertions") == []


def test_backup_restore_includes_pending_raw_and_failures(service, tmp_path):
    source, _, _ = setup_fact(service)
    run_id = create_run(service, source)
    chunk_id = export_run(service, run_id)["chunks"][0]["chunk_id"]
    with pytest.raises(ValueError):
        ingest_result(service, chunk_id, "not-json")
    pending = ChangeSet(
        expected_revision=service.store.revision, operations=[Entity(name="待审核")]
    )
    service.propose(pending)
    backup = service.store.backup(tmp_path / "backup.sqlite3")
    restored_path = Store.restore(backup, tmp_path / "restored.sqlite3")
    restored = Service(Store(restored_path))
    assert restored.store.rows("chunks")[0]["raw"] == "not-json"
    assert restored.candidate(pending.id)[1]["status"] == "pending"
    assert restored.store.revision == service.store.revision
    with pytest.raises(ValueError):
        Store.restore(backup, restored_path)
