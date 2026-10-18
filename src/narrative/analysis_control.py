"""Small persistent control mailbox for a running narrative agent."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")


def _path(operations, run_id: str) -> Path:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("分析任务 ID 无效")
    project = Path(operations.project_info().path)
    directory = project.parent / f"{project.stem}_analysis_runs" / run_id
    if not (directory / "journal.json").is_file():
        raise ValueError("分析任务不存在")
    return directory / "control.sqlite3"


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _initialize(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS control (
            id INTEGER PRIMARY KEY CHECK (id=1),
            paused INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS instructions (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            consumed_at TEXT
        );
        """
    )
    db.execute(
        "INSERT OR IGNORE INTO control(id,updated_at) VALUES (1,?)", (_stamp(),)
    )


def _state(db: sqlite3.Connection) -> dict:
    row = db.execute("SELECT paused,cancel_requested,updated_at FROM control WHERE id=1").fetchone()
    instructions = [
        {"seq": item[0], "id": item[1], "text": item[2], "created_at": item[3], "consumed_at": item[4]}
        for item in db.execute(
            "SELECT seq,id,text,created_at,consumed_at FROM instructions ORDER BY seq"
        )
    ]
    return {
        "paused": bool(row[0]),
        "cancel_requested": bool(row[1]),
        "updated_at": row[2],
        "pending_count": sum(item["consumed_at"] is None for item in instructions),
        "instructions": instructions,
    }


def read(operations, run_id: str) -> dict:
    path = _path(operations, run_id)
    if not path.is_file():
        return {"paused": False, "cancel_requested": False, "updated_at": None, "pending_count": 0, "instructions": []}
    with sqlite3.connect(path, timeout=10) as db:
        try:
            return _state(db)
        except sqlite3.OperationalError:
            return {"paused": False, "cancel_requested": False, "updated_at": None, "pending_count": 0, "instructions": []}


def set_paused(operations, run_id: str, paused: bool) -> dict:
    path = _path(operations, run_id)
    with sqlite3.connect(path, timeout=10) as db:
        _initialize(db)
        db.execute(
            "UPDATE control SET paused=?,updated_at=? WHERE id=1",
            (int(paused), _stamp()),
        )
        return _state(db)


def mark_cancel_requested(operations, run_id: str) -> dict:
    path = _path(operations, run_id)
    with sqlite3.connect(path, timeout=10) as db:
        _initialize(db)
        db.execute(
            "UPDATE control SET cancel_requested=1,paused=0,updated_at=? WHERE id=1",
            (_stamp(),),
        )
        return _state(db)


def send_instruction(operations, run_id: str, instruction: str) -> dict:
    if not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 4000:
        raise ValueError("补充指令须为 1–4000 字")
    path = _path(operations, run_id)
    item = {"id": uuid4().hex, "text": instruction.strip(), "created_at": _stamp()}
    with sqlite3.connect(path, timeout=10) as db:
        _initialize(db)
        db.execute(
            "INSERT INTO instructions(id,text,created_at) VALUES (?,?,?)",
            (item["id"], item["text"], item["created_at"]),
        )
        sequence = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
    return {**item, "seq": sequence}


def take_instructions(operations, run_id: str) -> list[dict]:
    path = _path(operations, run_id)
    with sqlite3.connect(path, timeout=10) as db:
        _initialize(db)
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        rows = list(db.execute(
            "SELECT seq,id,text,created_at FROM instructions WHERE consumed_at IS NULL ORDER BY seq"
        ))
        consumed_at = _stamp()
        db.executemany(
            "UPDATE instructions SET consumed_at=? WHERE seq=?",
            [(consumed_at, row[0]) for row in rows],
        )
    return [
        {"seq": row[0], "id": row[1], "text": row[2], "created_at": row[3], "consumed_at": consumed_at}
        for row in rows
    ]
