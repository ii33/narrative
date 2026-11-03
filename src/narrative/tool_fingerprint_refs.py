"""Readable transport references for program-owned screenplay fingerprints.

Operations still receive and compare the original digests. Only shared tool I/O
uses these project-bound handles; the registry is a chat-sidecar cache, never a
business table, node alias, or revision history.
"""
from __future__ import annotations

import base64
import copy
import re
import secrets
import sqlite3
import warnings
from contextlib import contextmanager
from typing import Any

_FIELD_KINDS = {
    "span_fingerprint": "span-basis",
    "fingerprint": "span-basis",
    "context_fingerprint": "context-basis",
    "plan_fingerprint": "plan-basis",
    "expected_storage_fingerprint": "storage-snapshot",
    "previous_storage_fingerprint": "storage-snapshot",
}
# These objects are user content, rather than fingerprint-bearing transport
# metadata. Never reinterpret a literal digest or an identically named user key.
_OPAQUE_FIELDS = {"text", "raw", "prompt", "instruction", "creative_brief", "brief", "payload",
                  "value", "plan", "story_time", "continuity_requirements", "config"}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_NAMESPACE = re.compile(r"[a-z2-7]{12}\Z")
_HANDLE = re.compile(r"(span-basis|context-basis|plan-basis|storage-snapshot)-([1-9][0-9]*)@([a-z2-7]{12})\Z")


class _Registry:
    def __init__(self, ops):
        from .project_package import layout

        self.project_id = str(ops.graph_project_info()["project_id"])
        self.path = layout(ops.project_info().path).chats / "tool-fingerprint-refs.sqlite3"

    @contextmanager
    def write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS refs(kind TEXT NOT NULL,value TEXT NOT NULL,number INTEGER NOT NULL,PRIMARY KEY(kind,value),UNIQUE(kind,number))")
            db.execute("CREATE TABLE IF NOT EXISTS sequences(kind TEXT PRIMARY KEY,next_number INTEGER NOT NULL)")
            owner = db.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()
            if owner is None:
                namespace = base64.b32encode(secrets.token_bytes(8)).decode("ascii").lower()[:12]
                db.execute("INSERT INTO meta VALUES('project_id',?)", (self.project_id,))
                db.execute("INSERT INTO meta VALUES('namespace',?)", (namespace,))
            namespace = self._namespace(db)
            yield db, namespace
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _namespace(self, db):
        rows = dict(db.execute("SELECT key,value FROM meta"))
        if rows.get("project_id") != self.project_id:
            raise ValueError("工具依据缓存不属于当前工程，请重新读取当前工程的上下文")
        namespace = rows.get("namespace", "")
        if not _NAMESPACE.fullmatch(namespace):
            raise ValueError("工具依据缓存损坏，无法解析依据编号")
        return namespace

    def encode(self, values):
        result = {}
        with self.write() as (db, namespace):
            for kind, value in values:
                row = db.execute("SELECT number FROM refs WHERE kind=? AND value=?", (kind, value)).fetchone()
                if row is None:
                    counter = db.execute("SELECT next_number FROM sequences WHERE kind=?", (kind,)).fetchone()
                    number = counter[0] if counter else 1
                    db.execute("INSERT INTO refs VALUES(?,?,?)", (kind, value, number))
                    db.execute("INSERT INTO sequences VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET next_number=excluded.next_number", (kind, number + 1))
                else:
                    number = row[0]
                result[kind, value] = f"{kind}-{number}@{namespace}"
        return result

    def decode(self, values):
        if not self.path.is_file():
            raise ValueError("依据编号无效或缓存已移除，请重新读取对应上下文")
        db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        try:
            namespace = self._namespace(db)
            result = {}
            for kind, value in values:
                match = _HANDLE.fullmatch(value)
                if match is None or match[1] != kind or match[3] != namespace:
                    raise ValueError("依据编号无效或不属于当前工程，请重新读取对应上下文")
                row = db.execute("SELECT value FROM refs WHERE kind=? AND number=?", (kind, int(match[2]))).fetchone()
                if row is None:
                    raise ValueError("依据编号不存在，请重新读取对应上下文")
                result[kind, value] = row[0]
            return result
        finally:
            db.close()


def _collect(value: Any, *, decoding: bool, targets: set[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _OPAQUE_FIELDS:
                continue
            kind = _FIELD_KINDS.get(key)
            if kind is not None and item is not None:
                if not isinstance(item, str):
                    raise ValueError(f"{key}须为程序返回的依据编号")
                if _DIGEST.fullmatch(item):
                    if not decoding:
                        targets.add((kind, item))
                elif decoding:
                    if not _HANDLE.fullmatch(item):
                        raise ValueError(f"{key}依据编号无效，请重新读取对应上下文")
                    targets.add((kind, item))
            else:
                _collect(item, decoding=decoding, targets=targets)
    elif isinstance(value, list):
        for item in value:
            _collect(item, decoding=decoding, targets=targets)


def _replace(value: Any, replacements: dict[tuple[str, str], str]) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in _OPAQUE_FIELDS:
                result[key] = item
                continue
            kind = _FIELD_KINDS.get(key)
            if kind is not None and isinstance(item, str):
                result[key] = replacements.get((kind, item), item)
            else:
                result[key] = _replace(item, replacements)
        return result
    if isinstance(value, list):
        return [_replace(item, replacements) for item in value]
    return value


def encode_tool_result(ops, result: Any) -> Any:
    """Return public handles in known metadata keys; leave text and artifacts exact."""
    targets: set[tuple[str, str]] = set()
    _collect(result, decoding=False, targets=targets)
    if not targets:
        return result
    try:
        replacements = _Registry(ops).encode(sorted(targets))
    except (sqlite3.Error, OSError, ValueError):
        message = "业务操作已完成；工具依据编号缓存不可用，已保留原始校验值，后续仍按原值校验。"
        diagnostic = {"severity": "warning", "code": "TOOL_FINGERPRINT_CACHE_UNAVAILABLE", "message": message}
        if isinstance(result, dict):
            previous = result.get("transport_diagnostics")
            return result | {"transport_diagnostics": (previous if isinstance(previous, list) else []) + [diagnostic]}
        warnings.warn(message, RuntimeWarning, stacklevel=2)
        return result
    return _replace(result, replacements)


def decode_tool_parameters(ops, args: dict) -> dict:
    """Resolve an argument copy without rereading or refreshing its old basis.

    Programmatic callers may supply Operations' exact raw digest. A non-digest
    value must be a known, correctly typed reference owned by this project.
    """
    args = copy.deepcopy(args)
    targets: set[tuple[str, str]] = set()
    _collect(args, decoding=True, targets=targets)
    if not targets:
        return args
    try:
        replacements = _Registry(ops).decode(sorted(targets))
    except sqlite3.Error as exc:
        raise ValueError("无法读取工具依据编号缓存，请重新读取对应上下文") from exc
    return _replace(args, replacements)
