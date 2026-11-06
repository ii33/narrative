"""Project extraction preferences and existing project-scoped model settings."""
from __future__ import annotations

from .harness import HarnessSettings

GRANULARITIES = {"standard", "fine"}


def load(ops) -> dict:
    with ops.project.store.connect() as db:
        row = db.execute("SELECT value FROM meta WHERE key='extraction_granularity'").fetchone()
    granularity = row[0] if row else "fine"
    if granularity not in GRANULARITIES:
        raise ValueError("工程提取粒度无效，只能是standard或fine")
    return {"extraction_granularity": granularity, "profile": ops.provider_profile(), "api_key_status": ops.provider_api_key_status()}


def save(ops, settings: dict, *, api_key=None, clear_api_key=False) -> dict:
    if not isinstance(settings, dict) or set(settings) - {"extraction_granularity", "profile", "api_key_status"}:
        raise ValueError("工程设置仅支持extraction_granularity和profile")
    granularity = settings.get("extraction_granularity")
    if not isinstance(granularity, str) or granularity not in GRANULARITIES:
        raise ValueError("提取粒度只能是standard或fine")
    from .graph_profile_ops import write_profile_in_transaction
    from .provider_credentials import apply_credential, prepare_credential
    profile = HarnessSettings.model_validate(settings.get('profile'))
    prepared = prepare_credential(ops._project_id, profile.model_dump(), api_key, clear_api_key)
    profile_changed = profile.model_dump() != ops.provider_profile()
    provider_path = ops._provider_store.path.resolve()
    provider_path.parent.mkdir(parents=True, exist_ok=True)
    with ops.project.store.connect(write=True) as db:
        db.execute('ATTACH DATABASE ? AS local_provider', (str(provider_path),))
        if profile_changed or prepared[0] != 'preserve':
            write_profile_in_transaction(db, ops._project_id, profile, 'local_provider')
        apply_credential(db, ops._project_id, profile.model_dump(), prepared, 'local_provider')
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('extraction_granularity',?)", (granularity,))
    return load(ops)
