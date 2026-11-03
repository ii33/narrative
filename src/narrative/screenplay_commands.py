"""Typed structure commands, never arbitrary JSON paths or SQL."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from .screenplay_models import ScriptModel


class EpisodeChanges(ScriptModel):
    title: str | None = None
    number: str | None = None
    brief: dict | None = None
    target_duration_seconds: float | int | None = Field(default=None, gt=0)


class ActChanges(ScriptModel):
    title: str | None = None
    brief: dict | None = None
    target_duration_seconds: float | int | None = Field(default=None, gt=0)


class ScreenplayChanges(ScriptModel):
    title: str | None = None
    language: str | None = None
    creative_brief: dict | None = None


class CreateEpisode(ScriptModel):
    op: Literal["create_episode"]
    id: str | None = None
    title: str
    number: str = "1"
    brief: dict = Field(default_factory=dict)
    target_duration_seconds: float | int | None = Field(default=None, gt=0)
    after_entry_id: str | None = None


class CreateAct(ScriptModel):
    op: Literal["create_act"]
    id: str | None = None
    title: str
    episode_id: str | None = None
    brief: dict = Field(default_factory=dict)
    target_duration_seconds: float | int | None = Field(default=None, gt=0)
    after_entry_id: str | None = None


class UpdateEpisode(ScriptModel):
    op: Literal["update_episode"]
    episode_id: str
    expected_revision: int
    changes: EpisodeChanges


class UpdateAct(ScriptModel):
    op: Literal["update_act"]
    act_id: str
    expected_revision: int
    changes: ActChanges


class UpdateScreenplay(ScriptModel):
    op: Literal["update_screenplay"]
    expected_revision: int
    changes: ScreenplayChanges


class Move(ScriptModel):
    op: Literal["move"]
    child_kind: Literal["episode", "act", "segment"]
    child_id: str
    parent_kind: Literal["screenplay", "episode", "act"]
    parent_id: str
    after_entry_id: str | None = None


class MoveTarget(ScriptModel):
    parent_kind: Literal["screenplay", "episode", "act"]
    parent_id: str


class DeleteEpisode(ScriptModel):
    op: Literal["delete_episode"]
    episode_id: str
    expected_revision: int
    move_to: MoveTarget | None = None


class DeleteAct(ScriptModel):
    op: Literal["delete_act"]
    act_id: str
    expected_revision: int
    move_to: MoveTarget | None = None


class DeleteSegment(ScriptModel):
    op: Literal["delete_segment"]
    segment_id: str
    expected_revision: int
    body_policy: Literal["require_empty", "discard"]


ACTION_ADAPTER = TypeAdapter(list[Annotated[CreateEpisode | CreateAct | UpdateEpisode | UpdateAct |
                                         UpdateScreenplay | Move | DeleteEpisode | DeleteAct | DeleteSegment,
                                         Field(discriminator="op")]])
