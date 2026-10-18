import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

SCHEMA_VERSION = 3


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, SCHEMA_VERSION):
                raise ValueError(f"不支持的数据库版本：{version}")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO meta VALUES ('revision','0');
                INSERT OR IGNORE INTO meta VALUES ('title','未命名作品');
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL,
                    hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(title,hash));
                CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assertions (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS changes (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    version INTEGER NOT NULL DEFAULT 1, result_revision INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS revisions (
                    revision INTEGER PRIMARY KEY, change_id TEXT, data TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
                    status TEXT NOT NULL, data TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs((1))
                    WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    start INTEGER NOT NULL, end INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', raw TEXT,
                    error TEXT, change_id TEXT, metadata TEXT);
                PRAGMA user_version=3;
            """)
            db.execute("INSERT OR IGNORE INTO meta VALUES ('project_id',?)", (uuid4().hex,))

    @contextmanager
    def connect(self, *, write: bool = False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @property
    def revision(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def rows(self, table: str) -> list[dict]:
        if table not in {
            "sources",
            "entities",
            "assertions",
            "events",
            "records",
            "changes",
            "revisions",
            "runs",
            "chunks",
        }:
            raise ValueError("未知数据表")
        with self.connect() as db:
            return [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]

    def objects(self, table: str) -> list[dict]:
        return [json.loads(r["data"]) for r in self.rows(table)]

    def source(self, source_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise ValueError("原文不存在")
        return dict(row)

    def backup(self, destination: str | Path) -> Path:
        target = Path(destination).resolve()
        if target == self.path or target.exists():
            raise ValueError("备份必须写入一个新的文件，避免覆盖已有资料")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("备份完整性检查失败")
        return target

    @staticmethod
    def restore(backup: str | Path, destination: str | Path) -> Path:
        source_path, target = Path(backup).resolve(), Path(destination).resolve()
        if not source_path.is_file() or target.exists():
            raise ValueError("备份不存在，或目标文件已存在；恢复请使用新文件")
        with closing(sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)) as source:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("备份损坏")
            if source.execute("PRAGMA user_version").fetchone()[0] not in (1, 2, SCHEMA_VERSION):
                raise ValueError("备份版本不兼容")
            if not source.execute("SELECT 1 FROM meta WHERE key='revision'").fetchone():
                raise ValueError("无效的项目备份")
            target.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(target)) as dest:
                source.backup(dest)
        return target
