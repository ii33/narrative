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


def _describe(db: Path, *, name: str, path: Path, package: bool) -> dict:
    """读一个工程的元信息：只读，不迁移、不写。"""
    try:
        # closing() is required: a bare `with connect(...)` only commits, it
        # leaves the handle open and Windows then blocks rename/delete.
        with contextlib.closing(
            sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=1)
        ) as handle:
            version = int(handle.execute("PRAGMA user_version").fetchone()[0])
            title_row = handle.execute("SELECT value FROM meta WHERE key='title'").fetchone()
            revision_row = handle.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
        title = title_row[0] if title_row else name
        revision = int(revision_row[0]) if revision_row else None
        # Only the current schema is openable; older files are listed so the
        # user can see and clear them, never silently migrated.
        status = "ready" if version == GRAPH_SCHEMA_VERSION else "incompatible"
    except (sqlite3.Error, ValueError, OSError):
        version, title, revision, status = None, name, None, "unreadable"
    return {
        "name": name,
        "path": str(path),
        "db": str(db),
        "package": package,
        "title": title,
        "version": version,
        "revision": revision,
        "status": status,
        "last_opened": None,
        "modified_at": db.stat().st_mtime if db.is_file() else 0.0,
    }


def list_projects(directory: str | Path) -> list[dict]:
    """List projects read-only; never opens/migrates or writes them.

    只列工程包（`<名字>.nproj/`），读的是包里 `graph.sqlite3` 的元信息。
    """
    from .project_package import DB_NAME, PACKAGE_SUFFIX

    root = Path(directory).resolve()
    if not root.is_dir():
        return []
    projects: list[dict] = []
    for package in root.glob(f"*{PACKAGE_SUFFIX}"):
        db = package / DB_NAME
        if package.is_dir() and db.is_file():
            projects.append(
                _describe(db, name=package.stem, path=package, package=True)
            )
    return sorted(projects, key=lambda item: item["modified_at"], reverse=True)


def clear_incompatible(directory: str | Path) -> dict:
    """Move away project packages whose schema version is not the current one.

    Only packages sitting directly in ``directory`` are considered. A package
    that reports a readable, older version is removed; one that cannot be read
    at all is reported instead — a corrupt database may still be recoverable.

    Deletion can be refused by a host trash guard, and a directory cannot be
    unlinked anyway, so a refused package is renamed into ``_removed/``: out of
    the project list, still there if you want it back.
    """
    root = Path(directory).resolve()
    removed: list[dict] = []
    moved: list[dict] = []
    kept: list[dict] = []
    for item in list_projects(root):
        path = Path(item["path"]).resolve()
        if path.parent != root or path.suffix.lower() != ".nproj":
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


def _database(path: str | Path) -> Path:
    """工程可能是裸库、包目录，也可能是包里的 graph.sqlite3：都归到库文件。"""
    from .project_package import layout

    return layout(path).db


def create_graph_project(path: str | Path) -> GraphOperations:
    """建一个工程包：目录、库、manifest 一次到位。"""
    from .project_package import layout as _layout
    from .project_package import write_manifest

    target = _layout(path)
    target.db.parent.mkdir(parents=True, exist_ok=True)
    ops = GraphOperations(target.db, create=True)
    info = ops.graph_project_info()
    write_manifest(target.root, title=info["title"], project_id=info["project_id"])
    return ops


def open_graph_project(path: str | Path) -> GraphOperations:
    return GraphOperations(_database(path))
