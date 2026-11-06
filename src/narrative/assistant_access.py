"""Explicit chat configuration; task permissions are captured separately by the host."""
from typing import TYPE_CHECKING, Any

from .chat_store import ChatStore


def access_configuration(chat):
    return {"chat_id": chat["id"] if chat else None,
            "access_mode": chat["access_mode"] if chat else "standard",
            "access_revision": chat["access_revision"] if chat else 0}


class AssistantAccessOperationsMixin:
    if TYPE_CHECKING:
        def project_info(self) -> Any: ...

    def read_assistant_access(self, chat_id=None):
        """Read an explicitly selected chat; no chat means the standard default."""
        if chat_id is None:
            return access_configuration(None)
        return access_configuration(ChatStore(self.project_info().path).load(chat_id))

    def set_assistant_access(self, chat_id, access_mode, reason="修改助手访问模式"):
        """Save the next-task mode without touching any running task or SDK session."""
        store = ChatStore(self.project_info().path)
        return access_configuration(store.set_access_mode(chat_id, access_mode, reason))
