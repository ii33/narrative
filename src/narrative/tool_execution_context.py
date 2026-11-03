"""Task-local program provenance, separate from model-supplied tool arguments."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar

_METADATA: ContextVar[dict[str, str] | None] = ContextVar("tool_execution_metadata", default=None)
_KEYS = {"task_id", "manifest_ref"}
_SCENE_TABLE: ContextVar[bool] = ContextVar("scene_table_generation", default=False)


@contextmanager
def bind_scene_table_generation():
    token = _SCENE_TABLE.set(True)
    try:
        yield
    finally:
        _SCENE_TABLE.reset(token)


def is_scene_table_generation() -> bool:
    return _SCENE_TABLE.get()


@contextmanager
def bind_tool_execution_metadata(metadata: Mapping[str, str]):
    """Bind one invocation's immutable metadata and always restore its caller."""
    if set(metadata) != _KEYS or any(not isinstance(value, str) or not value.strip() for value in metadata.values()):
        raise ValueError("工具任务元数据须明确包含task_id与manifest_ref")
    token = _METADATA.set(dict(metadata))
    try:
        yield
    finally:
        _METADATA.reset(token)


def current_tool_execution_metadata() -> dict[str, str]:
    """Return a copy; a handler cannot mutate another invocation's provenance."""
    return dict(_METADATA.get() or {})
