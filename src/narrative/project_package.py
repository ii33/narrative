"""工程包：一个目录，里面一份 `project.json` 说清这是什么东西。

私有格式就是那一个文件。它写明格式名与版本、这份作品是谁、数据放在哪：

    <名字>.nproj/
      project.json      格式描述：format/version、project{id,title,schema_version,
                        created_at,updated_at}、layout{database,material}、parts
      graph.sqlite3     图（节点、属性记录、候选、修订、原文）
      material/         资料卡 JSON + 图片原文件

图片是二进制，放不进 JSON，所以格式是「一个描述文件 + 它指到的数据」；提取缓存和
会话不属于这份作品，放在包旁边（`<包名>_extraction/`、`<包名>_chats/`）。

`layout(path)` 给出库和各个目录的真实位置，其余模块只管问它，不自己拼路径。
旧包里的 `manifest.json` 在打开时改名并补全成 `project.json`（只做一次）。
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PACKAGE_SUFFIX = ".nproj"
DB_NAME = "graph.sqlite3"
PROJECT_FILE = "project.json"
LEGACY_MANIFEST = "manifest.json"
FORMAT = "narrative-project"
VERSION = 2
# 包里只装「这份作品」：库和资料。提取缓存能重跑，会话是对话记录，都放在包旁边。


@dataclass(frozen=True)
class Layout:
    """一个工程的磁盘布局。包里根目录是包目录，裸工程是库的父目录。"""

    root: Path
    db: Path
    material: Path
    extraction: Path
    chats: Path

    def asdict(self) -> dict:
        return {
            "root": str(self.root),
            "db": str(self.db),
            "material": str(self.material),
            "extraction": str(self.extraction),
            "chats": str(self.chats),
        }


def _package_layout(root: Path) -> Layout:
    """包目录里是库和资料；缓存和会话在包旁边。

    缓存可以重跑，会话是聊天记录——两者都不属于「这份作品」，不该跟着包被复制、
    备份或发给别人。裸工程本来就是这个排法（`<名字>_extraction` 等），包沿用同一套，
    只是把库和资料收进了一个目录。
    """
    sibling = root.parent
    return Layout(
        root=root,
        db=root / DB_NAME,
        material=root / "material",
        extraction=sibling / f"{root.stem}_extraction",
        chats=sibling / f"{root.stem}_chats",
    )


def layout(path: str | Path) -> Layout:
    """工程就是一个 `.nproj` 目录：库和资料在里面，缓存和会话在它旁边。

    只认包。旧版那种「一个 `.sqlite3` + 三个同级目录」不再支持——它正是拷一个文件
    就丢资料、丢会话的形态，留着只会让人继续那样存。
    """
    target = Path(path)
    if target.is_dir():
        if target.suffix.lower() == PACKAGE_SUFFIX or (target / PROJECT_FILE).is_file():
            return _package_layout(target)
        if (target / DB_NAME).is_file():
            return _package_layout(target)
        if not any(target.iterdir()):
            # 空目录：当成还没建库的包。
            return _package_layout(target)
        raise ValueError(f"这个目录不是工程包：{target}")
    if target.name == DB_NAME and (target.parent / PROJECT_FILE).is_file():
        return _package_layout(target.parent)
    if target.suffix.lower() == PACKAGE_SUFFIX:
        # 还不存在的包目录：建库时再创建。
        return _package_layout(target)
    if target.suffix.lower() == ".sqlite3":
        raise ValueError("旧版单文件工程已不再支持：请把它放进 <名字>.nproj/ 再打开")
    raise ValueError(f"工程是一个 .nproj 目录，这个路径不是：{target}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _schema_version(db: Path) -> int | None:
    """库的 schema 版本：写进描述里，读的人不用先开库就知道能不能打开。"""
    try:
        with contextlib.closing(
            sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True, timeout=1)
        ) as handle:
            return int(handle.execute("PRAGMA user_version").fetchone()[0])
    except (sqlite3.Error, OSError, ValueError):
        return None


def write_project(
    root: str | Path,
    *,
    title: str,
    project_id: str,
    created_at: str | None = None,
) -> dict:
    """写那份唯一的描述文件：格式名与版本、这份作品是谁、数据在哪。"""
    root = Path(root)
    now = _now()
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "project": {
            "id": project_id,
            "title": title,
            "schema_version": _schema_version(root / DB_NAME),
            "created_at": created_at or now,
            "updated_at": now,
        },
        "layout": {"database": DB_NAME, "material": "material"},
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / PROJECT_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def _meta(db: Path) -> dict:
    """标题和工程号：只读，打不开就当没有。

    先 resolve：相对路径的 `as_uri()` 会抛 ValueError，被下面的 except 吞掉，
    于是每个相对路径建出来的包都写着「graph」——这个坑真的踩过。
    """
    db = Path(db).resolve()
    try:
        with contextlib.closing(
            sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=1)
        ) as handle:
            title = handle.execute("SELECT value FROM meta WHERE key='title'").fetchone()
            project_id = handle.execute(
                "SELECT value FROM meta WHERE key='project_id'"
            ).fetchone()
        return {
            "title": title[0] if title else db.stem,
            "project_id": project_id[0] if project_id else "",
        }
    except (sqlite3.Error, OSError, ValueError):
        return {"title": db.stem, "project_id": ""}


def project(layout_: Layout) -> dict:
    """读这份描述。文件不在或坏了就从库里补，读到的东西形状总是一样的。"""
    path = layout_.root / PROJECT_FILE
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError):
            data = {}
    meta = _meta(layout_.db)
    raw = data.get("project")
    known: dict = raw if isinstance(raw, dict) else {}
    return {
        "format": data.get("format") or FORMAT,
        "version": data.get("version") or VERSION,
        "project": {
            "id": known.get("id") or meta["project_id"],
            "title": known.get("title") or meta["title"],
            "schema_version": known.get("schema_version", _schema_version(layout_.db)),
            "created_at": known.get("created_at") or "",
            "updated_at": known.get("updated_at") or "",
        },
        "layout": data.get("layout") or {"database": DB_NAME, "material": "material"},
    }


def migrate(root: str | Path) -> bool:
    """把包里的描述升到当前格式。只动那一个文件，返回有没有动过。

    两件事：旧的 `manifest.json` 改名并补全；已经叫 `project.json` 但版本落后的，
    按当前格式重写（id、标题、创建时间照旧保留）。
    """
    root = Path(root)
    legacy = root / LEGACY_MANIFEST
    target = root / PROJECT_FILE

    if legacy.is_file() and not target.exists():
        try:
            old = json.loads(legacy.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            old = {}
        old = old if isinstance(old, dict) else {}
        meta = _meta(root / DB_NAME)
        write_project(
            root,
            title=str(old.get("title") or meta["title"]),
            project_id=str(old.get("project_id") or meta["project_id"]),
            created_at=str(old.get("created_at") or "") or None,
        )
        legacy.unlink(missing_ok=True)
        return True

    if target.is_file():
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        block = raw.get("project")
        inner: dict = block if isinstance(block, dict) else {}
        if raw.get("version") != VERSION:
            # 旧形状把 title/project_id/created_at 放在顶层，新的收在 project 里。
            meta = _meta(root / DB_NAME)
            write_project(
                root,
                title=str(inner.get("title") or raw.get("title") or meta["title"]),
                project_id=str(
                    inner.get("id") or raw.get("project_id") or meta["project_id"]
                ),
                created_at=str(
                    inner.get("created_at") or raw.get("created_at") or ""
                ) or None,
            )
            return True
    return False
