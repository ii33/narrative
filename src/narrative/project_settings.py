"""Project extraction preferences and existing project-scoped model settings."""
from __future__ import annotations

from .harness import HarnessSettings

GRANULARITIES = {"standard", "fine"}


def load(ops) -> dict:
    with ops.project.store.connect() as db:
        row = db.execute("SELECT value FROM meta WHERE key='extraction_granularity'").fetchone()
    granularity = row[0] if row else "standard"
    if granularity not in GRANULARITIES:
        raise ValueError("工程提取粒度无效，只能是standard或fine")
    return {"extraction_granularity": granularity, "profile": ops.provider_profile()}


def save(ops, settings: dict) -> dict:
    if not isinstance(settings, dict) or set(settings) - {"extraction_granularity", "profile"}:
        raise ValueError("工程设置仅支持extraction_granularity和profile")
    granularity = settings.get("extraction_granularity")
    if not isinstance(granularity, str) or granularity not in GRANULARITIES:
        raise ValueError("提取粒度只能是standard或fine")
    profile = HarnessSettings.model_validate(settings.get("profile")).model_dump()
    if profile != ops.provider_profile():
        ops.save_provider_profile(profile)
    with ops.project.store.connect(write=True) as db:
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('extraction_granularity',?)", (granularity,))
    return load(ops)
