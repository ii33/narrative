"""Provider profile settings, stored outside the graph project database."""

from __future__ import annotations

import json

from .graph_store import GraphStore
from .harness import HarnessSettings
from .provider_store import GLOBAL_SCOPE, ProviderStore


def _profiles(store: ProviderStore, scope: str) -> dict:
    raw = store.get(scope, "harness_profiles")
    if raw is None and scope != GLOBAL_SCOPE:
        raw = store.get(GLOBAL_SCOPE, "harness_profiles")
    return json.loads(raw) if raw else {}


def read_settings(store: ProviderStore, scope: str) -> dict:
    """Saved settings for one scope.

    A project without its own record inherits the application-wide default, so a
    new project is usable without retyping the endpoint.
    """
    raw = store.get(scope, "harness_settings")
    if raw is None and scope != GLOBAL_SCOPE:
        raw = store.get(GLOBAL_SCOPE, "harness_settings")
    if not raw:
        return HarnessSettings().model_dump()
    return HarnessSettings.model_validate_json(raw).model_dump()


def write_profile_in_transaction(db, scope, config, namespace='main'):
    table = namespace+'.provider_settings'
    db.execute(f'CREATE TABLE IF NOT EXISTS {table} (project_id TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(project_id,key))')
    def read(key):
        row = db.execute(f'SELECT value FROM {table} WHERE project_id=? AND key=?', (scope, key)).fetchone()
        if row is None and scope != GLOBAL_SCOPE:
            row = db.execute(f'SELECT value FROM {table} WHERE project_id=? AND key=?', (GLOBAL_SCOPE, key)).fetchone()
        return row[0] if row else None
    profiles = json.loads(read('harness_profiles') or '{}')
    raw_active = read('harness_settings')
    active = HarnessSettings.model_validate_json(raw_active) if raw_active else HarnessSettings()
    profiles.setdefault(active.provider, active.model_dump())
    profiles[config.provider] = config.model_dump()
    for key, value in [('harness_profiles', json.dumps(profiles, ensure_ascii=False)), ('harness_settings', config.model_dump_json())]:
        db.execute(f'INSERT INTO {table} VALUES(?,?,?) ON CONFLICT(project_id,key) DO UPDATE SET value=excluded.value', (scope, key, value))


def write_settings(store: ProviderStore, scope: str, settings: dict, *, api_key=None, clear_api_key=False) -> dict:
    from .provider_credentials import apply_credential, prepare_credential
    config = HarnessSettings.model_validate(settings)
    prepared = prepare_credential(scope, config.model_dump(), api_key, clear_api_key)
    with store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        write_profile_in_transaction(db, scope, config)
        apply_credential(db, scope, config.model_dump(), prepared)
    return config.model_dump()


def settings_for(store: ProviderStore, scope: str, provider: str | None = None) -> dict:
    """One scope's settings, optionally as a named provider profile."""
    from . import providers

    active = read_settings(store, scope)
    if provider is None or provider == active["provider"]:
        return active
    profiles = _profiles(store, scope)
    if provider in profiles:
        return HarnessSettings.model_validate(profiles[provider]).model_dump()
    if not providers.preset(provider)["model"]:
        return HarnessSettings(provider=provider, model="__unconfigured__").model_dump() | {
            "model": ""
        }
    return HarnessSettings(provider=provider).model_dump()


def global_settings(store: ProviderStore, provider: str | None = None) -> dict:
    """Application-wide defaults, used by the launcher before a project is open."""
    return settings_for(store, GLOBAL_SCOPE, provider)


def save_global_settings(store: ProviderStore, settings: dict) -> dict:
    return write_settings(store, GLOBAL_SCOPE, settings)


class ProfileService:
    """Read/write model settings for one project.

    Settings live in an application-level store keyed by ``project_id``; the
    graph database is only read once as a fallback for projects created before
    the split, and never written again.
    """

    def __init__(self, store: GraphStore, project_id: str, provider_store: ProviderStore) -> None:
        self.store = store
        self.project_id = project_id
        self.provider_store = provider_store

    def settings(self, provider: str | None = None) -> dict:
        return settings_for(self.provider_store, self.project_id, provider)

    def save(self, settings: dict, *, api_key=None, clear_api_key=False) -> dict:
        return write_settings(self.provider_store, self.project_id, settings, api_key=api_key, clear_api_key=clear_api_key)

    def credential_status(self, profile=None):
        from .provider_credentials import public_status
        source = profile if profile is not None else self.settings()
        # Credential status depends on endpoint identity, not an unfinished model draft.
        selected = {key: source[key] for key in ('provider', 'base_url', 'key_env') if key in source}
        config = HarnessSettings.model_validate(selected | {'model': '__credential_status__'}).model_dump()
        return public_status(self.provider_store, self.project_id, config)

    def saved_api_key(self, profile):
        from .provider_credentials import saved_key
        config = HarnessSettings.model_validate(profile).model_dump()
        return saved_key(self.provider_store, self.project_id, config)

    def catalog(self) -> list[dict]:
        from . import providers

        return providers.catalog()

    def set_title(self, title: str) -> None:
        normalized = title.strip()
        if not normalized:
            raise ValueError("工程名称不能为空")
        from . import project_package

        layout = project_package.layout(self.store.path)
        previous = self.store.info()["title"]
        project_package.update_title(layout.entry, normalized)
        try:
            with self.store.connect(write=True) as db:
                db.execute("UPDATE meta SET value=? WHERE key='title'", (normalized,))
        except Exception:
            project_package.update_title(layout.entry, previous)
            raise
