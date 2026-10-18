"""Offline v3 extraction: exact evidence, stable refs, snapshots and candidate isolation."""

import json

import pytest

from narrative import extraction
from narrative.models import (
    Assertion,
    ChangeSet,
    Entity,
    Event,
    KnowledgeRecord,
    PropertyDefinition,
    Relation,
    StateFact,
)
from narrative.service import Service
from narrative.store import Store


@pytest.fixture
def project(tmp_path):
    service = Service(Store(tmp_path / "complete-extraction.sqlite3"))
    text = "甲穿红衣。\n乙告诉甲门锁着。\n甲左臂受伤。\n甲左臂包扎。\n甲是乙的朋友。\n门锁着。"
    source_id = service.import_text("完整状态", text)
    run_id = extraction.create_run(service, source_id)
    chunk_id = extraction.export_run(service, run_id)["chunks"][0]["chunk_id"]
    return service, source_id, run_id, chunk_id


def payload(**values):
    return {
        "entities": [
            {"ref": "same", "name": "甲"},
            {"ref": "b", "name": "乙"},
            {"ref": "door", "name": "门", "entity_type": "other"},
            {"ref": "coat", "name": "红衣", "entity_type": "item"},
        ],
        **values,
    }


def ingest(project, value):
    return extraction.ingest_result(project[0], project[3], json.dumps(value, ensure_ascii=False))


def candidate(project, value):
    change_id = ingest(project, value)
    assert change_id is not None
    return project[0].candidate(change_id)[0]


def assertion(**extra):
    return {
        "ref": "same",
        "subject_ref": "door",
        "predicate": "关闭",
        "value": True,
        "quote": "门锁着。",
        **extra,
    }


def state(**extra):
    return {
        "ref": "same",
        "subject_ref": "same",
        "property_id": "outfit",
        "value_entity_ref": "coat",
        "quote": "甲穿红衣。",
        **extra,
    }


def relation(**extra):
    return {
        "ref": "same",
        "subject_ref": "same",
        "object_ref": "b",
        "predicate": "朋友",
        "quote": "甲是乙的朋友。",
        **extra,
    }


def knowledge(**extra):
    return {
        "ref": "same",
        "holder_ref": "same",
        "assertion_ref": "same",
        "attitude": "informed",
        "acquisition": "told",
        "source_character_ref": "b",
        "event_ref": "same",
        "quote": "乙告诉甲门锁着。",
        **extra,
    }


def telling(**extra):
    return {
        "ref": "same",
        "title": "乙告知甲",
        "action": "tell",
        "actor_ref": "b",
        "target_ref": "same",
        "assertion_ref": "same",
        "quote": "乙告诉甲门锁着。",
        **extra,
    }


def test_v3_snapshot_includes_all_readonly_existing_objects_and_full_schema(project):
    service, _, run_id, _ = project
    exported = extraction.export_run(service, run_id)
    assert exported["prompt_version"] == "evidence-extraction/11"
    assert exported["existing_assertions"] == [] and exported["existing_records"] == []
    assert set(exported["result_schema"]["properties"]) == {
        "entities",
        "assertions",
        "events",
        "states",
        "relations",
        "knowledge",
        "property_suggestions",
    }
    with service.store.connect(write=True) as db:
        record = PropertyDefinition(id="late-property", name="迟到属性", value_type="string")
        db.execute("INSERT INTO records VALUES (?,?)", (record.id, record.model_dump_json()))
    assert extraction.export_run(service, run_id) == exported


def test_complete_mixed_result_resolves_independent_namespaces_and_forward_refs(project):
    service, source_id, _, _ = project
    change = candidate(
        project,
        payload(
            assertions=[
                assertion(quote_start=service.store.source(source_id)["text"].rindex("门锁着。"))
            ],
            events=[
                telling(
                    time_kind="relative",
                    after_event_ref="dress",
                    knowledge_used=["same"],
                    preconditions=[
                        {"subject_ref": "same", "property_id": "outfit", "value_entity_ref": "coat"}
                    ],
                    effects=[
                        {"subject_ref": "same", "property_id": "injury:left_arm", "value": "包扎"}
                    ],
                ),
                {
                    "ref": "dress",
                    "title": "甲穿红衣",
                    "action": "dress",
                    "actor_ref": "same",
                    "item_ref": "coat",
                    "persistence": "until_changed",
                    "time_kind": "point",
                    "time_start": 1,
                    "quote": "甲穿红衣。",
                },
                {
                    "ref": "injure",
                    "title": "甲受伤",
                    "action": "injure",
                    "actor_ref": "same",
                    "target_ref": "same",
                    "slot": "left_arm",
                    "state_value": "受伤",
                    "quote": "甲左臂受伤。",
                },
                {
                    "ref": "treat",
                    "title": "甲包扎",
                    "action": "treat",
                    "actor_ref": "same",
                    "target_ref": "same",
                    "slot": "left_arm",
                    "state_value": "包扎",
                    "quote": "甲左臂包扎。",
                },
            ],
            states=[state(validity={"start_event_ref": "dress"})],
            relations=[relation(validity={"end_event_ref": "same"})],
            knowledge=[knowledge(validity={"start_event_ref": "same"})],
            property_suggestions=["提出新属性，须人工批准"],
        ),
    )
    grouped = {
        kind: [op for op in change.operations if op.kind == kind]
        for kind in {"entity", "assertion", "event", "state", "relation", "knowledge"}
    }
    person = next(op for op in grouped["entity"] if isinstance(op, Entity) and op.name == "甲")
    fact = grouped["assertion"][0]
    assert isinstance(fact, Assertion)
    tell = next(op for op in grouped["event"] if isinstance(op, Event) and op.action == "tell")
    dress = next(op for op in grouped["event"] if isinstance(op, Event) and op.action == "dress")
    assert isinstance(tell, Event) and isinstance(dress, Event)
    extracted_state, extracted_relation, extracted_knowledge = (
        grouped["state"][0],
        grouped["relation"][0],
        grouped["knowledge"][0],
    )
    assert isinstance(extracted_state, StateFact)
    assert isinstance(extracted_relation, Relation)
    assert isinstance(extracted_knowledge, KnowledgeRecord)
    assert tell.assertion_id == fact.id and tell.knowledge_used == [fact.id]
    assert tell.target_id == person.id and tell.after_event_id == dress.id
    assert tell.preconditions[0].subject_id == person.id
    assert tell.effects[0].property_id == "injury:left_arm"
    assert (
        extracted_state.subject_id == person.id
        and extracted_state.validity.start_event_id == dress.id
    )
    assert extracted_relation.validity.end_event_id == tell.id
    assert extracted_knowledge.event_id == tell.id and extracted_knowledge.assertion_id == fact.id
    assert extracted_knowledge.attitude == "informed" and fact.scope == "world"
    assert (
        len(
            {
                person.id,
                fact.id,
                tell.id,
                extracted_state.id,
                extracted_relation.id,
                extracted_knowledge.id,
            }
        )
        == 6
    )
    for op in change.operations:
        if isinstance(op, Entity):
            continue
        assert len(op.evidence) == 1
        ev = op.evidence[0]
        assert service.store.source(ev.source_id)["text"][ev.start : ev.end] == ev.quote
    assert not service.store.objects("entities") and not service.store.objects("records")
    assert change.source == "model" and change.run_id == project[2]
    assert not [
        diagnostic for diagnostic in service.diagnostics(change) if diagnostic.severity == "error"
    ]
    service.accept(change.id, 1)
    assert {row["kind"] for row in service.store.objects("records")} == {
        "state",
        "relation",
        "knowledge",
    }
    assert len(service.store.objects("events")) == 4


@pytest.mark.parametrize(
    "kind,factory", [("states", state), ("relations", relation), ("knowledge", knowledge)]
)
@pytest.mark.parametrize("damage", ["missing", "altered", "offset"])
def test_new_arrays_require_exact_evidence_and_fail_atomically(project, kind, factory, damage):
    entry = factory()
    if damage == "missing":
        entry.pop("quote")
    elif damage == "altered":
        entry["quote"] += "不存在"
    else:
        entry["quote_start"] = 999
    value = payload(
        assertions=[
            assertion(quote_start=project[0].store.source(project[1])["text"].rindex("门锁着。"))
        ],
        events=[telling()],
        **{kind: [entry]},
    )
    revision = project[0].store.revision
    with pytest.raises(ValueError):
        ingest(project, value)
    assert project[0].store.rows("changes") == []
    assert project[0].store.objects("entities") == []
    assert project[0].store.revision == revision


@pytest.mark.parametrize(
    "kind,factory", [("states", state), ("relations", relation), ("knowledge", knowledge)]
)
def test_duplicate_record_refs_fail_in_their_own_namespace(project, kind, factory):
    with pytest.raises(ValueError, match="重复"):
        ingest(project, payload(**{kind: [factory(), factory()]}))
    assert not project[0].store.rows("changes")


@pytest.mark.parametrize("property_name", ["suggested", "injury:", "injury", "holder", "clothing"])
def test_unapproved_properties_cannot_enter_states_or_event_effects(project, property_name):
    with pytest.raises(ValueError, match="属性未批准"):
        ingest(
            project,
            payload(
                states=[state(property_id=property_name)], property_suggestions=[property_name]
            ),
        )
    assert not project[0].store.rows("changes")
    with pytest.raises(ValueError, match="属性未批准"):
        ingest(
            project,
            payload(
                events=[
                    telling(
                        assertion_ref=None,
                        effects=[
                            {"subject_ref": "same", "property_id": property_name, "value": "红色"}
                        ],
                    )
                ]
            ),
        )
    assert not project[0].store.rows("changes")


def test_property_suggestions_do_not_create_properties_or_changes(project):
    assert ingest(project, {"property_suggestions": ["新属性建议"]}) is None
    assert not project[0].store.rows("changes") and not project[0].store.objects("records")


def test_approved_custom_property_and_assertion_ids_come_only_from_snapshot(project):
    service, source_id, _, _ = project
    person = Entity(name="已有甲")
    fact = Assertion(subject_id=person.id, predicate="关闭", value=True)
    approved = PropertyDefinition(name="温度", value_type="number", id="temperature")
    with service.store.connect(write=True) as db:
        db.execute("INSERT INTO entities VALUES (?,?)", (person.id, person.model_dump_json()))
        db.execute("INSERT INTO assertions VALUES (?,?)", (fact.id, fact.model_dump_json()))
        db.execute("INSERT INTO records VALUES (?,?)", (approved.id, approved.model_dump_json()))
    run_id = extraction.create_run(service, source_id)
    exported = extraction.export_run(service, run_id)
    chunk_id = exported["chunks"][0]["chunk_id"]
    value = {
        "states": [
            state(subject_ref=person.id, property_id=approved.id, value=37, value_entity_ref=None)
        ],
        "events": [
            telling(
                actor_ref=person.id,
                target_ref=person.id,
                assertion_ref=fact.id,
                knowledge_used=[fact.id],
            )
        ],
    }
    change_id = extraction.ingest_result(service, chunk_id, json.dumps(value, ensure_ascii=False))
    assert change_id is not None
    change, _ = service.candidate(change_id)
    assert isinstance(change.operations[0], Event) and change.operations[0].knowledge_used == [
        fact.id
    ]
    assert (
        isinstance(change.operations[1], StateFact)
        and change.operations[1].property_id == approved.id
    )
    assert exported["existing_assertions"][0]["id"] == fact.id
    assert exported["existing_records"][0]["id"] == approved.id
    old_chunk_id = project[3]
    with pytest.raises(ValueError, match="未知实体|属性未批准"):
        extraction.ingest_result(service, old_chunk_id, json.dumps(value))


def test_existing_assertions_and_records_cannot_be_redefined(project):
    service, source_id, _, _ = project
    person = Entity(name="甲")
    fact = Assertion(id="existing-fact", subject_id=person.id, predicate="关闭", value=True)
    saved_state = StateFact(
        id="existing-state", subject_id=person.id, property_id="outfit", value="红衣"
    )
    with service.store.connect(write=True) as db:
        db.execute("INSERT INTO entities VALUES (?,?)", (person.id, person.model_dump_json()))
        db.execute("INSERT INTO assertions VALUES (?,?)", (fact.id, fact.model_dump_json()))
        db.execute(
            "INSERT INTO records VALUES (?,?)", (saved_state.id, saved_state.model_dump_json())
        )
    run_id = extraction.create_run(service, source_id)
    chunk_id = extraction.export_run(service, run_id)["chunks"][0]["chunk_id"]
    for value in (
        payload(assertions=[assertion(ref=fact.id)]),
        payload(states=[state(ref=saved_state.id)]),
    ):
        with pytest.raises(ValueError, match="重写"):
            extraction.ingest_result(service, chunk_id, json.dumps(value))
    assert service.store.objects("assertions") == [fact.model_dump()]
    assert service.store.objects("records") == [saved_state.model_dump()]


@pytest.mark.parametrize(
    "version,contract,instruction",
    [
        ("evidence-extraction/1", extraction.LegacyExtractionResult, extraction.LEGACY_INSTRUCTION),
        ("evidence-extraction/2", extraction.V2ExtractionResult, extraction.V2_INSTRUCTION),
    ],
)
def test_old_run_fallback_exports_and_parses_its_original_contract(
    project, version, contract, instruction
):
    service, _, run_id, chunk_id = project
    with service.store.connect(write=True) as db:
        data = json.loads(db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        for field in ("instruction", "result_schema", "existing_assertions", "existing_records"):
            data.pop(field)
        data["prompt_version"] = version
        db.execute("UPDATE runs SET data=? WHERE id=?", (json.dumps(data), run_id))
    exported = extraction.export_run(service, run_id)
    assert exported["instruction"] == instruction
    assert exported["result_schema"] == contract.model_json_schema()
    assert "states" not in exported["result_schema"]["properties"]
    with pytest.raises(ValueError):
        extraction.ingest_result(service, chunk_id, json.dumps(payload(states=[state()])))
    old_fact = assertion(quote_start=service.store.source(project[1])["text"].rindex("门锁着。"))
    old_fact.pop("ref")
    assert extraction.ingest_result(service, chunk_id, json.dumps(payload(assertions=[old_fact])))


def test_review_new_arrays_keeps_kind_indices_and_raw_json_repair_audit(tmp_path):
    service = Service(Store(tmp_path / "repair-complete.sqlite3"))
    source_id = service.import_text("重复", "甲穿红衣。甲穿红衣。")
    run_id = extraction.create_run(service, source_id)
    chunk_id = extraction.export_run(service, run_id)["chunks"][0]["chunk_id"]
    value = payload(
        assertions=[
            {
                "ref": "same",
                "subject_ref": "same",
                "predicate": "服装",
                "value": "红衣",
                "quote": "甲穿红衣。",
            }
        ],
        states=[state()],
        relations=[relation(quote="甲穿红衣。")],
        knowledge=[knowledge(event_ref=None, quote="甲穿红衣。")],
    )
    raw = json.dumps(value, ensure_ascii=False)
    with pytest.raises(ValueError):
        extraction.ingest_result(service, chunk_id, raw, metadata={"model": "recorded"})
    review = extraction.review_result(service, chunk_id)
    assert review["parse_error"] is None
    assert [(r["kind"], r["index"]) for r in review["entries"]] == [
        ("assertion", 0),
        ("state", 0),
        ("relation", 0),
        ("knowledge", 0),
    ]
    assert all(row["issue"] == "ambiguous" for row in review["entries"])
    assert all([m["quote_start"] for m in row["matches"]] == [0, 5] for row in review["entries"])
    for key in ("assertions", "states", "relations", "knowledge"):
        value[key][0]["quote_start"] = 5
    edited = json.dumps(value, ensure_ascii=False)
    change_id = extraction.repair_result(service, chunk_id, edited, {}, expected_raw=raw)
    assert change_id is not None
    change, _ = service.candidate(change_id)
    assert all(op.evidence[0].start == 5 for op in change.operations if not isinstance(op, Entity))
    metadata = json.loads(service.store.rows("chunks")[0]["metadata"])
    assert metadata["model_response"]["raw"] == raw
    assert metadata["manual_corrections"][-1]["edited_raw"] == edited
    assert not service.store.objects("records")


def test_total_candidate_limit_remains_atomic(project):
    values = [relation(ref=f"relation-{i}") for i in range(300)]
    states = [state(ref=f"state-{i}") for i in range(201)]
    with pytest.raises(ValueError):
        ingest(project, payload(relations=values, states=states))
    assert not project[0].store.rows("changes")


def test_all_custom_fields_survive_changeset_serialization(project):
    change = candidate(
        project,
        payload(
            states=[
                state(
                    value=None,
                    value_entity_ref="coat",
                    persistence="bounded",
                    validity={"start": 1, "end": 3},
                )
            ]
        ),
    )
    copy = ChangeSet.model_validate_json(change.model_dump_json())
    saved = next(op for op in copy.operations if isinstance(op, StateFact))
    assert saved.persistence == "bounded" and saved.validity.start == 1 and saved.validity.end == 3
    assert saved.value is None and saved.value_entity_id
