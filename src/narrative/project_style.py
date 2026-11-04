"""Project-wide creative style agreement, separate from per-screenplay briefs."""
from __future__ import annotations

import json
from uuid import uuid4

from .graph_store import GraphStore

_KEY = "project_style_v1"


def read_style(db):
    row = db.execute("SELECT value FROM meta WHERE key=?", (_KEY,)).fetchone()
    return json.loads(row[0]) if row else {"revision": 0, "text": ""}


class ProjectStyleOperationsMixin:
    _store: GraphStore

    def load_project_style(self):
        with self._store.connect() as db:
            return read_style(db)

    def save_project_style(self, text, expected_revision, reason="编辑项目风格约定"):
        if not isinstance(text, str):
            raise ValueError("项目风格约定须为文本，可为空")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("请说明修改风格约定的原因")
        with self._store.connect(write=True) as db:
            previous = read_style(db)
            if type(expected_revision) is not int or expected_revision != previous["revision"]:
                raise ValueError("项目风格约定已变化，请刷新后重试")
            style = {"revision": previous["revision"] + 1, "text": text}
            db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (_KEY, json.dumps(style, ensure_ascii=False)))
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            db.execute("INSERT INTO actions(id,revision,action,reason,summary) VALUES (?,?,?,?,?)", (
                uuid4().hex, revision, "save_project_style", reason,
                json.dumps({"style_revision": style["revision"], "text_length": len(text)}, ensure_ascii=False)))
            return style
