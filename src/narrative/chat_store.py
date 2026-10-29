"""协作者的会话：一个工程一个目录，一次对话一个文件。

会话要留下来，因为一次对话里攒起来的东西（问过什么、查到了什么）比答案本身更值钱。
所以聊天记录落在工程旁边，不在数据库里：它不是图的一部分，也没有证据语义。

    <工程>_chats/<会话编号>.json

每份记录带一个 `session`，那是 harness 侧的会话号。重开窗口时用同一个号，
模型那边就能接上——我们存的是给人看的对话，`session` 才是给模型的记忆。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
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


class ChatStore:
    """一个工程的会话目录。没有缓存：读一次是一次的真实内容。"""

    def __init__(self, project: Path):
        from .project_package import layout

        self.project = Path(project)
        # 包和裸工程问同一处：包里的会话在 <包>/chats。
        self.root = layout(self.project).chats

    # -- 读写 ----------------------------------------------------------

    def path_for(self, chat_id: str) -> Path:
        return self.root / f"{chat_id}.json"

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
        data["updated_at"] = _now()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path_for(str(data["id"])).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return data

    # -- 会话 ----------------------------------------------------------

    def create(self, title: str = "") -> dict:
        chat_id = _new_id()
        return self._save({
            "id": chat_id,
            "session": f"chat-{chat_id}",
            "title": (title or "").strip() or "新会话",
            "created_at": _now(),
            "messages": [],
        })

    def list(self) -> list[dict]:
        """最近说过的排在前面；坏文件跳过，不让一个坏文件挡住整个列表。"""
        if not self.root.is_dir():
            return []
        rows: list[dict] = []
        for path in self.root.glob("*.json"):
            try:
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

    def rename(self, chat_id: str, title: str) -> dict:
        data = self.load(chat_id)
        data["title"] = (title or "").strip() or data["title"]
        return self._save(data)

    def delete(self, chat_id: str) -> None:
        path = self.path_for(chat_id)
        if path.is_file():
            path.unlink()

    # -- 消息 ----------------------------------------------------------

    def append(self, chat_id: str, role: str, text: str, **extra) -> dict:
        """追加一条消息；第一条用户消息顺手把会话标题起好。"""
        data = self.load(chat_id)
        message = {"role": role, "text": str(text or ""), "at": _now()}
        message.update({key: value for key, value in extra.items() if value not in (None, "", [])})
        data["messages"].append(message)
        if role == "user" and data["title"] in {"", "新会话"}:
            data["title"] = _title_from(text)
        return self._save(data)

    def latest(self) -> dict | None:
        rows = self.list()
        return self.load(rows[0]["id"]) if rows else None
