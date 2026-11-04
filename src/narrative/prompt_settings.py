"""Editable project-owned plain-text prompt fragments and immutable task snapshots."""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from .graph_store import GraphStore

_KEY = "project_prompt_overrides_v1"
_BOUND: ContextVar[tuple[str, dict] | None] = ContextVar("project_prompt_snapshot", default=None)


@contextmanager
def bind_prompt_snapshot(ops, snapshot):
    token = _BOUND.set((ops.graph_project_info()["project_id"], copy.deepcopy(snapshot)))
    try:
        yield
    finally:
        _BOUND.reset(token)


def _bound(project_id):
    current = _BOUND.get()
    return copy.deepcopy(current[1]) if current is not None and current[0] == project_id else None


def _defaults():
    from .prompt_defaults import DEFAULT_PROMPTS
    return DEFAULT_PROMPTS


def _load(db):
    row = db.execute("SELECT value FROM meta WHERE key=?", (_KEY,)).fetchone()
    return json.loads(row[0]) if row else {"revision": 0, "overrides": {}}


def _view(state):
    prompts = []
    for identifier, entry in _defaults().items():
        overridden = identifier in state["overrides"]
        version = entry["version"] + ("/project-" + str(state["revision"]) if overridden else "")
        prompts.append({"id": identifier, "label": entry["label"], "description": entry["description"],
            "version": version, "default": entry["text"], "effective": state["overrides"].get(identifier, entry["text"]),
            "overridden": overridden})
    return {"revision": state["revision"], "prompts": prompts,
            "effective_versions": {row["id"]: row["version"] for row in prompts}}


def effective_prompt(ops, identifier):
    return prompt_snapshot(ops)["texts"][identifier]


def snapshot_from_db(db):
    row = db.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()
    frozen = _bound(row[0]) if row else None
    return frozen if frozen is not None else snapshot_from_settings(_view(_load(db)))


def snapshot_from_settings(settings):
    return {"revision": settings["revision"], "versions": settings["effective_versions"],
            "texts": {row["id"]: row["effective"] for row in settings["prompts"]}}


def prompt_snapshot(ops):
    frozen = _bound(ops.graph_project_info()["project_id"])
    return frozen if frozen is not None else snapshot_from_settings(ops.load_prompt_settings())


class PromptSettingsOperationsMixin:
    _store: GraphStore

    def load_prompt_settings(self):
        with self._store.connect() as db:
            return _view(_load(db))

    def save_prompt_override(self, prompt_id, text, expected_revision, reason="编辑工程提示词"):
        if prompt_id not in _defaults():
            raise ValueError("提示词项目不存在")
        if text is not None and not isinstance(text, str):
            raise ValueError("提示词须为文本（可为空）；恢复默认请使用null")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("请说明修改提示词的原因")
        with self._store.connect(write=True) as db:
            state = _load(db)
            if type(expected_revision) is not int or expected_revision != state["revision"]:
                raise ValueError("提示词设置已变化，请刷新后重试")
            if text is None:
                state["overrides"].pop(prompt_id, None)
            else:
                state["overrides"][prompt_id] = text
            state["revision"] += 1
            db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (_KEY, json.dumps(state, ensure_ascii=False)))
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            summary = {"prompt_id": prompt_id, "prompt_revision": state["revision"], "restored_default": text is None}
            db.execute("INSERT INTO actions(id,revision,action,reason,summary) VALUES (?,?,?,?,?)",
                       (uuid4().hex, revision, "save_prompt_override", reason, json.dumps(summary, ensure_ascii=False)))
        return _view(state)
