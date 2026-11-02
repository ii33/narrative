"""Private project entry files and their relative data locations.

A ``.nproj`` file is the only public project entry. Its containing directory has
no naming convention. Database, material, extraction and chat paths are resolved
relative to that file; opening never rewrites the descriptor or historical data.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PACKAGE_SUFFIX = ".nproj"
DB_NAME = "graph.sqlite3"
FORMAT = "narrative-project"
VERSION = 3
_DB_ENTRIES: dict[Path, Path] = {}


@dataclass(frozen=True)
class Layout:
    root: Path
    db: Path
    material: Path
    extraction: Path
    chats: Path
    entry: Path

    def asdict(self) -> dict:
        return {key: str(getattr(self, key)) for key in
                ("root", "db", "material", "extraction", "chats", "entry")}


def _relative(base: Path, value: object, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"工程入口缺少 {name} 路径")
    candidate = Path(value)
    if candidate.is_absolute() or candidate.drive:
        raise ValueError(f"工程 {name} 必须使用相对路径")
    return (base / candidate).resolve()


def read_entry(path: str | Path) -> dict:
    entry = Path(path).resolve()
    if entry.suffix.lower() != PACKAGE_SUFFIX or not entry.is_file():
        raise ValueError("请选择 .nproj 工程入口信息文件，不是目录或数据库")
    try:
        data = json.loads(entry.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("工程入口信息文件无法读取") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("文件不是叙事工程入口")
    if data.get("version") != VERSION:
        raise ValueError("工程入口版本不受支持，仅支持当前格式")
    if (not isinstance(data.get("project"), dict)
        or not isinstance(data["project"].get("id"), str) or not data["project"]["id"].strip()):
        raise ValueError("工程入口缺少工程信息")
    if not isinstance(data.get("layout"), dict):
        raise ValueError("工程入口缺少数据位置")
    return data


def _from_entry(entry: Path, data: dict) -> Layout:
    paths = data["layout"]
    resolved = {key: _relative(entry.parent, paths.get(key), key)
                for key in ("database", "material", "extraction", "chats")}
    found = Layout(root=resolved["database"].parent, db=resolved["database"],
                   material=resolved["material"], extraction=resolved["extraction"],
                   chats=resolved["chats"], entry=entry)
    _DB_ENTRIES[found.db] = entry
    return found


def layout(path: str | Path) -> Layout:
    """Resolve a descriptor, or an internal database already linked to one.

    Public open functions validate ``read_entry`` first. The internal database
    form exists for services that hold ``Operations.project_info().path``.
    """
    target = Path(path).resolve()
    if target.is_dir():
        raise ValueError("目录不是工程入口，请选择其中的 .nproj 信息文件")
    if target.suffix.lower() == PACKAGE_SUFFIX:
        if target.exists():
            return _from_entry(target, read_entry(target))
        data_root = target.parent / f"{target.stem}_data"
        return Layout(data_root, data_root / DB_NAME, data_root / "material",
                      target.parent / f"{target.stem}_extraction",
                      target.parent / f"{target.stem}_chats", target)
    if target.suffix.lower() in {".sqlite3", ".db"}:
        known = _DB_ENTRIES.get(target)
        candidates = [known] if known else []
        candidates += list(target.parent.glob(f"*{PACKAGE_SUFFIX}"))
        candidates += list(target.parent.parent.glob(f"*{PACKAGE_SUFFIX}"))
        for entry in candidates:
            if entry is None or not entry.is_file():
                continue
            try:
                found = _from_entry(entry, read_entry(entry))
            except ValueError:
                continue
            if found.db == target:
                return found
        raise ValueError("数据库不是工程入口；请先选择引用它的 .nproj 信息文件")
    raise ValueError("请选择 .nproj 工程入口信息文件")


def _schema_version(db: Path) -> int | None:
    try:
        with contextlib.closing(sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=1)) as handle:
            return int(handle.execute("PRAGMA user_version").fetchone()[0])
    except (sqlite3.Error, OSError, ValueError):
        return None


def write_project(path: str | Path, *, title: str, project_id: str,
                  created_at: str | None = None, locations: Layout | None = None) -> dict:
    entry = Path(path).resolve()
    if entry.suffix.lower() != PACKAGE_SUFFIX or entry.is_dir():
        raise ValueError("工程入口必须是 .nproj 文件")
    found = locations or layout(entry)
    now = datetime.now(UTC).isoformat()
    payload = {"format": FORMAT, "version": VERSION,
               "project": {"id": project_id, "title": title,
                           "schema_version": _schema_version(found.db),
                           "created_at": created_at or now, "updated_at": now},
               "layout": {key: Path(os.path.relpath(value, entry.parent)).as_posix()
                          for key, value in (("database", found.db), ("material", found.material),
                                             ("extraction", found.extraction), ("chats", found.chats))}}
    entry.parent.mkdir(parents=True, exist_ok=True)
    # Entry creation must not replace a user's prior description.
    with entry.open("x", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, indent=2))
    _DB_ENTRIES[found.db] = entry
    return payload


def project(found: Layout) -> dict:
    return read_entry(found.entry)


def update_title(path: str | Path, title: str) -> dict:
    """Update only the display title in an existing project entry file."""
    normalized = title.strip()
    if not normalized:
        raise ValueError("工程名称不能为空")
    entry = Path(path).resolve()
    data = read_entry(entry)
    data["project"]["title"] = normalized
    data["project"]["updated_at"] = datetime.now(UTC).isoformat()
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{entry.name}.", suffix=".tmp", dir=entry.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(json.dumps(data, ensure_ascii=False, indent=2))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, entry)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return data
