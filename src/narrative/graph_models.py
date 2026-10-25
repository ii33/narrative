"""Version 5 graph project contracts.

Ordinary attribute/relation/participation assertions are retired in v5; current
node state is expressed by append-only property records. Temporal and containment
assertions remain the reviewed graph claim records.
"""

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

# v5 replaces ordinary claim assertions with append-only property records.

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


class NodeEvidence(GraphModel):
    """The exact sentence a node was extracted from."""

    source_id: str
    quote: str = Field(min_length=1)


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
    # Where the reader first meets this node in the source. Story time and
    # reading position are different axes; this one needs no inference.
    disclosure: DisclosureRef | None = None
    # The sentence it came from. Without this a node is just a name plus an
    # offset; the quote is what makes it checkable.
    evidence: NodeEvidence | None = None
    # Who and what takes part. Kept on the event itself: a participant list is
    # part of what the event is, not a separate assertion family.
    participants: list[str] = Field(default_factory=list)
    # Neighbouring events, recorded on the event so a chapter reads as a chain
    # without needing a separate temporal assertion for every pair.
    predecessors: list[str] = Field(default_factory=list)
    successors: list[str] = Field(default_factory=list)
    # Composite events contain atomic ones. A scene is just a larger event, and a
    # scene can itself sit inside a larger one — containment is a node field, not
    # a separate assertion family.
    events: list[str] = Field(default_factory=list)
    # Chapter boundaries are marks on the reading chain, not separate nodes and
    # not hard breaks: a chapter starts or ends at an event, and the chain runs on.
    source_marks: list[dict] = Field(default_factory=list)
    # Retraction keeps history: the record stays, the projection drops it.
    retracted: bool = False

    @model_validator(mode="after")
    def check_occurrence(self) -> Node:
        if self.occurrence and self.node_type != "event":
            raise ValueError("仅事件节点有发生区间；其他对象的时变状态用断言")
        for mark in self.source_marks:
            if not isinstance(mark, dict) or not isinstance(mark.get("source_id"), str):
                raise ValueError("章节标记须含 source_id")
            if mark.get("role") not in {"start", "end"}:
                raise ValueError("章节标记的 role 只能是 start 或 end")
        if self.source_marks and self.node_type != "event":
            raise ValueError("章节标记只挂在事件节点上")
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


class SourceOrder(GraphModel):
    kind: Literal["source_order"] = "source_order"
    id: str = Field(default_factory=new_id)
    source_id: str
    ordinal: int = Field(ge=0)
    context_id: str = "canon"


type GraphObject = Annotated[
    Node | Context | Timeline | SourceOrder,
    Field(discriminator="kind"),
]
GRAPH_OBJECTS = TypeAdapter(list[GraphObject])


def parse_objects(objects: list[dict]) -> list[GraphObject]:
    return GRAPH_OBJECTS.validate_python(objects)
