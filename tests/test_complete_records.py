import json
import sqlite3

import pytest

from narrative.models import (
    ArtifactAcceptance,
    ArtifactObservation,
    Assertion,
    AssetBinding,
    ChangeSet,
    Condition,
    CreativeIntent,
    Entity,
    Event,
    Intervention,
    KnowledgeRecord,
    PropertyDefinition,
    Relation,
    Scene,
    StateEffect,
    StateFact,
    TemporalScope,
    WorldRule,
)
from narrative.service import Conflict, Service
from narrative.store import SCHEMA_VERSION, Store


def adopt(service, *operations, **kwargs):
    change = ChangeSet(
        expected_revision=service.store.revision, operations=list(operations), **kwargs
    )
    service.propose(change)
    service.accept(change.id, 1)
    return change


@pytest.fixture
def project(tmp_path):
    service = Service(Store(tmp_path / "project.sqlite3"))
    source = service.import_text("原文", "甲穿上外套，把钥匙交给乙，然后告诉乙门锁着。")
    evidence = service.locate(source, "甲穿上外套，把钥匙交给乙，然后告诉乙门锁着。")
    adopt(
        service,
        Entity(id="a", name="甲"),
        Entity(id="b", name="乙"),
        Entity(id="coat", name="外套", entity_type="item"),
        Entity(id="key", name="钥匙", entity_type="item"),
        Entity(id="room", name="大厅", entity_type="location"),
    )
    adopt(
        service,
        Assertion(id="door", subject_id="room", predicate="门", value="锁着", evidence=evidence),
    )
    return service, evidence


def test_every_record_roundtrips_with_candidate_diff_audit_and_backup(project, tmp_path):
    service, evidence = project
    revision = service.store.revision
    records = [
        PropertyDefinition(
            id="mood",
            name="心情",
            value_type="string",
            subject_types=["person"],
            origin="production",
        ),
        StateFact(id="mood-a", subject_id="a", property_id="mood", value="紧张", evidence=evidence),
        Relation(id="friend", subject_id="a", predicate="朋友", object_id="b", evidence=evidence),
        KnowledgeRecord(
            id="knows", holder_id="b", assertion_id="door", acquisition="told", evidence=evidence
        ),
        WorldRule(
            id="rule",
            title="门前条件",
            rule_type="require",
            conditions=[Condition(subject_id="a", property_id="mood", value="紧张")],
            origin="production",
        ),
        AssetBinding(
            id="binding",
            entity_id="coat",
            role="costume",
            asset_uri="local:coat.glb",
            version="1",
            sha256="a" * 64,
            origin="production",
        ),
        Scene(
            id="scene",
            title="大厅",
            entity_ids=["a", "coat"],
            binding_ids=["binding"],
            origin="production",
        ),
        Intervention(id="intervention", prompt="甲进入大厅", entity_ids=["a"], origin="creative"),
        CreativeIntent(
            id="intent",
            actor_id="a",
            goal="开门",
            intervention_id="intervention",
            origin="creative",
        ),
        ArtifactObservation(
            id="observation",
            scene_id="scene",
            scene_revision=revision,
            artifact_uri="local:render.png",
            artifact_sha256="b" * 64,
            origin="production",
        ),
        ArtifactAcceptance(
            id="acceptance",
            scene_id="scene",
            scene_revision=revision,
            observation_ids=["observation"],
            origin="production",
        ),
    ]
    change = ChangeSet(expected_revision=revision, operations=records)
    service.propose(change)
    assert all(diff["before"] is None for diff in service.diff(change))
    assert service.store.objects("records") == []
    service.accept(change.id, 1)
    saved = {obj["id"]: obj for obj in service.store.objects("records")}
    assert saved == {obj.id: obj.model_dump() for obj in records}
    audit = json.loads(service.store.rows("revisions")[-1]["data"])
    assert {entry["after"]["kind"] for entry in audit} == {obj.kind for obj in records}
    backup = service.store.backup(tmp_path / "backup.sqlite3")
    restored = Service(Store(Store.restore(backup, tmp_path / "restored.sqlite3")))
    assert restored.store.objects("records") == service.store.objects("records")
    assert restored.store.revision == revision + 1
    with service.store.connect() as original, restored.store.connect() as copy:
        assert (
            original.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()[0]
            == copy.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()[0]
        )


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_migration_preserves_sources_candidates_and_revisions(tmp_path, version):
    path = tmp_path / f"legacy-{version}.sqlite3"
    service = Service(Store(path))
    source = service.import_text("旧章", "甲在门外。")
    adopt(service, Entity(id="a", name="甲"))
    pending = ChangeSet(expected_revision=service.store.revision, operations=[Entity(name="待审")])
    service.propose(pending)
    expected = {
        table: service.store.rows(table)
        for table in ("sources", "changes", "revisions", "entities")
    }
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE records")
        if version == 1:
            db.execute("DROP TABLE events")
        db.execute(f"PRAGMA user_version={version}")
    restored = Store.restore(path, tmp_path / f"restored-{version}.sqlite3")
    for candidate_path in (path, restored):
        migrated = Service(Store(candidate_path))
        for table, rows in expected.items():
            assert migrated.store.rows(table) == rows
        assert migrated.store.source(source)["text"] == "甲在门外。"
        assert migrated.store.objects("records") == []
        with migrated.store.connect() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


@pytest.mark.parametrize(
    "operation",
    [
        StateFact(
            id="a",
            subject_id="a",
            property_id="location",
            value_entity_id="room",
            origin="production",
        ),
        Relation(id="door", subject_id="a", predicate="朋友", object_id="b", origin="production"),
    ],
)
def test_ids_cannot_cross_object_kinds(project, operation):
    service, _ = project
    with pytest.raises(Conflict, match="跨类型"):
        adopt(service, operation)


def test_ids_cannot_cross_kinds_inside_records_table(project):
    service, _ = project
    adopt(
        service,
        Relation(id="same", subject_id="a", predicate="朋友", object_id="b", origin="production"),
    )
    with pytest.raises(Conflict, match="跨类型"):
        adopt(
            service,
            StateFact(
                id="same",
                subject_id="a",
                property_id="location",
                value_entity_id="room",
                origin="production",
            ),
        )


@pytest.mark.parametrize(
    "operation",
    [
        StateFact(subject_id="a", property_id="location", value_entity_id="room"),
        KnowledgeRecord(holder_id="a", assertion_id="door"),
        Relation(subject_id="a", predicate="朋友", object_id="b"),
    ],
)
def test_original_records_require_evidence_even_when_disputed(project, operation):
    service, _ = project
    operation.status = "disputed"
    with pytest.raises(Conflict, match="证据"):
        adopt(service, operation)


def test_entity_evidence_is_validated_when_supplied(project):
    service, evidence = project
    bad = evidence[0].model_copy(update={"quote": "伪造"})
    with pytest.raises(Conflict, match="证据"):
        adopt(service, Entity(name="新人物", evidence=[bad]))


def test_inference_requires_valid_acyclic_dependencies(project):
    service, _ = project
    fact = StateFact(
        id="s", subject_id="a", property_id="location", value_entity_id="room", origin="inference"
    )
    with pytest.raises(Conflict, match="derived_from"):
        adopt(service, fact)
    fact.derived_from = ["missing"]
    with pytest.raises(Conflict, match="引用"):
        adopt(service, fact)
    fact.derived_from = ["door"]
    adopt(service, fact)
    parent = Assertion.model_validate(service.store.objects("assertions")[0])
    parent.derived_from = ["s"]
    with pytest.raises(Conflict, match="成环"):
        adopt(service, parent)


def test_locked_changes_require_explicit_override_and_keep_audit(project):
    service, _ = project
    relation = Relation(
        id="relation",
        subject_id="a",
        predicate="朋友",
        object_id="b",
        origin="production",
        locked=True,
    )
    adopt(service, relation)
    relation.predicate = "敌人"
    with pytest.raises(Conflict, match="锁定"):
        adopt(service, relation)
    adopt(service, relation, allow_locked_changes=True)
    audit = json.loads(service.store.rows("revisions")[-1]["data"])[0]
    assert audit["before"]["predicate"] == "朋友" and audit["after"]["predicate"] == "敌人"


def test_lock_comparison_normalizes_defaults_in_older_saved_json(project):
    service, _ = project
    relation = Relation(
        id="old", subject_id="a", predicate="朋友", object_id="b", origin="production", locked=True
    )
    adopt(service, relation)
    old = relation.model_dump()
    old.pop("properties")
    old.pop("derived_from")
    with service.store.connect(write=True) as db:
        db.execute("UPDATE records SET data=? WHERE id='old'", (json.dumps(old),))
    assert not [
        diagnostic
        for diagnostic in service.diagnostics(
            ChangeSet(expected_revision=service.store.revision, operations=[relation])
        )
        if diagnostic.rule_id == "LOCKED"
    ]


def test_superseded_dependency_graph_can_be_archived_atomically(project):
    service, _ = project
    definition = PropertyDefinition(
        id="legacy-property", name="旧属性", value_type="string", origin="production"
    )
    state = StateFact(
        id="legacy-state",
        subject_id="a",
        property_id=definition.id,
        value="旧值",
        origin="production",
    )
    adopt(service, definition, state)
    definition.status = "superseded"
    state.status = "superseded"
    adopt(service, definition, state)
    assert {record["status"] for record in service.store.objects("records")} == {"superseded"}


@pytest.mark.parametrize("value", [True, "3"])
def test_custom_number_property_is_strict_and_rechecks_existing_records(project, value):
    service, _ = project
    definition = PropertyDefinition(
        id="height", name="高度", value_type="number", origin="production"
    )
    adopt(service, definition)
    with pytest.raises(Conflict, match="number"):
        adopt(
            service,
            StateFact(subject_id="a", property_id="height", value=value, origin="production"),
        )
    adopt(service, StateFact(subject_id="a", property_id="height", value=3, origin="production"))
    definition.value_type = "boolean"
    with pytest.raises(Conflict, match="boolean"):
        adopt(service, definition)


@pytest.mark.parametrize(
    "fields",
    [
        {"subject_id": "key", "property_id": "location", "value_entity_id": "room"},
        {"subject_id": "a", "property_id": "location", "value_entity_id": "key"},
        {"subject_id": "a", "property_id": "location", "value": "room"},
        {"subject_id": "a", "property_id": "undefined", "value": "x"},
    ],
)
def test_builtin_and_custom_property_references_are_checked(project, fields):
    service, _ = project
    with pytest.raises(Conflict):
        adopt(service, StateFact(**fields, origin="production", status="disputed"))


def test_correcting_entity_or_superseding_assertion_cannot_break_existing_records(project):
    service, _ = project
    adopt(
        service,
        KnowledgeRecord(id="known", holder_id="a", assertion_id="door", origin="production"),
    )
    with pytest.raises(Conflict, match="人物引用"):
        adopt(service, Entity(id="a", name="甲", entity_type="item"))
    assertion = Assertion.model_validate(service.store.objects("assertions")[0])
    assertion.status = "superseded"
    with pytest.raises(Conflict, match="取代"):
        adopt(service, assertion)


def test_event_anchor_cannot_be_superseded_while_used_by_existing_validity(project):
    service, evidence = project
    event = Event(
        id="anchor",
        title="开始",
        action="observe",
        time_kind="point",
        time_start=10,
        evidence=evidence,
    )
    adopt(service, event)
    adopt(
        service,
        StateFact(
            subject_id="a",
            property_id="location",
            value_entity_id="room",
            validity=TemporalScope(start_event_id="anchor"),
            origin="production",
        ),
    )
    event.status = "superseded"
    with pytest.raises(Conflict, match="取代"):
        adopt(service, event)


@pytest.mark.parametrize(
    "action,fields",
    [
        ("dress", {"actor_id": "a", "item_id": "coat"}),
        ("injure", {"target_id": "b"}),
        ("treat", {"target_id": "b"}),
        ("tell", {"actor_id": "a", "target_id": "b", "assertion_id": "door"}),
    ],
)
def test_new_actions_are_saved_through_same_candidate_protocol(project, action, fields):
    service, evidence = project
    event = Event(title=action, action=action, evidence=evidence, **fields)
    change = adopt(service, event)
    assert service.diff(change)[0]["before"]["action"] == action
    assert service.store.objects("events")[0]["action"] == action


def test_effects_preconditions_and_knowledge_references_are_validated(project):
    service, evidence = project
    event = Event(
        title="观察",
        action="observe",
        evidence=evidence,
        effects=[StateEffect(subject_id="a", property_id="location", value_entity_id="missing")],
    )
    with pytest.raises(Conflict, match="引用"):
        adopt(service, event)
    event.effects = []
    event.preconditions = [Condition(subject_id="a", property_id="missing")]
    with pytest.raises(Conflict, match="属性未定义"):
        adopt(service, event)
    event.preconditions = []
    event.knowledge_used = ["missing"]
    with pytest.raises(Conflict, match="引用"):
        adopt(service, event)


def test_state_correction_rechecks_existing_event_preconditions(project):
    service, evidence = project
    initial = StateFact(
        id="holder",
        subject_id="key",
        property_id="held_by",
        value_entity_id="a",
        validity=TemporalScope(start=0),
        persistence="until_changed",
        origin="production",
    )
    adopt(service, initial)
    adopt(
        service,
        Event(
            title="交接",
            action="transfer",
            actor_id="a",
            target_id="b",
            item_id="key",
            time_kind="point",
            time_start=10,
            evidence=evidence,
        ),
    )
    initial.value_entity_id = "b"
    revision = service.store.revision
    with pytest.raises(Conflict, match="前置条件"):
        adopt(service, initial)
    assert service.store.revision == revision
    assert service.store.objects("records")[0]["value_entity_id"] == "a"


def test_relative_event_after_latest_numeric_anchor_is_checked_globally(project):
    service, evidence = project
    initial = StateFact(
        subject_id="key",
        property_id="held_by",
        value_entity_id="b",
        validity=TemporalScope(start=0),
        persistence="until_changed",
        origin="production",
    )
    adopt(
        service,
        initial,
        Event(
            id="anchor",
            title="时间锚点",
            action="observe",
            time_kind="point",
            time_start=10,
            evidence=evidence,
        ),
    )
    with pytest.raises(Conflict, match="前置条件"):
        adopt(
            service,
            Event(
                title="不可能的交接",
                action="transfer",
                actor_id="a",
                target_id="b",
                item_id="key",
                time_kind="relative",
                after_event_id="anchor",
                evidence=evidence,
            ),
        )


def test_full_snapshot_evidence_validation_covers_unchanged_records(project):
    service, _ = project
    with service.store.connect(write=True) as db:
        assertion = json.loads(
            db.execute("SELECT data FROM assertions WHERE id='door'").fetchone()[0]
        )
        assertion["evidence"][0]["quote"] = "损坏的引文"
        db.execute("UPDATE assertions SET data=? WHERE id='door'", (json.dumps(assertion),))
    revision = service.store.revision
    with pytest.raises(Conflict, match="证据"):
        adopt(service, Entity(name="无关实体"))
    assert service.store.revision == revision


def test_artifact_acceptance_requires_matching_scene_revision(project):
    service, _ = project
    revision = service.store.revision
    adopt(
        service,
        Scene(id="scene", title="场景", origin="production"),
        Scene(id="other", title="另一场景", origin="production"),
    )
    adopt(
        service,
        ArtifactObservation(
            id="observed",
            scene_id="scene",
            scene_revision=revision,
            artifact_uri="render.png",
            artifact_sha256="c" * 64,
            origin="production",
        ),
    )
    for scene_id, scene_revision in [("other", revision), ("scene", revision + 1)]:
        with pytest.raises(Conflict, match="同一场景及修订"):
            adopt(
                service,
                ArtifactAcceptance(
                    scene_id=scene_id,
                    scene_revision=scene_revision,
                    observation_ids=["observed"],
                    origin="production",
                ),
            )
    with pytest.raises(Conflict, match="未来"):
        adopt(
            service,
            ArtifactObservation(
                scene_id="scene",
                scene_revision=999,
                artifact_uri="future.png",
                artifact_sha256="d" * 64,
                origin="production",
            ),
        )


def test_temporal_conflict_can_be_retained_as_disputed_but_refs_cannot(project):
    service, evidence = project
    adopt(
        service,
        Event(
            id="start",
            title="开始",
            action="observe",
            time_kind="point",
            time_start=10,
            evidence=evidence,
        ),
        Event(
            id="end",
            title="结束",
            action="observe",
            time_kind="point",
            time_start=5,
            evidence=evidence,
        ),
    )
    fact = StateFact(
        subject_id="a",
        property_id="location",
        value_entity_id="room",
        validity=TemporalScope(start_event_id="start", end_event_id="end"),
        origin="production",
    )
    with pytest.raises(Conflict, match="终点"):
        adopt(service, fact)
    fact.status = "disputed"
    adopt(service, fact)
    fact.value_entity_id = "missing"
    with pytest.raises(Conflict, match="引用"):
        adopt(service, fact)


@pytest.mark.parametrize(
    "fields",
    [
        {"time_kind": "point", "time_start": 10, "min_duration": 1},
        {"time_kind": "interval", "time_start": 10, "time_end": 20, "max_duration": 5},
    ],
)
def test_event_duration_constraint_blocks_conflict_but_retains_disputed(project, fields):
    service, evidence = project
    event = Event(title="时间约束", action="observe", evidence=evidence, **fields)
    with pytest.raises(Conflict, match="时长"):
        adopt(service, event)
    event.status = "disputed"
    adopt(service, event)


def test_unknown_time_duration_constraint_remains_insufficient(project):
    service, evidence = project
    event = Event(title="时长待定", action="observe", min_duration=5, evidence=evidence)
    change = ChangeSet(expected_revision=service.store.revision, operations=[event])
    diagnostics = service.diagnostics(change)
    assert any(
        d.rule_id == "TIME_DURATION" and d.verdict == "insufficient" and d.severity == "info"
        for d in diagnostics
    )
    adopt(service, event)


def test_effect_validity_must_extend_beyond_event_completion(project):
    service, evidence = project
    event = Event(
        title="过期效果",
        action="observe",
        time_kind="point",
        time_start=10,
        effects=[
            StateEffect(
                subject_id="a", property_id="location", value_entity_id="room", valid_until=10
            )
        ],
        evidence=evidence,
    )
    with pytest.raises(Conflict, match="有效期终点"):
        adopt(service, event)
    event.status = "disputed"
    adopt(service, event)
