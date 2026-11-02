"""协作者的会话：一个工程一个目录，一次对话一个文件。

会话要留下来，因为一次对话里攒起来的东西（问过什么、查到了什么）比答案本身更值钱。
所以聊天记录落在工程旁边，不在数据库里：它不是图的一部分，也没有证据语义。

    <工程>_chats/<会话编号>.json

每份记录带一个 `session`，那是 harness 侧的会话号。重开窗口时用同一个号，
模型那边就能接上——我们存的是给人看的对话，`session` 才是给模型的记忆。
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4

MAX_TITLE = 24


def _now() -> str:
    # 毫秒：一秒内建两个会话时，「最近说过」还分得出先后。
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _new_id() -> str:
    return uuid4().hex[:12]


def _title_from(text: str) -> str:
    """第一条消息就是标题：取前一段，去掉换行。"""
    line = " ".join(str(text or "").split())
    if not line:
        return "新会话"
    return line[:MAX_TITLE] + ("…" if len(line) > MAX_TITLE else "")


_CHAT_LOCKS: dict[str, threading.RLock] = {}
_CHAT_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path):
    # Resolve the directory, not the replace target: on Windows resolving an
    # existing file itself briefly opens it before we can acquire this lock.
    key = os.path.normcase(str(path.parent.resolve() / path.name))
    with _CHAT_LOCKS_GUARD:
        return _CHAT_LOCKS.setdefault(key, threading.RLock())


def _locked_chat(method):
    @wraps(method)
    def locked(self, chat_id, *args, **kwargs):
        with _path_lock(self.path_for(chat_id)):
            return method(self, chat_id, *args, **kwargs)
    return locked


def _locked_active(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        # When nested, always acquire pointer before chat. Message mutations
        # never access the pointer while holding their chat lock.
        with _path_lock(self.root / ".active"):
            return method(self, *args, **kwargs)
    return locked


class ChatStore:
    """一个工程的会话目录。没有缓存：读一次是一次的真实内容。"""

    def __init__(self, project: Path):
        from .project_package import layout

        self.project = Path(project)
        # 包和裸工程问同一处：包里的会话在 <包>/chats。
        self.root = layout(self.project).chats

    # -- 读写 ----------------------------------------------------------

    def path_for(self, chat_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(chat_id)):
            raise ValueError("会话编号无效")
        return self.root / f"{chat_id}.json"

    @_locked_chat
    def load(self, chat_id: str) -> dict:
        path = self.path_for(chat_id)
        if not path.is_file():
            raise ValueError(f"会话不存在：{chat_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"会话文件损坏：{chat_id}")
        data.setdefault("id", chat_id)
        data.setdefault("session", f"chat-{chat_id}")
        data.setdefault("title", "新会话")
        data["messages"] = data.get("messages") if isinstance(data.get("messages"), list) else []
        return data

    def _save(self, data: dict) -> dict:
        with _path_lock(self.path_for(str(data["id"]))):
            data["updated_at"] = _now()
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.path_for(str(data["id"]))
            temporary = path.with_suffix(f".{uuid4().hex}.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
            return data

    @_locked_active
    def set_active(self, chat_id: str) -> dict:
        chat = self.load(chat_id)
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / ".active"
        temporary = self.root / f".active-{uuid4().hex}.tmp"
        temporary.write_text(json.dumps({"chat_id": chat["id"]}), encoding="utf-8")
        temporary.replace(target)
        return chat

    @_locked_active
    def active(self, *, create: bool = False) -> dict | None:
        try:
            pointer = json.loads((self.root / ".active").read_text(encoding="utf-8"))
            return self.load(str(pointer["chat_id"]))
        except (FileNotFoundError, ValueError, TypeError, KeyError):
            chat = self.latest()
            if chat is not None:
                return self.set_active(chat["id"])
            return self.create() if create else None

    def select(self, chat_id: str | None = None, *, create: bool = True,
               activate: bool = True) -> dict | None:
        if chat_id:
            return self.set_active(chat_id) if activate else self.load(chat_id)
        return self.active(create=create)

    def sdk_directory(self) -> Path:
        from .project_package import layout

        # Preserve the assistant's real SDK home and cwd, including existing
        # chat session contexts. Extraction history in conversation/ is untouched.
        return layout(self.project).extraction / "assistant"

    # -- 会话 ----------------------------------------------------------

    def create(self, title: str = "") -> dict:
        chat_id = _new_id()
        chat = self._save({
            "id": chat_id,
            "session": f"chat-{chat_id}",
            "title": (title or "").strip() or "新会话",
            "created_at": _now(),
            "messages": [],
        })
        self.set_active(chat_id)
        return chat

    def list(self) -> list[dict]:
        """最近说过的排在前面；坏文件跳过，不让一个坏文件挡住整个列表。"""
        if not self.root.is_dir():
            return []
        rows: list[dict] = []
        for path in self.root.glob("*.json"):
            try:
                with _path_lock(path):
                    data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            raw = data.get("messages")
            messages: list = raw if isinstance(raw, list) else []
            rows.append({
                "id": str(data.get("id") or path.stem),
                "session": str(data.get("session") or f"chat-{path.stem}"),
                "title": str(data.get("title") or "新会话"),
                "updated_at": str(data.get("updated_at") or ""),
                "created_at": str(data.get("created_at") or ""),
                "message_count": len(messages),
            })
        # 时间戳相同（同一毫秒）时用 id 兜底，列表顺序才不会跟着文件枚举顺序变。
        rows.sort(key=lambda row: (row["updated_at"], row["created_at"], row["id"]), reverse=True)
        return rows

    @_locked_chat
    def rename(self, chat_id: str, title: str) -> dict:
        data = self.load(chat_id)
        data["title"] = (title or "").strip() or data["title"]
        return self._save(data)

    @_locked_chat
    def delete(self, chat_id: str) -> None:
        path = self.path_for(chat_id)
        if path.is_file():
            path.unlink()

    # -- 消息 ----------------------------------------------------------

    @_locked_chat
    def append(self, chat_id: str, role: str, text: str, **extra) -> dict:
        """追加一条消息；第一条用户消息顺手把会话标题起好。"""
        data = self.load(chat_id)
        message = {"role": role, "text": str(text or ""), "at": _now()}
        message.update({key: value for key, value in extra.items() if value not in (None, "", [])})
        data["messages"].append(message)
        if role == "user" and data["title"] in {"", "新会话"}:
            data["title"] = _title_from(text)
        return self._save(data)

    @_locked_chat
    def annotate_message(self, chat_id: str, message_id: str, **extra) -> dict:
        """Attach completed task metadata to an already streamed model message."""
        data = self.load(chat_id)
        for message in data["messages"]:
            if message.get("message_id") == message_id:
                message.update(extra)
                return self._save(data)
        raise ValueError("模型消息不存在")

    def latest(self) -> dict | None:
        rows = self.list()
        return self.load(rows[0]["id"]) if rows else None
