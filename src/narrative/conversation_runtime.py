"""One resident Harness process per project, with task-scoped tool authority."""

from __future__ import annotations

import atexit
import copy
import hashlib
import os
import secrets
import threading
from contextlib import contextmanager
from contextvars import copy_context
from datetime import UTC, datetime
from pathlib import Path

from .conversation_steering import SteeringLedger, pack_instruction
from .harness import HarnessRunner
from .leases import ProjectLease
from .prompt_settings import prompt_snapshot
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
    tools = {row["name"]: row for row in tool_catalog(access_mode="full")}
    return list(tools.values())


class ProjectConversation:
    def __init__(self, ops, settings, api_key: str, runner_factory,
                 *, restart_reason: str = "initial", prompt_config=None):
        from .chat_store import ChatStore

        self.project_path = _project_path(ops)
        self.sdk_directory = ChatStore(_project_path(ops)).sdk_directory()
        self.sdk_directory.mkdir(parents=True, exist_ok=True)
        self.closed = False
        self.restart_reason = restart_reason
        self._restart_announced = False
        self._config = settings.model_dump()
        self._prompt_config = copy.deepcopy(prompt_config) if prompt_config is not None else prompt_snapshot(ops)
        self._key_digest = hashlib.sha256(api_key.encode()).digest()
        self._factory = runner_factory
        self._binding = None
        self._visible_by_chat: dict[str, list[str]] = {}
        self._steering = SteeringLedger()
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
                system_prompt=self._prompt_config["texts"]["system"],
            )
            self.runner.interrupt_barrier = self._interrupt_done
            if hasattr(self.runner, "set_tool_allowlist"):
                self.runner.set_tool_allowlist(self._model_tool_allowlist)
            if hasattr(self.runner, "set_steering_listener"):
                self.runner.set_steering_listener(self._steering_delivered)
        except Exception:
            self.close("startup_failed")
            raise

    def compatible(self, settings, api_key: str, runner_factory, prompt_config=None) -> bool:
        return (
            not self.closed and settings.model_dump() == self._config
            and hashlib.sha256(api_key.encode()).digest() == self._key_digest
            and runner_factory is self._factory
            and (prompt_config is None or prompt_config == self._prompt_config)
        )

    def activate(self, chat_id: str, catalog: list[dict], dispatch, *, session_id=None, access_mode="standard", task_id=None, cancel_event=None, secrets_to_redact=()) -> None:
        from .assistant import tool_catalog
        from .tool_loading import initial_tool_names
        if access_mode not in {"standard", "full"}:
            raise ValueError("访问模式须为standard或full")
        mode_allowed = {row["name"] for row in tool_catalog(access_mode=access_mode)}
        while True:
            self._interrupt_done.wait()
            with self._lock:
                if not self._interrupt_done.is_set():
                    continue
                if self.closed:
                    raise ValueError("工程对话运行时已关闭")
                if self._binding is not None:
                    raise ValueError("工程对话正在执行另一个任务")
                allowed = {row["name"] for row in catalog} & mode_allowed
                core = initial_tool_names([row["name"] for row in catalog if row["name"] in allowed])
                visible = core + [name for name in self._visible_by_chat.get(chat_id, []) if name in allowed and name not in core]
                self._visible_by_chat[chat_id] = visible
                task_id = task_id or f"host-task-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
                self._binding = {"chat_id": chat_id, "session_id": session_id or chat_id,
                                 "nonce": secrets.token_hex(16), "cancelled": False, "interrupt_sent": False,
                                 "allowed": allowed, "visible": list(visible),
                                 "access_mode": access_mode, "task_id": task_id, "accept_steering": True,
                                 "cancel_event": cancel_event if cancel_event is not None else threading.Event(),
                                 "secrets_to_redact": tuple(secrets_to_redact), "calls_active": 0, "calls_settled": threading.Event(),
                                 "dispatch": dispatch, "context": copy_context()}
                self._binding["calls_settled"].set()
                return

    def deactivate(self) -> None:
        with self._lock:
            binding = self._binding
            if binding:
                binding["accept_steering"] = False
        if binding:
            self._settle_steering(binding)
            binding["calls_settled"].wait()
        with self._lock:
            if self._binding is binding:
                self._binding = None
        if binding and binding.get("task_id"):
            from .assistant_host import finish_host_commands
            finish_host_commands(self.project_path, binding["task_id"], completed=False)

    def _steering_delivered(self, session_id, message_id):
        with self._lock:
            binding = self._binding
            same = binding and binding["session_id"] == session_id
        if same:
            self._steering.update(message_id, "delivered")

    def _settle_steering(self, binding):
        pending = self._steering.pending(binding["task_id"])
        if not pending:
            return
        try:
            if self.host is not None and not self.closed:
                self.host.remove_steering(binding["session_id"], binding["nonce"], pending,
                    lambda: not self.closed and self._binding is binding)
        except Exception:
            self.close("steering_cleanup_failed")
            raise
        finally:
            for identifier in pending:
                self._steering.update(identifier, "not_delivered", "任务已结束或停止，插话未进入本次模型上下文")

    def steer(self, chat_id, task_id, nonce, content_blocks, *, message_id=None, on_status=None):
        import re
        blocks = pack_instruction(content_blocks)
        identifier = message_id or f"steer-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", identifier):
            raise ValueError("插话消息编号无效")
        with self._lock:
            binding = self._binding
            if (self.closed or not binding or binding["cancelled"] or not binding["accept_steering"]
                    or binding["chat_id"] != chat_id or binding["task_id"] != task_id or binding["nonce"] != nonce
                    or self.runner is None or self.host is None
                    or getattr(self.runner, "active_session", None) != binding["session_id"]
                    or getattr(self.runner, "turn_settled", True)):
                raise ValueError("该聊天任务已结束或停止，未发送插话")
            host = self.host
            self._steering.add(identifier, chat_id, task_id, on_status)
        def active():
            return (not self.closed and self._binding is binding and not binding["cancelled"]
                    and binding["accept_steering"] and getattr(self.runner, "active_session", None) == binding["session_id"])
        try:
            result = host.steer(binding["session_id"], nonce, identifier, blocks, active)
            if not result.get("accepted"):
                raise ValueError("任务已结束，插话未送达")
            self._steering.update(identifier, "queued")
            return {"message_id": identifier, "chat_id": chat_id, "task_id": task_id, "status": "queued"}
        except Exception as exc:
            self._steering.update(identifier, "not_delivered", str(exc))
            raise

    @contextmanager
    def bind(self, chat_id: str, catalog: list[dict], dispatch, *, session_id=None, access_mode="standard", task_id=None, cancel_event=None, secrets_to_redact=()):
        self.activate(chat_id, catalog, dispatch, session_id=session_id, access_mode=access_mode, task_id=task_id, cancel_event=cancel_event, secrets_to_redact=secrets_to_redact)
        try:
            yield self
        finally:
            self.deactivate()

    def _model_tool_allowlist(self):
        with self._lock:
            binding = self._binding
            return tuple(name for name in binding["visible"] if name in binding["allowed"]) if binding and not binding["cancelled"] and not self.closed else ()

    def _visible_tools(self, binding):
        with self._lock:
            return tuple(name for name in binding["visible"] if name in binding["allowed"]) if self._binding is binding and not binding["cancelled"] and not self.closed else ()

    def _load_tools(self, binding, requested):
        with self._lock:
            if self.closed or self._binding is not binding or binding["cancelled"]:
                raise ValueError("工具加载不属于当前活动任务")
            names = set(requested)
            if not names <= binding["allowed"]:
                raise ValueError("不能加载当前任务未授权的工具")
            for name in requested:
                if name not in binding["visible"]:
                    binding["visible"].append(name)
            self._visible_by_chat[binding["chat_id"]] = list(binding["visible"])
            return tuple(name for name in binding["visible"] if name in binding["allowed"])

    def _authority(self, session_id: str) -> str:
        with self._lock:
            binding = self._binding
            if self.closed or binding is None or binding["cancelled"] or binding["session_id"] != session_id:
                raise ValueError("当前工具调用不属于获授权的活动任务")
            return binding["nonce"]

    def begin_turn(self, nonce: str, session_id: str, *, task_id=None) -> None:
        with self._lock:
            binding = self._binding
            if (self.closed or binding is None or binding["nonce"] != nonce
                    or binding["cancelled"] or binding["session_id"] != session_id):
                raise ValueError("当前任务已停止，未启动模型调用")
            if self.runner is None:
                raise ValueError("工程运行时未就绪")
            # Arm before entering the runner. Stop either wins this lock and
            # rejects admission, or sees this exact turn and waits for Agent activity.
            if task_id is not None:
                binding["business_task_id"] = task_id
            self.runner.active_session = session_id
            self.runner.turn_settled = False

    def signal_stop(self, nonce: str) -> None:
        with self._lock:
            binding = self._binding
            if not self.closed and binding is not None and binding["nonce"] == nonce:
                binding["cancelled"] = True
                binding["cancel_event"].set()

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
            binding["cancel_event"].set()
            binding["interrupt_sent"] = True
            runner, host = self.runner, self.host
            if runner is None or host is None or getattr(runner, "active_session", None) != binding["session_id"] or getattr(runner, "turn_settled", False):
                return
            self._interrupt_done.clear()
        # New admission waits for this exact command to settle, but close never
        # waits on this lock and can invalidate/wake an unresponsive controller.
        try:
            host.interrupt(binding["session_id"], nonce,
                           lambda: not self.closed and self._binding is binding
                           and getattr(runner, "active_session", None) == binding["session_id"]
                           and not getattr(runner, "turn_settled", False))
        finally:
            try:
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
            binding["calls_active"] += 1
            binding["calls_settled"].clear()
        def authorized_dispatch():
            from .assistant_host import host_access_context
            from .tool_loading import bind_tool_loader
            with host_access_context(binding["access_mode"], binding["task_id"], binding["cancel_event"], self.project_path,
                                     secrets_to_redact=binding["secrets_to_redact"]), bind_tool_loader(
                    lambda requested: self._load_tools(binding, requested),
                    visible_names=lambda: self._visible_tools(binding), allowed_names=binding["allowed"],
                    access_mode=binding["access_mode"]):
                return dispatch(name, args)
        try:
            return context.run(authorized_dispatch)
        finally:
            with self._lock:
                binding["calls_active"] -= 1
                if not binding["calls_active"]:
                    binding["calls_settled"].set()

    def close(self, reason: str = "closed") -> None:
        with self._lock:
            already_closed = self.closed
            binding = None if already_closed else self._binding
            if not already_closed:
                self.closed = True
                self._interrupt_done.set()
                self.restart_reason = reason
                if binding is not None:
                    binding["cancel_event"].set()
                self._binding = None
            runner, host = self.runner, self.host
        if reason in {"project_closed", "process_exit"}:
            from .assistant_host import cancel_project_host_commands
            cancel_project_host_commands(self.project_path)
        if already_closed:
            return
        if binding:
            for identifier in self._steering.pending(binding["task_id"]):
                self._steering.update(identifier, "not_delivered", "会话运行时已关闭，插话未送达")
        try:
            if binding and binding.get("task_id"):
                from .assistant_host import finish_host_commands
                finish_host_commands(self.project_path, binding["task_id"], completed=False)
            if runner is not None:
                runner.close()
        finally:
            try:
                if host is not None:
                    host.close()
            finally:
                self._owner.close()


def get_runtime(ops, settings, api_key: str, runner_factory=HarnessRunner, *, prompt_config=None) -> ProjectConversation:
    key = _project_key(ops)
    captured = prompt_snapshot(ops) if prompt_config is None else prompt_config
    with _RUNTIMES_LOCK:
        existing = _RUNTIMES.get(key)
        if existing is not None and existing.compatible(settings, api_key, runner_factory, captured):
            return existing
        reason = "initial"
        if existing is not None:
            reason = existing.restart_reason if existing.closed else "configuration_changed"
            existing.close(reason)
        runtime = ProjectConversation(ops, settings, api_key, runner_factory, restart_reason=reason, prompt_config=captured)
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
