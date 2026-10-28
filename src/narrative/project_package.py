"""工程包：一个目录装下库、资料、提取缓存和会话。

裸工程是「一个 `.sqlite3` + 三个同名目录」散在同级，备份、搬家、发给别人都容易漏。
工程包把它们收进一个目录：

    <名字>.nproj/
      manifest.json     这份包是什么、什么时候封的
      graph.sqlite3     图（节点、属性记录、候选、修订、原文）
      material/         资料卡 JSON + 图片原文件
      extraction/       提取缓存 + 运行时工作目录
      chats/            协作者会话

两种布局都认：`layout(path)` 给出库和各个目录的真实位置，其余模块只管问它，
不自己拼 `_material`、`_extraction`、`_chats`。裸工程照旧能开——包里包外是同一套
读写路径，区别只在根目录在哪。
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PACKAGE_SUFFIX = ".nproj"
DB_NAME = "graph.sqlite3"
MANIFEST_NAME = "manifest.json"
FORMAT = "narrative-project"
VERSION = 1
PARTS = ("material", "extraction", "chats")


@dataclass(frozen=True)
class Layout:
    """一个工程的磁盘布局。包里根目录是包目录，裸工程是库的父目录。"""

    root: Path
    db: Path
    material: Path
    extraction: Path
    chats: Path
    package: bool

    def asdict(self) -> dict:
        return {
            "root": str(self.root),
            "db": str(self.db),
            "material": str(self.material),
            "extraction": str(self.extraction),
            "chats": str(self.chats),
            "package": self.package,
        }


def _package_layout(root: Path) -> Layout:
    return Layout(
        root=root,
        db=root / DB_NAME,
        material=root / "material",
        extraction=root / "extraction",
        chats=root / "chats",
        package=True,
    )


def _loose_layout(db: Path) -> Layout:
    parent = db.parent
    stem = db.stem
    return Layout(
        root=parent,
        db=db,
        material=parent / f"{stem}_material",
        extraction=parent / f"{stem}_extraction",
        chats=parent / f"{stem}_chats",
        package=False,
    )


def layout(path: str | Path) -> Layout:
    """工程在哪、库叫什么、三个目录在哪。目录、包目录、库文件都收。"""
    target = Path(path)
    if target.is_dir():
        if target.suffix.lower() == PACKAGE_SUFFIX or (target / MANIFEST_NAME).is_file():
            return _package_layout(target)
        if (target / DB_NAME).is_file():
            return _package_layout(target)
        found = sorted(target.glob("*.sqlite3"))
        if len(found) == 1:
            return layout(found[0])
        if not found:
            # 空目录：按包处理，库还没建。
            return _package_layout(target)
        raise ValueError(f"目录里有多个库，请指定是哪一个：{target}")
    if not target.is_file():
        # 还没建库的路径也认：资料和会话可以先于库存在。
        return _loose_layout(target)
    if target.name == DB_NAME and (target.parent / MANIFEST_NAME).is_file():
        return _package_layout(target.parent)
    return _loose_layout(target)


def _meta(db: Path) -> dict:
    """标题和工程号：只读，打不开就当没有。"""
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


def manifest(layout_: Layout) -> dict:
    path = layout_.root / MANIFEST_NAME
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    meta = _meta(layout_.db)
    return {
        "format": FORMAT,
        "version": VERSION,
        "title": meta["title"],
        "project_id": meta["project_id"],
        "package": layout_.package,
        "parts": {name: (layout_.root / name).is_dir() for name in PARTS},
    }


def _move_db(db: Path, target: Path) -> None:
    """库连同 WAL 一起搬：只搬主文件会丢掉还没合并的事务。"""
    db.rename(target)
    for suffix in ("-wal", "-shm"):
        sidecar = db.with_name(db.name + suffix)
        if sidecar.is_file():
            sidecar.rename(target.with_name(target.name + suffix))


def pack(
    project: str | Path,
    *,
    dest: str | Path | None = None,
    move: bool = True,
) -> dict:
    """把裸工程收成一个包。

    默认搬家（同一块盘上是改目录项，瞬时完成）；`move=False` 复制一份留着原工程。
    已经是包的话原样返回，不重复套一层。
    """
    current = layout(project)
    if current.package:
        return {"package": str(current.root), "moved": False, "note": "已经是工程包"}
    root = Path(dest) if dest else current.db.with_name(current.db.stem + PACKAGE_SUFFIX)
    if root.exists():
        raise ValueError(f"目标已存在：{root}")
    root.mkdir(parents=True)
    target_db = root / DB_NAME
    if move:
        _move_db(current.db, target_db)
    else:
        # 复制用 sqlite 的备份口，WAL 里的内容一并带过去。
        with contextlib.closing(sqlite3.connect(current.db)) as source:
            with contextlib.closing(sqlite3.connect(target_db)) as target:
                source.backup(target)
    packed: list[str] = []
    for name in PARTS:
        source = getattr(current, name)
        if not source.is_dir():
            continue
        target = root / name
        if move:
            source.rename(target)
        else:
            shutil.copytree(source, target)
        packed.append(name)
    meta = _meta(target_db)
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "title": meta["title"],
        "project_id": meta["project_id"],
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "parts": {name: name in packed for name in PARTS},
    }
    (root / MANIFEST_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "package": str(root),
        "db": str(target_db),
        "parts": packed,
        "moved": move,
        "title": payload["title"],
    }
