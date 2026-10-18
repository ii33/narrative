"""Isolated v4 graph database; never upgrades or mutates historical projects."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

GRAPH_SCHEMA_VERSION = 4


class GraphStore:
    def __init__(self, path: str | Path, *, create: bool = False):
        self.path = Path(path).resolve()
        if create:
            if self.path.exists():
                raise ValueError("项目已存在；新图项目须使用新文件名")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect(write=True) as db:
                db.executescript("""
                    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE sources (
                        id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL,
                        hash TEXT NOT NULL, imported_revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(title,hash));
                    CREATE TABLE objects (
                        id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL,
                        accepted_revision INTEGER NOT NULL);
                    CREATE INDEX objects_kind ON objects(kind);
                    CREATE INDEX assertions_subject ON objects(json_extract(data,'$.subject_id'))
                        WHERE kind='assertion';
                    CREATE INDEX assertions_object ON objects(json_extract(data,'$.object_id'))
                        WHERE kind='assertion';
                    CREATE INDEX assertions_holder ON objects(json_extract(data,'$.holder_id'))
                        WHERE kind='assertion';
                    CREATE INDEX assertions_predicate ON objects(json_extract(data,'$.predicate_id'))
                        WHERE kind='assertion';
                    CREATE TABLE evidence_links (
                        id TEXT PRIMARY KEY, assertion_id TEXT NOT NULL,
                        evidence_id TEXT NOT NULL, role TEXT NOT NULL,
                        accepted_revision INTEGER NOT NULL);
                    CREATE INDEX evidence_by_assertion ON evidence_links(assertion_id);
                    CREATE INDEX evidence_by_evidence ON evidence_links(evidence_id);
                    CREATE TABLE candidates (
                        id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL,
                        version INTEGER NOT NULL, expected_revision INTEGER NOT NULL,
                        result_revision INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE revisions (
                        revision INTEGER PRIMARY KEY, candidate_id TEXT, data TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                    PRAGMA user_version=4;
                """)
                db.executemany(
                    "INSERT INTO meta(key,value) VALUES (?,?)",
                    [
                        ("revision", "0"),
                        ("title", "未命名作品"),
                        ("project_id", uuid4().hex),
                    ],
                )
                # Canonical world and its story axis are infrastructure, not claims.
                for obj in (
                    {"kind": "context", "id": "canon", "label": "正篇", "context_type": "canon", "parent_id": None, "holder_id": None},
                    {"kind": "timeline", "id": "story", "context_id": "canon", "label": "故事时间", "calendar": None},
                ):
                    db.execute(
                        "INSERT INTO objects(id,kind,data,accepted_revision) VALUES (?,?,?,0)",
                        (obj["id"], obj["kind"], json.dumps(obj, ensure_ascii=False)),
                    )
        elif not self.path.is_file():
            raise ValueError("图项目不存在；请先创建新项目")
        # Probe with a read-only URI before any project connection can write.
        with sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != GRAPH_SCHEMA_VERSION:
                raise ValueError(f"图项目需要 v4 新库；此文件版本为 {version}，未修改历史库")
            if not db.execute("SELECT 1 FROM meta WHERE key='revision'").fetchone():
                raise ValueError("无效的图项目")

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

    def info(self) -> dict:
        with self.connect() as db:
            meta = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM meta")}
            count = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        return {
            "path": str(self.path),
            "project_id": meta["project_id"],
            "title": meta["title"],
            "revision": int(meta["revision"]),
            "source_count": count,
            "schema_version": GRAPH_SCHEMA_VERSION,
            "status": "ready",
        }

    @property
    def revision(self) -> int:
        return self.info()["revision"]

    def sources(self, *, include_text: bool = False) -> list[dict]:
        fields = "*" if include_text else "id,title,hash,created_at,length(text) AS length"
        with self.connect() as db:
            return [dict(row) for row in db.execute(f"SELECT {fields} FROM sources ORDER BY rowid")]

    def source(self, source_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise ValueError("原文不存在")
        return dict(row)

    def snapshot(self, as_of_revision: int | None = None) -> dict:
        with self.connect() as db:
            current = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            if as_of_revision is None:
                as_of_revision = current
            if as_of_revision < 0 or as_of_revision > current:
                raise ValueError("审核修订号超出范围")
            if as_of_revision == current:
                rows = list(db.execute("SELECT id,data,accepted_revision FROM objects"))
                objects = [json.loads(row["data"]) for row in rows]
                object_revisions = {row["id"]: row["accepted_revision"] for row in rows}
                links = [dict(row) | {"kind": "evidence_link"} for row in db.execute(
                    "SELECT id,assertion_id,evidence_id,role FROM evidence_links"
                )]
            else:
                state: dict[str, dict] = {}
                object_revisions: dict[str, int] = {}
                for row in db.execute(
                    "SELECT data FROM objects WHERE accepted_revision=0"
                ):
                    obj = json.loads(row[0])
                    state[obj["id"]] = obj
                    object_revisions[obj["id"]] = 0
                for row in db.execute(
                    "SELECT revision,data FROM revisions WHERE revision<=? ORDER BY revision",
                    (as_of_revision,),
                ):
                    for item in json.loads(row["data"]).get("operations", []):
                        if item["kind"] != "evidence_link":
                            state[item["id"]] = item
                            object_revisions[item["id"]] = row["revision"]
                        else:
                            state[item["id"]] = item
                objects = list(state.values())
                links = [o for o in objects if o["kind"] == "evidence_link"]
                objects = [o for o in objects if o["kind"] != "evidence_link"]
            sources = [dict(row) for row in db.execute(
                "SELECT id,title,hash,created_at,length(text) AS length FROM sources WHERE imported_revision<=?",
                (as_of_revision,),
            )]
        groups: dict[str, list[dict]] = {
            key: [] for key in ("nodes", "predicates", "assertions", "evidences", "contexts", "timelines", "source_orders")
        }
        for obj in objects:
            groups.setdefault(obj["kind"] + "s", []).append(obj)
        return {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "revision": as_of_revision,
            "current_revision": current,
            "object_revisions": object_revisions,
            "objects": objects,
            "evidence_links": links,
            "sources": sources,
            **groups,
        }
