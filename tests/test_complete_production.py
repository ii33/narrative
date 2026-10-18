"""Scene contract acceptance without a media generator, network or model call."""

import copy
import json

import pytest
from pydantic import ValidationError

from narrative.models import (
    ArtifactAcceptance,
    ArtifactObservation,
    AssetBinding,
    AssetRequirement,
    Condition,
    CreativeIntent,
    Entity,
    Event,
    Intervention,
    ObservedAttribute,
    Scene,
    StateEffect,
    StateFact,
    TemporalScope,
)
from narrative.operations import OperationError, Operations
from narrative.production import ScenePackage, build_package
from narrative.state import query


def story():
    person = Entity(id="person", name="甲")
    red = Entity(id="red", name="红衣", entity_type="item")
    blue = Entity(id="blue", name="蓝衣", entity_type="item")
    state = StateFact(
        id="outfit-state",
        subject_id=person.id,
        property_id="outfit",
        value_entity_id=red.id,
        validity=TemporalScope(start=0),
        persistence="until_changed",
        origin="creative",
        locked=True,
    )
    binding = AssetBinding(
        id="asset",
        entity_id=person.id,
        role="portrait",
        asset_uri="asset://portrait",
        version="v1",
        sha256="1" * 64,
        attributes={"outfit": "red"},
        origin="production",
    )
    scene = Scene(
        id="scene",
        title="红衣场景",
        validity=TemporalScope(start=1, end=9),
        entity_ids=[person.id],
        constraints=[Condition(subject_id=person.id, property_id="outfit", value_entity_id=red.id)],
        asset_requirements=[
            AssetRequirement(entity_id=person.id, role="portrait", attributes={"outfit": "red"})
        ],
        binding_ids=[binding.id],
        origin="production",
        production_decisions=["固定红衣角色形象"],
    )
    return {
        "project_id": "project-one",
        "branch_id": "main",
        "revision": 7,
        "entities": [obj.model_dump() for obj in (person, red, blue)],
        "assertions": [],
        "events": [],
        "records": [obj.model_dump() for obj in (state, binding, scene)],
        "sources": [],
    }


def record(snapshot, kind):
    return next(obj for obj in snapshot["records"] if obj["kind"] == kind)


def formal(snapshot):
    return build_package(snapshot, "scene", draft=False)


def test_formal_package_pins_project_revision_assets_and_boundary_states():
    snapshot = story()
    package = formal(snapshot)
    assert package.schema_version == "scene-package/1" and not package.draft
    assert package.branch_revision.project_id == snapshot["project_id"]
    assert package.branch_revision.revision == 7 and package.branch_revision.branch_id == "main"
    assert package.start_state is not None and package.start_state.boundary == "before"
    assert package.end_state is not None and package.end_state.boundary == "after"
    assert package.bindings[0].version == "v1" and package.bindings[0].sha256 == "1" * 64
    assert package.production_decisions == ["固定红衣角色形象"]
    assert not package.unresolved
    # Required values can reference an entity outside Scene.entity_ids; the
    # package must still carry its definition for an independent consumer.
    assert {entity["id"] for entity in package.entities} == {"person", "red"}
    assert any("未读取或检验媒体内容" in item for item in package.coverage)


def test_package_is_a_frozen_value_after_snapshot_and_asset_revision_changes():
    snapshot = story()
    package = formal(snapshot)
    frozen = package.model_dump_json()
    snapshot["revision"] += 1
    record(snapshot, "asset_binding").update(version="v2", sha256="2" * 64)
    record(snapshot, "scene")["production_decisions"].append("新版决定")
    assert package.model_dump_json() == frozen
    newer = formal(snapshot)
    assert newer.branch_revision.revision == 8 and newer.bindings[0].version == "v2"
    assert newer.bindings[0].sha256 == "2" * 64 and package.bindings[0].sha256 == "1" * 64
    assert package.production_decisions == ["固定红衣角色形象"]


@pytest.mark.parametrize("mode", ["missing", "disputed", "superseded", "wrong_kind"])
def test_required_asset_gaps_allow_draft_but_block_formal_export(mode):
    snapshot = story()
    binding = record(snapshot, "asset_binding")
    if mode == "missing":
        snapshot["records"].remove(binding)
    elif mode == "wrong_kind":
        record(snapshot, "scene")["binding_ids"] = [record(snapshot, "state")["id"]]
    else:
        binding["status"] = mode
    before = copy.deepcopy(snapshot)
    draft = build_package(snapshot, "scene")
    assert draft.draft and draft.unresolved and not draft.bindings
    with pytest.raises(ValueError, match="场景包尚有缺口"):
        formal(snapshot)
    assert snapshot == before


def test_optional_asset_can_be_absent_without_inventing_a_binding():
    snapshot = story()
    record(snapshot, "scene")["asset_requirements"][0]["required"] = False
    record(snapshot, "scene")["binding_ids"] = []
    package = formal(snapshot)
    assert not package.bindings and not package.unresolved


@pytest.mark.parametrize("attributes", [{}, {"outfit": "blue"}, {"outfit": True}])
def test_asset_attribute_missing_mismatch_and_type_conflicts_are_explicit(attributes):
    snapshot = story()
    record(snapshot, "asset_binding")["attributes"] = attributes
    assert any(
        "资产属性不匹配或未声明" in item for item in build_package(snapshot, "scene").unresolved
    )
    with pytest.raises(ValueError, match="资产属性"):
        formal(snapshot)


def test_bool_asset_attribute_does_not_match_numeric_one():
    snapshot = story()
    record(snapshot, "scene")["asset_requirements"][0]["attributes"] = {"count": 1}
    record(snapshot, "asset_binding")["attributes"] = {"count": True}
    assert build_package(snapshot, "scene").unresolved


def test_multiple_matching_bindings_and_duplicate_refs_are_not_silently_selected():
    snapshot = story()
    duplicate = copy.deepcopy(record(snapshot, "asset_binding"))
    duplicate["id"] = "asset-two"
    duplicate["version"] = "v2"
    snapshot["records"].append(duplicate)
    record(snapshot, "scene")["binding_ids"].append(duplicate["id"])
    assert any("多个资产" in item for item in build_package(snapshot, "scene").unresolved)
    record(snapshot, "scene")["binding_ids"] = ["asset", "asset"]
    assert any("重复引用" in item for item in build_package(snapshot, "scene").unresolved)


@pytest.mark.parametrize(
    "field,value", [("version", ""), ("sha256", "latest"), ("sha256", "a" * 63), ("asset_uri", "")]
)
def test_asset_contract_requires_nonempty_fixed_version_and_valid_hash(field, value):
    binding = record(story(), "asset_binding") | {field: value}
    with pytest.raises(ValidationError):
        AssetBinding.model_validate(binding)


def test_scene_numeric_validity_is_half_open_at_end_and_boundary_projection_is_after():
    snapshot = story()
    snapshot["events"].append(
        Event(
            id="dress-blue",
            title="场景结束后换蓝衣",
            action="dress",
            actor_id="person",
            item_id="blue",
            time_kind="point",
            time_start=9,
            persistence="until_changed",
            origin="creative",
        ).model_dump()
    )
    package = formal(snapshot)
    assert package.end_state is not None
    assert (
        next(item for item in package.end_state.state["states"] if item["property_id"] == "outfit")[
            "value_entity_id"
        ]
        == "blue"
    )
    assert not package.unresolved


def test_interval_event_anchor_means_completion_in_both_scene_endpoints():
    snapshot = story()
    snapshot["events"] = [
        Event(
            id="start-anchor",
            title="开场移动",
            action="observe",
            time_kind="interval",
            time_start=1,
            time_end=3,
            origin="creative",
        ).model_dump(),
        Event(
            id="end-anchor",
            title="离场",
            action="observe",
            time_kind="interval",
            time_start=7,
            time_end=9,
            origin="creative",
        ).model_dump(),
    ]
    record(snapshot, "scene")["validity"] = TemporalScope(
        start_event_id="start-anchor", end_event_id="end-anchor"
    ).model_dump()
    package = formal(snapshot)
    assert package.start_state is not None and package.start_state.minute == 3
    assert package.end_state is not None and package.end_state.minute == 9


@pytest.mark.parametrize(
    "scope", [{}, {"start_event_id": "missing", "end": 9}, {"start": 1, "end_event_id": "relative"}]
)
def test_unresolved_scene_times_are_named_gaps_without_inventing_minutes(scope):
    snapshot = story()
    snapshot["events"] = [
        Event(id="relative", title="稍后", action="observe", origin="creative").model_dump()
    ]
    record(snapshot, "scene")["validity"] = TemporalScope.model_validate(scope).model_dump()
    draft = build_package(snapshot, "scene")
    assert any("起止时间" in item for item in draft.unresolved)
    with pytest.raises(ValueError, match="起止时间"):
        formal(snapshot)


def test_constraints_cover_mid_scene_effect_expiry_and_do_not_revive_older_state():
    snapshot = story()
    event = Event(
        id="bounded",
        title="有界服装状态",
        action="observe",
        time_kind="point",
        time_start=2,
        effects=[
            StateEffect(
                subject_id="person",
                property_id="outfit",
                value_entity_id="red",
                persistence="bounded",
                valid_until=5,
            )
        ],
        origin="creative",
    )
    snapshot["events"].append(event.model_dump())
    draft = build_package(snapshot, "scene")
    assert any(
        "资料不足" in item and ("@ 5.0" in item or "@ 7.0" in item) for item in draft.unresolved
    )
    with pytest.raises(ValueError, match="约束资料不足"):
        formal(snapshot)


@pytest.mark.parametrize("persistence", ["instant", "unknown"])
def test_constraints_probe_open_intervals_even_when_transition_itself_is_known(persistence):
    snapshot = story()
    record(snapshot, "state")["validity"]["start"] = 1
    record(snapshot, "state")["persistence"] = persistence
    assert (
        next(item for item in query(snapshot, 1)["states"] if item["property_id"] == "outfit")[
            "status"
        ]
        == "known"
    )
    assert any(
        "资料不足" in item and "@ 5.0" in item
        for item in build_package(snapshot, "scene").unresolved
    )
    with pytest.raises(ValueError):
        formal(snapshot)


def test_overlapping_transition_uncertainty_blocks_formal_scene_contract():
    snapshot = story()
    snapshot["events"].append(
        Event(
            id="changing",
            title="换装进行中",
            action="dress",
            actor_id="person",
            item_id="blue",
            time_kind="interval",
            time_start=3,
            time_end=7,
            persistence="until_changed",
            origin="creative",
        ).model_dump()
    )
    unresolved = build_package(snapshot, "scene").unresolved
    assert any("资料不足" in item for item in unresolved)
    assert any("冲突" in item for item in unresolved)


def test_three_scene_packages_can_be_loaded_by_an_independent_consumer(tmp_path):
    snapshot = story()
    template = record(snapshot, "scene")
    snapshot["records"].remove(template)
    paths = []
    for index, (start, end) in enumerate(((1, 2), (3, 4), (5, 6)), 1):
        scene = copy.deepcopy(template)
        scene.update(id=f"scene-{index}", title=f"独立场景{index}")
        scene["validity"] = TemporalScope(start=start, end=end).model_dump()
        snapshot["records"].append(scene)
        package = build_package(snapshot, scene["id"], draft=False)
        path = tmp_path / f"scene-{index}.json"
        path.write_text(package.model_dump_json(), encoding="utf-8")
        paths.append(path)
    # This consumer reads JSON contracts, with no Store, Operations or source
    # database connection. It can select exact assets and schedule each scene.
    consumer = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert [item["scene"]["title"] for item in consumer] == ["独立场景1", "独立场景2", "独立场景3"]
    assert [item["start_state"]["minute"] for item in consumer] == [1, 3, 5]
    assert all(item["bindings"][0]["sha256"] == "1" * 64 for item in consumer)
    assert all(item["branch_revision"]["revision"] == 7 for item in consumer)
    assert all(
        ScenePackage.model_validate(item).scene.id == f"scene-{i}"
        for i, item in enumerate(consumer, 1)
    )


def accept(operations, objects, **options):
    change_id = operations.propose_objects(objects, **options)
    review = operations.review_candidate(change_id)
    assert not [diagnostic for diagnostic in review.diagnostics if diagnostic.severity == "error"]
    operations.accept_candidate(change_id, review.version)
    return change_id


def test_operations_keeps_narrative_asset_and_artifact_observation_layers_separate(tmp_path):
    operations = Operations(tmp_path / "three-layers.sqlite3")
    snapshot = story()
    accept(operations, [*snapshot["entities"], *snapshot["records"]])
    first = operations.scene_package("scene", draft=False)
    first_text = json.dumps(first, ensure_ascii=False, sort_keys=True)
    before = operations.state_at_time(5)
    revision = operations.project_info().revision
    observation = ArtifactObservation(
        id="observation",
        scene_id="scene",
        scene_revision=revision,
        artifact_uri="artifact://frame-1",
        artifact_sha256="3" * 64,
        attributes=[
            ObservedAttribute(
                entity_id="person", attribute="outfit", value="blue", certainty="certain"
            )
        ],
        origin="production",
    )
    acceptance = ArtifactAcceptance(
        id="acceptance",
        scene_id="scene",
        scene_revision=revision,
        observation_ids=[observation.id],
        decision="needs_review",
        deviations=["观察为蓝衣，计划红衣"],
        origin="production",
    )
    accept(operations, [observation.model_dump(), acceptance.model_dump()])
    after = operations.state_at_time(5)
    assert before["states"] == after["states"]
    assert operations.records("artifact_observation")[0]["attributes"][0]["value"] == "blue"
    assert operations.records("asset_binding")[0]["attributes"]["outfit"] == "red"
    assert operations.records("state")[0]["value_entity_id"] == "red"
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == first_text
    assert (
        operations.scene_package("scene", draft=False)["branch_revision"]["revision"]
        > first["branch_revision"]["revision"]
    )
    assert ScenePackage.model_validate(first).branch_revision.revision == revision


def test_locked_narrative_survives_intervention_and_intent_and_blocks_unapproved_edit(tmp_path):
    operations = Operations(tmp_path / "locked-story.sqlite3")
    snapshot = story()
    accept(operations, [*snapshot["entities"], *snapshot["records"]])
    intervention = Intervention(
        id="intervention",
        prompt="门外传来敲门声",
        entity_ids=["person"],
        event_budget=3,
        entity_budget=0,
        model_call_budget=1,
        origin="creative",
    )
    intent = CreativeIntent(
        id="intent",
        actor_id="person",
        goal="保守秘密",
        intervention_id=intervention.id,
        origin="creative",
    )
    before = operations.state_at_time(5)
    accept(operations, [intervention.model_dump(), intent.model_dump()])
    assert before["states"] == operations.state_at_time(5)["states"]
    locked = operations.records("state")[0]
    revision = operations.project_info().revision
    candidate_id = operations.propose_objects(
        [locked | {"value_entity_id": "blue"}], source="correction"
    )
    review = operations.review_candidate(candidate_id)
    assert any(item.rule_id == "LOCKED" for item in review.diagnostics)
    with pytest.raises(OperationError, match="锁定"):
        operations.accept_candidate(candidate_id, review.version)
    assert operations.project_info().revision == revision
    assert operations.records("state")[0] == locked


@pytest.mark.parametrize("field", ["event_budget", "entity_budget", "model_call_budget"])
@pytest.mark.parametrize("value", [True, False, "2", 1.5, 2.0, -1, 21])
def test_intervention_budget_types_and_limits_are_strict(field, value):
    with pytest.raises(ValidationError):
        Intervention.model_validate({"prompt": "预算测试", field: value})


def test_valid_zero_and_bounded_intervention_budgets_are_retained():
    intervention = Intervention(
        prompt="预算测试", event_budget=20, entity_budget=0, model_call_budget=0
    )
    assert (
        intervention.event_budget == 20
        and intervention.entity_budget == 0
        and intervention.model_call_budget == 0
    )
