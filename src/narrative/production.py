"""Pinned scene contracts; production observations never write narrative state."""

from typing import Literal

from pydantic import Field, JsonValue

from .models import AssetBinding, AssetRequirement, Condition, Model, Scene


class RevisionRef(Model):
    project_id: str
    branch_id: str = "main"
    revision: int = Field(ge=0)


class BoundaryState(Model):
    minute: float
    boundary: Literal["before", "after"]
    state: dict[str, JsonValue]


class ScenePackage(Model):
    schema_version: Literal["scene-package/1"] = "scene-package/1"
    branch_revision: RevisionRef
    scene: Scene
    start_state: BoundaryState | None = None
    end_state: BoundaryState | None = None
    entities: list[dict[str, JsonValue]] = Field(default_factory=list)
    requirements: list[AssetRequirement] = Field(default_factory=list)
    bindings: list[AssetBinding] = Field(default_factory=list)
    constraints: list[Condition] = Field(default_factory=list)
    production_decisions: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    coverage: list[str] = Field(default_factory=list)
    draft: bool = True


def _condition_result(condition: Condition, projection: dict) -> bool | None:
    item = next(
        (
            item
            for item in projection.get("states", [])
            if item["subject_id"] == condition.subject_id
            and item["property_id"] == condition.property_id
        ),
        None,
    )
    known = item is not None and item.get("status") == "known"
    if condition.operator == "known":
        return known
    if condition.operator == "unknown":
        return not known
    if not known or item is None:
        return None
    actual = (
        item.get("value_entity_id")
        if item.get("value_entity_id") is not None
        else item.get("value")
    )
    expected = (
        condition.value_entity_id if condition.value_entity_id is not None else condition.value
    )
    equal = type(actual) is type(expected) and actual == expected
    return equal if condition.operator == "eq" else not equal


def build_package(snapshot: dict, scene_id: str, *, draft: bool = True) -> ScenePackage:
    from .state import query

    records = {record["id"]: record for record in snapshot["records"]}
    raw = records.get(scene_id)
    if raw is None or raw["kind"] != "scene":
        raise ValueError("场景不存在")
    scene = Scene.model_validate(raw)
    unresolved: list[str] = []
    if scene.status != "accepted":
        unresolved.append("场景有争议或已被取代")
    start, end = scene.validity.start, scene.validity.end
    events = {event["id"]: event for event in snapshot["events"]}
    for bound, anchor in (
        ("start", scene.validity.start_event_id),
        ("end", scene.validity.end_event_id),
    ):
        if anchor:
            event = events.get(anchor, {})
            if event.get("status", "accepted") == "accepted":
                # TemporalScope event anchors refer to completion, matching
                # state validity and reference validation for interval events.
                minute = event.get("time_end")
                if minute is None:
                    minute = event.get("time_start")
                if bound == "start":
                    start = minute
                else:
                    end = minute
    if start is None or end is None or start >= end:
        unresolved.append("场景起止时间尚未形成有效数值区间")
    start_projection = query(snapshot, start, "before") if start is not None else None
    end_projection = query(snapshot, end, "after") if end is not None else None
    bindings = [
        AssetBinding.model_validate(records[ref])
        for ref in scene.binding_ids
        if ref in records
        and records[ref].get("kind") == "asset_binding"
        and records[ref].get("status", "accepted") == "accepted"
    ]
    for requirement in scene.asset_requirements:
        matches = [
            binding
            for binding in bindings
            if binding.entity_id == requirement.entity_id and binding.role == requirement.role
        ]
        if not matches and requirement.required:
            unresolved.append(f"缺少固定版本资产：{requirement.entity_id} / {requirement.role}")
        if len(matches) > 1:
            unresolved.append(f"同一需求绑定多个资产：{requirement.entity_id} / {requirement.role}")
        for binding in matches:
            for key, expected in requirement.attributes.items():
                actual = binding.attributes.get(key)
                if (
                    key not in binding.attributes
                    or type(actual) is not type(expected)
                    or actual != expected
                ):
                    unresolved.append(f"资产属性不匹配或未声明：{binding.id} / {key}")
    if len(bindings) != len(scene.binding_ids):
        unresolved.append("存在失效、争议或非资产类型的绑定")
    if len(set(scene.binding_ids)) != len(scene.binding_ids):
        unresolved.append("同一资产绑定被重复引用")
    required_entity_ids = set(scene.entity_ids)
    required_entity_ids.update(requirement.entity_id for requirement in scene.asset_requirements)
    required_entity_ids.update(binding.entity_id for binding in bindings)
    for condition in scene.constraints:
        required_entity_ids.add(condition.subject_id)
        if condition.value_entity_id:
            required_entity_ids.add(condition.value_entity_id)
    entity_ids = {entity["id"] for entity in snapshot["entities"]}
    for entity_id in sorted(required_entity_ids - entity_ids):
        unresolved.append(f"场景引用的实体不存在：{entity_id}")
    # Check every supported transition cut, not only the endpoints of the scene.
    cuts: set[float] = {minute for minute in (start, end) if minute is not None}
    if start is not None and end is not None and start < end:
        for event in snapshot["events"]:
            for key in ("time_start", "time_end", "valid_until"):
                minute = event.get(key)
                if minute is not None and start <= minute <= end:
                    cuts.add(minute)
            for effect in event.get("effects", []):
                minute = effect.get("valid_until")
                if minute is not None and start <= minute <= end:
                    cuts.add(minute)
        for record in snapshot["records"]:
            for key in ("start", "end"):
                minute = record.get("validity", {}).get(key)
                if minute is not None and start <= minute <= end:
                    cuts.add(minute)
    if scene.constraints and start is not None and end is not None and start < end:
        ordered_cuts = sorted(cuts)
        # Scene validity is [start, end). Inspect both sides of internal cuts,
        # and the open intervals: instant/unknown persistence can be known at
        # a transition itself yet unknown immediately afterwards.
        probes = {(minute, "after") for minute in ordered_cuts if minute < end}
        probes.update((minute, "before") for minute in ordered_cuts if minute > start)
        probes.update(
            ((left + right) / 2, "after")
            for left, right in zip(ordered_cuts, ordered_cuts[1:], strict=False)
        )
        for minute, boundary in sorted(probes):
            projection = query(snapshot, minute, boundary)
            for condition in scene.constraints:
                result = _condition_result(condition, projection)
                if result is not True:
                    verdict = "冲突" if result is False else "资料不足"
                    unresolved.append(
                        f"场景约束{verdict}：{condition.subject_id}/{condition.property_id} @ {minute} {boundary}"
                    )
    unresolved = list(dict.fromkeys(unresolved))
    if not draft and unresolved:
        raise ValueError("场景包尚有缺口：" + "；".join(unresolved))
    return ScenePackage(
        branch_revision=RevisionRef(
            project_id=snapshot["project_id"],
            branch_id=snapshot.get("branch_id", "main"),
            revision=snapshot["revision"],
        ),
        scene=scene,
        start_state=BoundaryState(minute=start, boundary="before", state=start_projection)
        if start_projection is not None and start is not None
        else None,
        end_state=BoundaryState(minute=end, boundary="after", state=end_projection)
        if end_projection is not None and end is not None
        else None,
        entities=[entity for entity in snapshot["entities"] if entity["id"] in required_entity_ids],
        requirements=scene.asset_requirements,
        bindings=bindings,
        constraints=scene.constraints,
        production_decisions=scene.production_decisions,
        unresolved=unresolved,
        draft=draft,
        coverage=[
            "固定叙事修订和资产 URI／版本／声明哈希",
            "数值边界状态与支持的状态转换时刻约束",
            "资产属性仅核对清单声明，未读取或检验媒体内容",
            "人物动机与文学合理性未自动分析",
        ],
    )
