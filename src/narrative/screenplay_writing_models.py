"""Typed current screenplay writing objects and bounded edit actions."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def timestamp():
    return datetime.now(UTC).isoformat()


class WritingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class ScenePlan(WritingModel):
    title: str = ""
    interior_exterior: Literal["INT", "EXT", "INT_EXT", "UNSPECIFIED"] = "UNSPECIFIED"
    location_id: str | None = None
    location_text: str = ""
    time_label: str = ""
    story_time: dict[str, Any] | None = None
    cast_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    goal: str = ""
    conflict: str = ""
    outcome: str = ""
    estimated_duration_seconds: float | None = Field(default=None, gt=0)
    narrative_mode: Literal["normal", "flashback", "dream", "imagined"] = "normal"
    source_event_ids: list[str] = Field(default_factory=list)
    continuity_requirements: list[dict[str, Any]] = Field(default_factory=list)
    production_notes: str = ""
    presentation: Literal["title_card", "black_screen", "credits", "other"] | None = None


class UnitDraft(WritingModel):
    id: str | None = None
    kind: Literal["scene", "screen_segment"] = "scene"
    enabled: bool = True
    scene_number: str | None = None
    plan: ScenePlan


class ScriptUnit(WritingModel):
    id: str
    segment_id: str
    kind: Literal["scene", "screen_segment"] = "scene"
    position: int = Field(ge=0, strict=True)
    enabled: bool = True
    scene_number: str | None = None
    plan: ScenePlan
    plan_revision: int = Field(default=1, ge=1, strict=True)
    body_revision: int = Field(default=0, ge=0, strict=True)
    revision: int = Field(default=1, ge=1, strict=True)
    body_basis_id: str | None = None
    created_at: str = Field(default_factory=timestamp)
    updated_at: str = Field(default_factory=timestamp)


class TextPayload(WritingModel):
    text: str


class DialoguePayload(TextPayload):
    speaker_id: str | None = None
    speaker_label: str = ""
    delivery: Literal["on_screen", "off_screen", "voice_over", "inner_voice"] = "on_screen"
    parenthetical: str = ""
    simultaneous_group_id: str | None = None

    @model_validator(mode="after")
    def speaker(self):
        if not self.speaker_id and not self.speaker_label.strip():
            raise ValueError("未绑定对白人物时须填写speaker_label")
        return self


class ScreenTextPayload(TextPayload):
    presentation: Literal["overlay", "card"] = "overlay"


class SoundPayload(TextPayload):
    category: Literal["effect", "music", "other"] = "effect"
    entity_id: str | None = None


class TransitionPayload(WritingModel):
    kind: str = Field(min_length=1)
    text: str = ""


PAYLOADS = {"action": TextPayload, "dialogue": DialoguePayload, "screen_text": ScreenTextPayload,
            "sound": SoundPayload, "transition": TransitionPayload}


class BlockDraft(WritingModel):
    id: str | None = None
    type: Literal["action", "dialogue", "screen_text", "sound", "transition"]
    payload: dict[str, Any]

    @model_validator(mode="after")
    def payload_shape(self):
        self.payload = PAYLOADS[self.type].model_validate(self.payload).model_dump(mode="json")
        return self


class ScriptBlock(WritingModel):
    id: str
    unit_id: str
    position: int = Field(ge=0, strict=True)
    type: Literal["action", "dialogue", "screen_text", "sound", "transition"]
    payload: dict[str, Any]

    @model_validator(mode="after")
    def payload_shape(self):
        self.payload = PAYLOADS[self.type].model_validate(self.payload).model_dump(mode="json")
        return self


class EntityUsage(WritingModel):
    id: str
    unit_id: str
    block_id: str | None = None
    entity_id: str
    role: Literal["present", "speaker", "mentioned", "used", "visible"]


class WritingBasis(WritingModel):
    id: str
    target_kind: Literal["segment", "unit"]
    target_id: str
    stage: Literal["scene_table", "body"]
    span_fingerprint: str
    context_fingerprint: str
    plan_fingerprint: str | None = None
    dependency_refs: list[dict[str, str]] = Field(default_factory=list)
    manifest_ref: str | None = None
    task_id: str | None = None
    reason: str
    fingerprint_version: int = 2
    created_at: str = Field(default_factory=timestamp)


class UnitEdit(WritingModel):
    kind: Literal["scene", "screen_segment"] | None = None
    enabled: bool | None = None
    scene_number: str | None = None
    plan: ScenePlan | None = None


class InsertUnit(WritingModel):
    op: Literal["insert"]
    unit: UnitDraft
    after_id: str | None = None


class UpdateUnit(WritingModel):
    op: Literal["update"]
    unit_id: str
    changes: UnitEdit


class MoveUnit(WritingModel):
    op: Literal["move"]
    unit_id: str
    after_id: str | None = None


class DeleteUnit(WritingModel):
    op: Literal["delete"]
    unit_id: str
    body_policy: Literal["require_empty", "discard"]


class InsertBlock(WritingModel):
    op: Literal["insert"]
    block: BlockDraft
    after_id: str | None = None


class UpdateBlock(WritingModel):
    op: Literal["update"]
    block_id: str
    block: BlockDraft


class MoveBlock(WritingModel):
    op: Literal["move"]
    block_id: str
    after_id: str | None = None


class DeleteBlock(WritingModel):
    op: Literal["delete"]
    block_id: str


UnitChange = Annotated[InsertUnit | UpdateUnit | MoveUnit | DeleteUnit, Field(discriminator="op")]
BlockChange = Annotated[InsertBlock | UpdateBlock | MoveBlock | DeleteBlock, Field(discriminator="op")]
MODEL_BY_TABLE = {"script_units": ScriptUnit, "script_blocks": ScriptBlock,
                  "script_entity_usages": EntityUsage, "script_writing_bases": WritingBasis}
