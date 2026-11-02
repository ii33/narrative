"""One resident Harness process per project, with task-scoped tool authority."""

from __future__ import annotations

import atexit
import hashlib
import os
import secrets
import threading
from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path

from .harness import CONVERSATION_SYSTEM_PROMPT, HarnessRunner
from .leases import ProjectLease
from .task_tool_host import TaskToolHost

_RUNTIMES: dict[str, ProjectConversation] = {}
_RUNTIMES_LOCK = threading.RLock()


def _project_path(ops_or_path) -> Path:
    if hasattr(ops_or_path, "project_info"):
        return Path(ops_or_path.project_info().path)
    return Path(ops_or_path)


def _project_key(ops_or_path) -> str:
    from .project_package import layout

    return os.path.normcase(str(layout(_project_path(ops_or_path)).db.resolve()))


def _catalog() -> list[dict]:
    from .assistant import tool_catalog
    # The runtime registry is stable. Real authority is selected by bind(),
    # never by the mere presence of a tool in this union catalog.
    tools = {row["name"]: row for row in tool_catalog()}
    return list(tools.values())


class ProjectConversation:
    def __init__(self, ops, settings, api_key: str, runner_factory,
                 *, restart_reason: str = "initial"):
        from .chat_store import ChatStore

        self.sdk_directory = ChatStore(_project_path(ops)).sdk_directory()
        self.sdk_directory.mkdir(parents=True, exist_ok=True)
        self.closed = False
        self.restart_reason = restart_reason
        self._restart_announced = False
        self._config = settings.model_dump()
        self._key_digest = hashlib.sha256(api_key.encode()).digest()
        self._factory = runner_factory
        self._binding = None
        self._lock = threading.RLock()
        self._resumed: set[str] = set()
        self._interrupt_done = threading.Event()
        self._interrupt_done.set()
        try:
            self._owner = ProjectLease(self.sdk_directory / "runtime-owner.lock")
        except OSError as exc:
            raise ValueError("该工程对话已被另一个进程占用；请先关闭该工程的其他窗口或进程") from exc
        self.host = None
        self.runner = None
        try:
            self.host = TaskToolHost(
                _catalog(), self._dispatch, authority=self._authority,
                redact=lambda value: value.replace(api_key, "[REDACTED]") if api_key else value,
            )
            self.runner = runner_factory(
                settings, api_key, self.sdk_directory, task_tools=self.host.config,
                system_prompt=CONVERSATION_SYSTEM_PROMPT,
            )
            self.runner.interrupt_barrier = self._interrupt_done
        except Exception:
            self.close("startup_failed")
            raise

    def compatible(self, settings, api_key: str, runner_factory) -> bool:
        return (
            not self.closed and settings.model_dump() == self._config
            and hashlib.sha256(api_key.encode()).digest() == self._key_digest
            and runner_factory is self._factory
        )

    def activate(self, chat_id: str, catalog: list[dict], dispatch, *, session_id=None) -> None:
        while True:
            self._interrupt_done.wait()
            with self._lock:
                if not self._interrupt_done.is_set():
                    continue
                if self.closed:
                    raise ValueError("工程对话运行时已关闭")
                if self._binding is not None:
                    raise ValueError("工程对话正在执行另一个任务")
                self._binding = {"chat_id": chat_id, "session_id": session_id or chat_id,
                                 "nonce": secrets.token_hex(16), "cancelled": False, "interrupt_sent": False,
                                 "allowed": {row["name"] for row in catalog},
                                 "dispatch": dispatch, "context": copy_context()}
                return

    def deactivate(self) -> None:
        with self._lock:
            self._binding = None

    @contextmanager
    def bind(self, chat_id: str, catalog: list[dict], dispatch, *, session_id=None):
        self.activate(chat_id, catalog, dispatch, session_id=session_id)
        try:
            yield self
        finally:
            self.deactivate()

    def _authority(self, session_id: str) -> str:
        with self._lock:
            binding = self._binding
            if self.closed or binding is None or binding["cancelled"] or binding["session_id"] != session_id:
                raise ValueError("当前工具调用不属于获授权的活动任务")
            return binding["nonce"]

    def begin_turn(self, nonce: str, session_id: str) -> None:
        with self._lock:
            binding = self._binding
            if (self.closed or binding is None or binding["nonce"] != nonce
                    or binding["cancelled"] or binding["session_id"] != session_id):
                raise ValueError("当前任务已停止，未启动模型调用")
            if self.runner is None:
                raise ValueError("工程运行时未就绪")
            # Arm before entering the runner. Stop either wins this lock and
            # rejects admission, or sees this exact turn and waits for Agent activity.
            self.runner.active_session = session_id

    def signal_stop(self, nonce: str) -> None:
        with self._lock:
            binding = self._binding
            if not self.closed and binding is not None and binding["nonce"] == nonce:
                binding["cancelled"] = True

    def healthy(self) -> bool:
        if self.closed or self.runner is None or getattr(self.runner, "_closed", False):
            return False
        harness = getattr(self.runner, "_harness", None)
        process = getattr(getattr(harness, "client", None), "_proc", None)
        return process is None or process.poll() is None

    def interrupt(self, nonce: str) -> None:
        with self._lock:
            binding = self._binding
            if self.closed or binding is None or binding["nonce"] != nonce or binding["interrupt_sent"]:
                return
            binding["cancelled"] = True
            binding["interrupt_sent"] = True
            runner, host = self.runner, self.host
            if runner is None or host is None or getattr(runner, "active_session", None) != binding["session_id"]:
                return
            self._interrupt_done.clear()
        # New admission waits for this exact command to settle, but close never
        # waits on this lock and can invalidate/wake an unresponsive controller.
        try:
            host.interrupt(binding["session_id"], nonce,
                           lambda: not self.closed and self._binding is binding
                           and getattr(runner, "active_session", None) == binding["session_id"])
            if hasattr(runner, "interrupt_upstream"):
                runner.interrupt_upstream()
        finally:
            self._interrupt_done.set()

    def _dispatch(self, name: str, args: dict, session_id=None, nonce=None):
        with self._lock:
            binding = self._binding
            if (self.closed or binding is None or binding["cancelled"]
                    or binding["session_id"] != session_id or binding["nonce"] != nonce):
                raise ValueError("当前没有获授权的工程任务或调用已过期")
            if name not in binding["allowed"]:
                raise ValueError(f"当前任务未授权工具 {name}")
            dispatch = binding["dispatch"]
            context = binding["context"].copy()
        return context.run(dispatch, name, args)

    def close(self, reason: str = "closed") -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
            self._interrupt_done.set()
            self.restart_reason = reason
            self._binding = None
            runner, host = self.runner, self.host
        try:
            if runner is not None:
                runner.close()
        finally:
            try:
                if host is not None:
                    host.close()
            finally:
                self._owner.close()


def get_runtime(ops, settings, api_key: str, runner_factory=HarnessRunner) -> ProjectConversation:
    key = _project_key(ops)
    with _RUNTIMES_LOCK:
        existing = _RUNTIMES.get(key)
        if existing is not None and existing.compatible(settings, api_key, runner_factory):
            return existing
        reason = "initial"
        if existing is not None:
            reason = existing.restart_reason if existing.closed else "configuration_changed"
            existing.close(reason)
        runtime = ProjectConversation(ops, settings, api_key, runner_factory, restart_reason=reason)
        _RUNTIMES[key] = runtime
        return runtime


def get_existing_runtime(ops_or_path) -> ProjectConversation | None:
    with _RUNTIMES_LOCK:
        return _RUNTIMES.get(_project_key(ops_or_path))


def close_project_runtime(ops_or_path, reason: str = "project_closed") -> None:
    with _RUNTIMES_LOCK:
        runtime = _RUNTIMES.get(_project_key(ops_or_path))
        if runtime is not None:
            runtime.close(reason)


def close_all_runtimes(reason: str = "process_exit") -> None:
    for runtime in list(_RUNTIMES.values()):
        try:
            runtime.close(reason)
        except Exception:
            pass


atexit.register(close_all_runtimes)
