"""Application-level provider profile storage.

Provider credentials/model settings are application configuration, not narrative
data. They live in a separate SQLite file keyed by graph project_id, so exporting
or backing up a graph project no longer carries model configuration.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

# Settings saved before any project is open: the launcher's global defaults.
GLOBAL_SCOPE = "__global__"


def default_path() -> Path:
    override = os.environ.get("NARRATIVE_PROVIDER_DB")
    if override:
        return Path(override)
    return Path.home() / ".narrative" / "provider-settings.sqlite3"


class ProviderStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_path()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute(
            "CREATE TABLE IF NOT EXISTS provider_settings ("
            "project_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
            "PRIMARY KEY(project_id, key))"
        )
        return db

    def get(self, project_id: str, key: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT value FROM provider_settings WHERE project_id=? AND key=?",
                (project_id, key),
            ).fetchone()
        return row[0] if row else None

    def put(self, project_id: str, key: str, value: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO provider_settings(project_id,key,value) VALUES (?,?,?) "
                "ON CONFLICT(project_id,key) DO UPDATE SET value=excluded.value",
                (project_id, key, value),
            )

    def get_json(self, project_id: str, key: str) -> dict | None:
        raw = self.get(project_id, key)
        return json.loads(raw) if raw else None
