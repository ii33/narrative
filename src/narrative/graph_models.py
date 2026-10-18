"""Version 4 graph project contracts. Assertions are the sole claim-bearing record."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


def new_id() -> str:
    return uuid4().hex


class GraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


type NodeType = Literal[
    "person", "item", "location", "event", "scene", "storyline", "group", "other"
]
type Origin = Literal["original", "inference", "creative", "production"]


class TimeRef(GraphModel):
    timeline_id: str = "story"
    form: Literal["number", "datetime", "event_boundary"]
    number: float | None = None
    datetime: str | None = None
    event_id: str | None = None
    boundary: Literal["start", "end"] | None = None

    @model_validator(mode="after")
    def check_form(self) -> TimeRef:
        if self.form == "number" and self.number is not None and not any(
            (self.datetime, self.event_id, self.boundary)
        ):
            return self
        if self.form == "datetime" and self.datetime and not any(
            (self.number is not None, self.event_id, self.boundary)
        ):
            return self
        if self.form == "event_boundary" and self.event_id and self.boundary and not any(
            (self.number is not None, self.datetime)
        ):
            return self
        raise ValueError("时间引用须且仅须提供对应数值、日期或事件起止边界")


class TimeBound(GraphModel):
    state: Literal["known", "unknown", "unbounded"] = "unknown"
    ref: TimeRef | None = None

    @model_validator(mode="after")
    def check_ref(self) -> TimeBound:
        if (self.state == "known") != (self.ref is not None):
            raise ValueError("known 边界须有时间引用，unknown/unbounded 不可附引用")
        return self


class TimeExtent(GraphModel):
    kind: Literal["point", "interval"] = "interval"
    point: TimeRef | None = None
    start: TimeBound = Field(default_factory=TimeBound)
    end: TimeBound = Field(default_factory=TimeBound)

    @model_validator(mode="after")
    def check_shape(self) -> TimeExtent:
        if self.kind == "point":
            if self.point is None or self.start.state != "unknown" or self.end.state != "unknown":
                raise ValueError("时间点须仅指定 point，不可混用区间边界")
        elif self.point is not None:
            raise ValueError("时间区间不可指定 point")
        return self


class DisclosureRef(GraphModel):
    source_id: str
    start: int = Field(ge=0)


class Node(GraphModel):
    kind: Literal["node"] = "node"
    id: str = Field(default_factory=new_id)
    node_type: NodeType
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    subtype: str | None = None
    context_id: str = "canon"
    identity_note: str = ""
    occurrence: TimeExtent | None = None

    @model_validator(mode="after")
    def check_occurrence(self) -> Node:
        if self.occurrence and self.node_type != "event":
            raise ValueError("仅事件节点有发生区间；其他对象的时变状态用断言")
        return self


class PredicateDefinition(GraphModel):
    kind: Literal["predicate"] = "predicate"
    id: str
    label: str = Field(min_length=1)
    semantic: Literal["attribute", "relation", "participation", "temporal", "containment"]
    subject_types: list[NodeType] = Field(default_factory=list)
    object_types: list[NodeType] = Field(default_factory=list)
    value_type: Literal["node", "scalar", "either"] = "node"
    functional: bool = False
    temporal_relation: Literal["before", "after", "same", "overlaps", "contains"] | None = None
    description: str = ""

    @model_validator(mode="after")
    def check_temporal_relation(self) -> PredicateDefinition:
        if self.semantic == "temporal" and self.temporal_relation is None:
            raise ValueError("时序谓词须指定 temporal_relation")
        if self.semantic != "temporal" and self.temporal_relation is not None:
            raise ValueError("只有时序谓词可指定 temporal_relation")
        if self.semantic == "containment" and self.value_type != "node":
            raise ValueError("组织包含谓词须使用节点宾语")
        return self


class Context(GraphModel):
    kind: Literal["context"] = "context"
    id: str
    label: str = Field(min_length=1)
    context_type: Literal["canon", "branch", "hypothesis", "viewpoint"]
    parent_id: str | None = None
    holder_id: str | None = None

    @model_validator(mode="after")
    def check_viewpoint(self) -> Context:
        if self.context_type == "viewpoint" and not self.holder_id:
            raise ValueError("人物观点语境须指定人物持有人")
        if self.context_type != "viewpoint" and self.holder_id:
            raise ValueError("只有人物观点语境可指定持有人")
        return self


class Timeline(GraphModel):
    kind: Literal["timeline"] = "timeline"
    id: str
    context_id: str = "canon"
    label: str = Field(min_length=1)
    calendar: str | None = None


class Evidence(GraphModel):
    kind: Literal["evidence"] = "evidence"
    id: str = Field(default_factory=new_id)
    source_id: str
    source_hash: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)


class EvidenceLink(GraphModel):
    kind: Literal["evidence_link"] = "evidence_link"
    id: str = Field(default_factory=new_id)
    assertion_id: str
    evidence_id: str
    role: Literal["supports", "refutes", "mentions"] = "supports"


class Assertion(GraphModel):
    kind: Literal["assertion"] = "assertion"
    id: str = Field(default_factory=new_id)
    subject_id: str
    predicate_id: str
    object_id: str | None = None
    value: str | bool | int | float | None = None
    scope: Literal["world", "claim", "knowledge"] = "world"
    holder_id: str | None = None
    context_id: str = "canon"
    polarity: Literal["positive", "negative"] = "positive"
    modality: Literal["actual", "possible", "hypothetical", "reported"] = "actual"
    certainty: Literal["certain", "suspects", "denies", "unknown"] = "unknown"
    validity: TimeExtent = Field(default_factory=TimeExtent)
    disclosure: DisclosureRef | None = None
    origin: Origin = "original"
    assessment: Literal["supported", "disputed", "retracted", "unresolved"] = "unresolved"
    derived_from: list[str] = Field(default_factory=list)
    supersedes_id: str | None = None
    qualifiers: dict[str, str] = Field(default_factory=dict)
    temporal_left: TimeRef | None = None
    temporal_right: TimeRef | None = None
    order_index: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode="after")
    def check_claim(self) -> Assertion:
        if (self.object_id is None) == (self.value is None) and self.temporal_right is None:
            raise ValueError("断言须且仅须有节点宾语或标量值")
        if self.object_id is not None and self.value is not None:
            raise ValueError("节点宾语与标量值不能同时存在")
        if self.scope == "world" and self.holder_id:
            raise ValueError("世界断言不可指定人物持有人")
        if self.scope != "world" and not self.holder_id:
            raise ValueError("人物主张／认知须指定持有人")
        if self.origin == "inference" and not self.derived_from:
            raise ValueError("推断断言须给出所依据的断言 ID")
        return self


class SourceOrder(GraphModel):
    kind: Literal["source_order"] = "source_order"
    id: str = Field(default_factory=new_id)
    source_id: str
    ordinal: int = Field(ge=0)
    context_id: str = "canon"


type GraphObject = Annotated[
    Node | PredicateDefinition | Context | Timeline | Evidence | EvidenceLink | Assertion | SourceOrder,
    Field(discriminator="kind"),
]
GRAPH_OBJECTS = TypeAdapter(list[GraphObject])


def parse_objects(objects: list[dict]) -> list[GraphObject]:
    return GRAPH_OBJECTS.validate_python(objects)
