"""Shared Pydantic base for model/settings contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
