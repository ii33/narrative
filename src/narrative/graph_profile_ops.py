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


def write_settings(store: ProviderStore, scope: str, settings: dict) -> dict:
    config = HarnessSettings.model_validate(settings)
    profiles = _profiles(store, scope)
    profiles.setdefault(read_settings(store, scope)["provider"], read_settings(store, scope))
    profiles[config.provider] = config.model_dump()
    store.put(scope, "harness_profiles", json.dumps(profiles, ensure_ascii=False))
    store.put(scope, "harness_settings", config.model_dump_json())
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

    def save(self, settings: dict) -> dict:
        return write_settings(self.provider_store, self.project_id, settings)

    def catalog(self) -> list[dict]:
        from . import providers

        return providers.catalog()

    def set_title(self, title: str) -> None:
        if not title.strip():
            raise ValueError("项目标题不能为空")
        with self.store.connect(write=True) as db:
            db.execute("UPDATE meta SET value=? WHERE key='title'", (title.strip(),))
