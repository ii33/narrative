"""Typed current screenplay containers, spans and authoritative organization."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .graph_models import Node
from .state_anchors import StateAnchor


class ScriptModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class CurrentObject(ScriptModel):
    id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    created_at: str
    updated_at: str


class Screenplay(CurrentObject):
    timeline_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    format: Literal["single", "series"] = "single"
    language: str = "zh-CN"
    creative_brief: dict = Field(default_factory=dict)
    structure_revision: int = Field(default=1, ge=1)


class TimelineNode(Node):
    sort_id: int
    suppressed: bool = False
    source_node_id: str | None = None
    point_id: str | None = None


class ScriptTimeline(ScriptModel):
    id: str
    screenplay_id: str
    source_timeline_id: str
    revision: int = Field(default=0, ge=0)
    source_revision: int | None = None
    nodes: list[TimelineNode] = Field(default_factory=list)
    state_anchors: list[StateAnchor] = Field(default_factory=list)
    properties: list[dict] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)
    sync_diagnostics: list[dict] = Field(default_factory=list)
    baseline_nodes: dict[str, dict] = Field(default_factory=dict)
    baseline_properties: dict[str, dict] = Field(default_factory=dict)
    import_summary: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ScriptEpisode(CurrentObject):
    screenplay_id: str
    number: str = "1"
    title: str = Field(min_length=1)
    brief: dict = Field(default_factory=dict)
    target_duration_seconds: float | int | None = Field(default=None, gt=0)
    position: int = Field(ge=0)


class ScriptAct(CurrentObject):
    screenplay_id: str
    episode_id: str | None = None
    title: str = Field(min_length=1)
    brief: dict = Field(default_factory=dict)
    target_duration_seconds: float | int | None = Field(default=None, gt=0)


class StructureEntry(CurrentObject):
    screenplay_id: str
    parent_kind: Literal["screenplay", "episode", "act"]
    parent_id: str
    child_kind: Literal["episode", "act", "segment"]
    child_id: str
    position: int = Field(ge=0)


class AdaptationSpan(CurrentObject):
    timeline_id: str
    name: str = Field(min_length=1)
    start_node_id: str
    end_node_id: str
    boundary_mode: Literal["inclusive_anchors"] = "inclusive_anchors"
    suppression_policy: Literal["exclude_suppressed"] = "exclude_suppressed"


class ScriptSegment(CurrentObject):
    screenplay_id: str
    span_id: str
    title: str = Field(min_length=1)
    role: Literal["normal", "prologue", "epilogue"] = "normal"
    brief: dict = Field(default_factory=dict)
    target_duration_seconds: float | int | None = Field(default=None, gt=0)
    enabled: bool = True
    outline_revision: int = Field(default=1, ge=1)
    outline_basis_id: str | None = None
    adopted_outline_revision: int | None = Field(default=None, ge=1)


MODEL_BY_TABLE = {
    "screenplays": Screenplay,
    "script_timelines": ScriptTimeline,
    "script_episodes": ScriptEpisode,
    "script_acts": ScriptAct,
    "script_structure_entries": StructureEntry,
    "adaptation_spans": AdaptationSpan,
    "script_segments": ScriptSegment,
}
