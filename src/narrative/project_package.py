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
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PACKAGE_SUFFIX = ".nproj"
DB_NAME = "graph.sqlite3"
MANIFEST_NAME = "manifest.json"
FORMAT = "narrative-project"
VERSION = 1
# 包里只装「这份作品」：库和资料。提取缓存能重跑，会话是对话记录，都放在包旁边。
PARTS = ("material",)


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
        if target.suffix.lower() == PACKAGE_SUFFIX or (target / MANIFEST_NAME).is_file():
            return _package_layout(target)
        if (target / DB_NAME).is_file():
            return _package_layout(target)
        if not any(target.iterdir()):
            # 空目录：当成还没建库的包。
            return _package_layout(target)
        raise ValueError(f"这个目录不是工程包：{target}")
    if target.name == DB_NAME and (target.parent / MANIFEST_NAME).is_file():
        return _package_layout(target.parent)
    if target.suffix.lower() == PACKAGE_SUFFIX:
        # 还不存在的包目录：建库时再创建。
        return _package_layout(target)
    if target.suffix.lower() == ".sqlite3":
        raise ValueError("旧版单文件工程已不再支持：请把它放进 <名字>.nproj/ 再打开")
    raise ValueError(f"工程是一个 .nproj 目录，这个路径不是：{target}")


def write_manifest(
    root: str | Path,
    *,
    title: str,
    project_id: str,
    parts: dict | None = None,
) -> dict:
    """写 manifest。新建工程时直接落在包目录里，不用先建裸库再搬家。"""
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "title": title,
        "project_id": project_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "parts": parts or {name: False for name in PARTS},
    }
    Path(root, MANIFEST_NAME).write_text(
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
        "parts": {name: (layout_.root / name).is_dir() for name in PARTS},
    }
