from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

Scalar = StrictStr | StrictBool | StrictInt | StrictFloat | None
Origin = Literal["original", "inference", "creative", "production"]
RecordStatus = Literal["accepted", "disputed", "superseded"]
Persistence = Literal["until_changed", "bounded", "instant", "unknown"]
Action = Literal["move", "transfer", "observe", "dress", "injure", "treat", "tell", "act", "narrative"]


def uid() -> str:
    return uuid4().hex


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Evidence(Model):
    source_id: str
    source_hash: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)


class Entity(Model):
    kind: Literal["entity"] = "entity"
    id: str = Field(default_factory=uid)
    name: str = Field(min_length=1)
    entity_type: Literal["person", "location", "item", "other"] = "person"
    aliases: list[str] = Field(default_factory=list)
    subtype: str | None = None
    description: str = ""
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    origin: Origin = "original"
    derived_from: list[str] = Field(default_factory=list)
    locked: bool = False


class Assertion(Model):
    kind: Literal["assertion"] = "assertion"
    id: str = Field(default_factory=uid)
    subject_id: str
    predicate: str = Field(min_length=1)
    value: str | bool | int | float | None
    scope: Literal["world", "claim", "knowledge"] = "world"
    holder_id: str | None = None
    certainty: Literal["unknown", "certain", "suspects", "denies"] = "unknown"
    learning_method: str | None = None
    source_character_id: str | None = None
    story_time: str | None = None
    disclosure_position: int | None = Field(default=None, ge=0)
    origin: Literal["original", "inference", "creative", "production"] = "original"
    status: Literal["accepted", "disputed", "superseded"] = "accepted"
    evidence: list[Evidence] = Field(default_factory=list)
    validity: "TemporalScope" = Field(default_factory=lambda: TemporalScope())
    value_entity_id: str | None = None
    derived_from: list[str] = Field(default_factory=list)
    locked: bool = False
    properties: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_scope(self):
        if self.value is not None and self.value_entity_id is not None:
            raise ValueError("断言值不能同时为标量与实体引用")
        if self.scope != "world" and not self.holder_id:
            raise ValueError("人物主张／认知必须指定所属人物")
        if self.scope == "world" and self.holder_id:
            raise ValueError("世界事实不能带人物认知持有者")
        return self


class EventParticipant(Model):
    entity_id: str
    role: str = Field(min_length=1)


class Event(Model):
    kind: Literal["event"] = "event"
    id: str = Field(default_factory=uid)
    title: str = Field(min_length=1)
    action: Action
    actor_id: str | None = None
    target_id: str | None = None
    item_id: str | None = None
    location_id: str | None = None
    participants: list[EventParticipant] = Field(default_factory=list)
    narrative_type: str | None = None
    time_kind: Literal["point", "interval", "relative", "unknown"] = "unknown"
    time_start: float | None = None
    time_end: float | None = None
    after_event_id: str | None = None
    disclosure_position: int | None = Field(default=None, ge=0)
    status: Literal["accepted", "disputed", "superseded"] = "accepted"
    evidence: list[Evidence] = Field(default_factory=list)
    origin: Origin = "original"
    assertion_id: str | None = None
    slot: str = Field(default="default", min_length=1)
    state_value: Scalar = None
    persistence: Persistence | None = None
    valid_until: float | None = None
    min_duration: float | None = Field(default=None, ge=0)
    max_duration: float | None = Field(default=None, ge=0)
    preconditions: list["Condition"] = Field(default_factory=list)
    effects: list["StateEffect"] = Field(default_factory=list)
    knowledge_attitude: Literal[
        "informed", "knows", "believes", "suspects", "denies", "unknown"
    ] = "informed"
    knowledge_used: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    locked: bool = False
    properties: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_time(self):
        if self.narrative_type is not None and not self.narrative_type.strip():
            raise ValueError("叙事类型不能为空白")
        if len({(item.entity_id, item.role) for item in self.participants}) != len(self.participants):
            raise ValueError("事件参与实体和角色不能重复")
        if self.action == "narrative":
            if not self.actor_id and not self.participants:
                raise ValueError("叙事事件须有至少一位实体参与")
            if (
                self.assertion_id is not None
                or self.effects
                or self.preconditions
                or self.knowledge_used
                or self.state_value is not None
                or self.persistence is not None
                or self.valid_until is not None
                or self.slot != "default"
                or self.knowledge_attitude != "informed"
            ):
                raise ValueError("叙事事件不能附带状态或认知效果")
        if self.action == "act":
            if self.actor_id is None:
                raise ValueError("叙事动作须指定行动人物")
            if (
                self.assertion_id is not None
                or self.effects
                or self.preconditions
                or self.knowledge_used
                or self.state_value is not None
                or self.persistence is not None
                or self.valid_until is not None
                or self.slot != "default"
                or self.knowledge_attitude != "informed"
            ):
                raise ValueError("叙事动作不能附带状态或认知效果")
        if self.time_kind == "point":
            if self.time_start is None or self.time_end not in (None, self.time_start):
                raise ValueError("时间点须指定 time_start，time_end 应为空或相同")
        elif self.time_kind == "interval":
            if self.time_start is None or self.time_end is None or self.time_end < self.time_start:
                raise ValueError("时间区间须指定起止且结束不早于开始")
        elif self.time_start is not None or self.time_end is not None:
            raise ValueError("相对或未定时间不能附带猜测的数值坐标")
        if self.time_kind == "relative" and not self.after_event_id:
            raise ValueError("相对时间须指定 after_event_id")
        if self.time_kind == "unknown" and self.after_event_id:
            raise ValueError("已指定先后关系的事件应使用相对时间")
        if self.valid_until is not None:
            end = self.time_end if self.time_end is not None else self.time_start
            if end is not None and self.valid_until <= end:
                raise ValueError("状态有效终点须晚于事件完成时刻")
        if self.persistence == "bounded" and self.valid_until is None:
            raise ValueError("有界状态须指定 valid_until")
        if (
            self.min_duration is not None
            and self.max_duration is not None
            and self.min_duration > self.max_duration
        ):
            raise ValueError("最小时长不能超过最大时长")
        return self


class TemporalScope(Model):
    """Story validity: inclusive start, exclusive end; absent anchors mean unresolved."""

    start: float | None = None
    end: float | None = None
    start_event_id: str | None = None
    end_event_id: str | None = None
    label: str | None = None

    @model_validator(mode="after")
    def check_bounds(self):
        if self.start is not None and self.start_event_id:
            raise ValueError("有效期起点不能同时指定数值和事件")
        if self.end is not None and self.end_event_id:
            raise ValueError("有效期终点不能同时指定数值和事件")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("有效期终点必须晚于起点")
        return self


class Condition(Model):
    subject_id: str
    property_id: str
    operator: Literal["eq", "ne", "known", "unknown"] = "eq"
    value: Scalar = None
    value_entity_id: str | None = None


class StateEffect(Model):
    subject_id: str
    property_id: str
    value: Scalar = None
    value_entity_id: str | None = None
    operation: Literal["set", "clear"] = "set"
    persistence: Persistence | None = None
    valid_until: float | None = None

    @model_validator(mode="after")
    def check_value(self):
        if self.value_entity_id is not None and self.value is not None:
            raise ValueError("状态值不能同时为标量与实体引用")
        if self.persistence == "bounded" and self.valid_until is None:
            raise ValueError("有界效果须指定 valid_until")
        return self


class Record(Model):
    id: str = Field(default_factory=uid)
    origin: Origin = "original"
    status: RecordStatus = "accepted"
    evidence: list[Evidence] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    locked: bool = False
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class PropertyDefinition(Record):
    kind: Literal["property"] = "property"
    name: str = Field(min_length=1)
    value_type: Literal["string", "number", "boolean", "entity"]
    subject_types: list[Literal["person", "location", "item", "other"]] = Field(
        default_factory=list
    )
    reference_type: Literal["person", "location", "item", "other"] | None = None
    persistence: Persistence = "unknown"
    description: str = ""


class StateFact(Record):
    kind: Literal["state"] = "state"
    subject_id: str
    property_id: str
    value: Scalar = None
    value_entity_id: str | None = None
    validity: TemporalScope = Field(default_factory=TemporalScope)
    persistence: Persistence | None = None

    @model_validator(mode="after")
    def check_value(self):
        if self.value is not None and self.value_entity_id is not None:
            raise ValueError("状态值不能同时为标量与实体引用")
        if (
            self.persistence == "bounded"
            and self.validity.end is None
            and not self.validity.end_event_id
        ):
            raise ValueError("有界状态须指定有效期终点")
        return self


class Relation(Record):
    kind: Literal["relation"] = "relation"
    subject_id: str
    predicate: str = Field(min_length=1)
    object_id: str
    directed: bool = True
    validity: TemporalScope = Field(default_factory=TemporalScope)
    certainty: Literal["unknown", "certain", "suspects", "denies"] = "unknown"


class KnowledgeRecord(Record):
    kind: Literal["knowledge"] = "knowledge"
    holder_id: str
    assertion_id: str
    attitude: Literal[
        "informed", "knows", "believes", "suspects", "denies", "unknown", "forgotten"
    ] = "unknown"
    acquisition: Literal["observed", "told", "inferred", "read", "initial", "unknown"] = "unknown"
    source_character_id: str | None = None
    event_id: str | None = None
    validity: TemporalScope = Field(default_factory=TemporalScope)


class WorldRule(Record):
    kind: Literal["rule"] = "rule"
    title: str = Field(min_length=1)
    rule_type: Literal["require", "forbid", "knowledge_required"]
    conditions: list[Condition] = Field(default_factory=list)
    assertion_id: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    validity: TemporalScope = Field(default_factory=TemporalScope)
    closed_world: bool = False
    severity: Literal["error", "warning"] = "warning"


class AssetRequirement(Model):
    entity_id: str
    role: str = Field(min_length=1)
    required: bool = True
    attributes: dict[str, Scalar] = Field(default_factory=dict)


class AssetBinding(Record):
    kind: Literal["asset_binding"] = "asset_binding"
    entity_id: str
    role: str = Field(min_length=1)
    asset_uri: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    attributes: dict[str, Scalar] = Field(default_factory=dict)


class Scene(Record):
    kind: Literal["scene"] = "scene"
    title: str = Field(min_length=1)
    validity: TemporalScope = Field(default_factory=TemporalScope)
    entity_ids: list[str] = Field(default_factory=list)
    constraints: list[Condition] = Field(default_factory=list)
    asset_requirements: list[AssetRequirement] = Field(default_factory=list)
    binding_ids: list[str] = Field(default_factory=list)
    production_decisions: list[str] = Field(default_factory=list)


class Intervention(Record):
    kind: Literal["intervention"] = "intervention"
    prompt: str = Field(min_length=1)
    entity_ids: list[str] = Field(default_factory=list)
    validity: TemporalScope = Field(default_factory=TemporalScope)
    constraints: list[Condition] = Field(default_factory=list)
    event_budget: int = Field(default=5, ge=1, le=20, strict=True)
    entity_budget: int = Field(default=0, ge=0, le=20, strict=True)
    model_call_budget: int = Field(default=2, ge=0, le=20, strict=True)


class CreativeIntent(Record):
    kind: Literal["creative_intent"] = "creative_intent"
    actor_id: str
    goal: str = Field(min_length=1)
    intervention_id: str | None = None
    constraints: list[Condition] = Field(default_factory=list)
    proposed_event_ids: list[str] = Field(default_factory=list)


class EventLink(Record):
    kind: Literal["event_link"] = "event_link"
    source_event_id: str
    target_event_id: str
    link_type: Literal["precedes", "causes", "motivates", "contrasts", "echoes", "other"]
    description: str = ""
    interpretive: bool = True

    @model_validator(mode="after")
    def provenance(self):
        if self.interpretive and self.origin == "original":
            self.origin = "inference"
        if self.origin == "inference" and not self.derived_from:
            self.derived_from = [self.source_event_id, self.target_event_id]
        return self


class Storyline(Record):
    kind: Literal["storyline"] = "storyline"
    title: str = Field(min_length=1)
    line_type: Literal["main", "branch", "unknown"] = "unknown"
    event_ids: list[str] = Field(min_length=1)
    description: str = ""
    interpretive: Literal[True] = True

    @model_validator(mode="after")
    def provenance(self):
        if self.origin == "original":
            self.origin = "inference"
        if not self.derived_from:
            self.derived_from = list(self.event_ids)
        return self


class Classification(Record):
    kind: Literal["classification"] = "classification"
    target_id: str
    target_kind: Literal["entity", "assertion", "relation", "event", "storyline"]
    category: str = Field(min_length=1)
    note: str = ""
    interpretive: Literal[True] = True

    @model_validator(mode="after")
    def provenance(self):
        if self.origin == "original":
            self.origin = "inference"
        if not self.derived_from:
            self.derived_from = [self.target_id]
        return self


class ObservedAttribute(Model):
    entity_id: str
    attribute: str = Field(min_length=1)
    value: Scalar = None
    certainty: Literal["certain", "uncertain", "unknown"] = "unknown"
    note: str = ""


class ArtifactObservation(Record):
    kind: Literal["artifact_observation"] = "artifact_observation"
    scene_id: str
    scene_revision: int = Field(ge=0)
    artifact_uri: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    attributes: list[ObservedAttribute] = Field(default_factory=list)
    media_note: str = ""


class ArtifactAcceptance(Record):
    kind: Literal["artifact_acceptance"] = "artifact_acceptance"
    scene_id: str
    scene_revision: int = Field(ge=0)
    observation_ids: list[str] = Field(min_length=1)
    decision: Literal["accepted", "rejected", "needs_review"] = "needs_review"
    deviations: list[str] = Field(default_factory=list)


Operation = Annotated[
    Entity
    | Assertion
    | Event
    | PropertyDefinition
    | StateFact
    | Relation
    | KnowledgeRecord
    | WorldRule
    | AssetBinding
    | Scene
    | Intervention
    | CreativeIntent
    | EventLink
    | Storyline
    | Classification
    | ArtifactObservation
    | ArtifactAcceptance,
    Field(discriminator="kind"),
]

OBJECT_TABLES = {
    "entity": "entities",
    "assertion": "assertions",
    "event": "events",
    **{
        kind: "records"
        for kind in (
            "property",
            "state",
            "relation",
            "knowledge",
            "rule",
            "asset_binding",
            "scene",
            "intervention",
            "creative_intent",
            "event_link",
            "storyline",
            "classification",
            "artifact_observation",
            "artifact_acceptance",
        )
    },
}


class ChangeSet(Model):
    id: str = Field(default_factory=uid)
    expected_revision: int = Field(ge=0)
    operations: list[Operation] = Field(min_length=1, max_length=500)
    source: Literal["manual", "model", "correction"] = "manual"
    assumptions: list[str] = Field(default_factory=list)
    affected_scope: str = ""
    run_id: str | None = None
    note: str = ""
    allow_locked_changes: bool = False


class Diagnostic(Model):
    rule_id: str
    severity: Literal["error", "warning", "info"]
    verdict: Literal["consistent", "conflict", "insufficient", "unsupported"]
    object_id: str | None = None
    message: str
    coverage: str
    suggestion: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    time: TemporalScope | None = None


Assertion.model_rebuild()
Event.model_rebuild()
