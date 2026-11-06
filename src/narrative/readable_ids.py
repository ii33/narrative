"""Transaction-local allocation of stable readable business identities.

IDs express type and identity only. They never define reading or display order.
Counters live in the caller's existing meta table and roll back with its write.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from sqlite3 import Connection

from .content_errors import ContentConflictError


def allocate_id(db: Connection, namespace: str, prefix: str, existing_ids: Iterable[str] = ()) -> str:
    """Return ``prefix + integer`` without reusing successfully allocated IDs.

    The caller must already hold a write transaction. Project and asset stores
    both expose ``meta(key,value)``; SQLite text/integer affinity is supported.
    Existing readable IDs bootstrap the high-water mark without rewriting them.
    """
    if not db.in_transaction:
        raise ContentConflictError("可读业务ID必须在现有写事务中自动分配")
    if not isinstance(namespace, str) or not namespace or namespace != namespace.strip():
        raise ValueError("ID计数命名空间不能为空")
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", prefix):
        raise ValueError("ID前缀须为可读类型名")
    key = "readable_id:" + namespace + ":" + prefix
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    try:
        previous = int(row[0]) if row else 0
    except (ValueError, TypeError) as exc:
        raise ContentConflictError("可读ID计数器无效，未重置或复用编号") from exc
    if previous < 0:
        raise ContentConflictError("可读ID计数器无效，未重置或复用编号")
    pattern = re.compile(re.escape(prefix) + r"([1-9][0-9]*)\Z")
    taken = set(existing_ids)
    highest = previous
    for identifier in taken:
        match = pattern.fullmatch(identifier) if isinstance(identifier, str) else None
        if match:
            highest = max(highest, int(match.group(1)))
    identifier = prefix + str(highest + 1)
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, highest + 1))
    return identifier


def reserve_id(db: Connection, namespace: str, prefix: str, identifier: str) -> str:
    """Retain an explicit numeric ID's high-water mark without allocating a slot.

    Non-numeric custom identities remain literal identities and consume no
    numeric sequence. The reservation rolls back with the caller's transaction.
    """
    if not db.in_transaction:
        raise ContentConflictError("可读业务ID必须在现有写事务中自动分配")
    if not isinstance(namespace, str) or not namespace or namespace != namespace.strip():
        raise ValueError("ID计数命名空间不能为空")
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", prefix):
        raise ValueError("ID前缀须为可读类型名")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("显式业务ID不能为空")
    match = re.fullmatch(re.escape(prefix) + r"([1-9][0-9]*)", identifier)
    if match is None:
        return identifier
    key = "readable_id:" + namespace + ":" + prefix
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    try:
        previous = int(row[0]) if row else 0
    except (ValueError, TypeError) as exc:
        raise ContentConflictError("可读ID计数器无效，未重置或复用编号") from exc
    if previous < 0:
        raise ContentConflictError("可读ID计数器无效，未重置或复用编号")
    if int(match[1]) > previous:
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, int(match[1])))
    return identifier
