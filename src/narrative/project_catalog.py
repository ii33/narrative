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
    """Discover actual private entry files; directories/databases are not entries."""
    from .project_package import PACKAGE_SUFFIX, layout, read_entry

    root = Path(directory).resolve()
    if not root.is_dir():
        return []
    entries = set(root.glob(f"*{PACKAGE_SUFFIX}")) | set(root.glob(f"*/*{PACKAGE_SUFFIX}"))
    projects = []
    for entry in sorted(entries):
        if not entry.is_file():
            continue
        try:
            read_entry(entry)
            found = layout(entry)
            projects.append(_describe(found.db, name=entry.stem, path=entry, package=True))
        except ValueError:
            projects.append({"name": entry.stem, "path": str(entry), "db": "", "package": True,
                             "title": entry.stem, "version": None, "revision": None,
                             "status": "unreadable", "last_opened": None,
                             "modified_at": entry.stat().st_mtime})
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
    """Resolve only a valid private project entry; no implicit migration."""
    from .project_package import layout, read_entry

    read_entry(path)
    return layout(path).db


def create_graph_project(path: str | Path) -> GraphOperations:
    """Create a private information file with separate, uniquely named data."""
    from .project_package import layout, write_project

    entry = Path(path).resolve()
    try:
        if entry.exists():
            raise ValueError("工程入口已存在，不能覆盖")
        target = layout(entry)
        if target.root.exists() and (not target.root.is_dir() or any(target.root.iterdir())):
            raise ValueError("工程数据路径已存在，不能覆盖")
        target.db.parent.mkdir(parents=True, exist_ok=True)
        ops = GraphOperations(target.db, create=True)
        info = ops.graph_project_info()
        write_project(entry, title=info["title"], project_id=info["project_id"], locations=target)
        return ops
    except PermissionError as exc:
        blocked = exc.filename or str(entry.parent)
        raise ValueError(
            f"无法在所选位置创建工程，没有写入权限：{blocked}。"
            "请选择可写位置；若普通用户目录也失败，请检查应用运行环境。"
        ) from exc


def open_graph_project(path: str | Path) -> GraphOperations:
    from .project_package import read_entry

    description = read_entry(path)
    ops = GraphOperations(_database(path))
    if ops.graph_project_info()["project_id"] != description["project"]["id"]:
        raise ValueError("工程入口标识与数据库不一致，拒绝打开")
    return ops


def inspect_graph_project(path: str | Path) -> dict:
    """Describe one explicit entry through the same read-only catalog boundary."""
    from .project_package import layout, read_entry
    entry = Path(path).resolve()
    base = {"name": entry.stem, "path": str(entry), "db": "", "package": True,
            "title": entry.stem, "version": None, "revision": None, "last_opened": None, "modified_at": 0.0}
    if not entry.is_file():
        return base | {"status": "missing"}
    try:
        read_entry(entry)
        found = layout(entry)
        return _describe(found.db, name=entry.stem, path=entry, package=True)
    except (ValueError, OSError):
        return base | {"status": "unreadable"}


def next_project_copy_path(source: str | Path) -> Path:
    """Suggest a free entry and all its derived directories without creating it."""
    from .project_package import layout, read_entry
    entry = Path(source).resolve()
    read_entry(entry)
    number = 1
    while True:
        suffix = "-副本" if number == 1 else f"-副本-{number}"
        candidate = entry.with_name(entry.stem + suffix + ".nproj")
        target = layout(candidate)
        if not any(path.exists() for path in (candidate, target.root, target.extraction, target.chats)):
            return candidate
        number += 1


def duplicate_graph_project(source: str | Path, destination: str | Path) -> GraphOperations:
    from .project_copy import duplicate_graph_project as duplicate
    return duplicate(source, destination)
