"""Project catalog and graph project entry points.

This module deliberately does not import the legacy Operations facade. UI and
CLI graph commands can list/create/open projects without loading the old
Store/Service/extraction/analysis stack.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from .graph_operations import GraphOperations
from .graph_store import GRAPH_SCHEMA_VERSION


def list_projects(directory: str | Path) -> list[dict]:
    """List project files read-only; never opens/migrates or writes them."""
    root = Path(directory).resolve()
    if not root.is_dir():
        return []
    projects = []
    for path in root.glob("*.sqlite3"):
        if not path.is_file():
            continue
        try:
            # closing() is required: a bare `with connect(...)` only commits, it
            # leaves the handle open and Windows then blocks rename/delete.
            with contextlib.closing(
                sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1)
            ) as db:
                version = int(db.execute("PRAGMA user_version").fetchone()[0])
                title_row = db.execute("SELECT value FROM meta WHERE key='title'").fetchone()
                revision_row = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
            title = title_row[0] if title_row else path.stem
            revision = int(revision_row[0]) if revision_row else None
            # Only the current schema is openable; older files are listed so the
            # user can see and clear them, never silently migrated.
            status = "ready" if version == GRAPH_SCHEMA_VERSION else "incompatible"
        except (sqlite3.Error, ValueError, OSError):
            version, title, revision, status = None, path.stem, None, "unreadable"
        projects.append({
            "name": path.stem,
            "path": str(path),
            "title": title,
            "version": version,
            "revision": revision,
            "status": status,
            "last_opened": None,
            "modified_at": path.stat().st_mtime,
        })
    return sorted(projects, key=lambda item: item["modified_at"], reverse=True)


def clear_incompatible(directory: str | Path) -> dict:
    """Remove project files whose schema version is not the current one.

    A file is removed only when it sits directly in ``directory``, has the
    ``.sqlite3`` suffix and reports a readable, older version. Unreadable files
    are reported instead of deleted: a corrupt file may still be recoverable.

    Deletion can be refused by a host trash guard, so a refused file is moved
    into ``_removed/`` instead — out of the project list, still recoverable.
    """
    root = Path(directory).resolve()
    removed: list[dict] = []
    moved: list[dict] = []
    kept: list[dict] = []
    for item in list_projects(root):
        path = Path(item["path"]).resolve()
        if path.parent != root or path.suffix.lower() != ".sqlite3":
            continue
        if item["status"] == "ready":
            continue
        if item["status"] != "incompatible" or not isinstance(item["version"], int):
            kept.append(item)
            continue
        try:
            path.unlink()
        except OSError:
            quarantine = root / "_removed"
            try:
                quarantine.mkdir(exist_ok=True)
                path.replace(quarantine / path.name)
            except OSError:
                kept.append(item)
                continue
            moved.append(item)
            continue
        removed.append(item)
    return {"removed": removed, "moved": moved, "kept": kept}


def create_graph_project(path: str | Path) -> GraphOperations:
    return GraphOperations(path, create=True)


def open_graph_project(path: str | Path) -> GraphOperations:
    return GraphOperations(path)
