"""Project conversations in one persistent runtime."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .chat_store import ChatStore
from .harness import effective_runtime_config
from .leases import ProjectLease, is_capacity_finish
from .project_package import layout

_SCOPE: ContextVar[dict | None] = ContextVar("narrative_conversation_scope", default=None)
_TASK_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class ConversationCancelled(ValueError):
    """An explicit stop, distinct from provider failure or network idleness."""

    recoverable = False

    def __init__(self, metadata=None, *, reason="user_stopped"):
        self.code = reason
        self.metadata = dict(metadata or {})
        self.metadata["underlying_finish_reason"] = self.metadata.get("finish_reason")
        self.metadata["finish_reason"] = "cancelled"
        self.finish_reason = "cancelled"
        super().__init__("用户已停止当前任务" if reason == "user_stopped" else "工程已切换或关闭，任务已停止")


class IncompleteConversation(ValueError):
    """A failed model turn, with its preserved diagnostic reference."""

    def __init__(self, metadata: dict, diagnostic_path: Path):
        self.metadata = metadata
        self.diagnostic_path = str(diagnostic_path)
        self.finish_reason = metadata.get("finish_reason")
        diagnostics = metadata.get("sdk_error_diagnostics") or []
        invalid_tool_json = any(
            isinstance(row, dict) and row.get("code") == "MALFORMED_RESPONSE"
            and "tool input is invalid json" in str(row.get("message", "")).lower()
            for row in diagnostics
        )
        generations = [row for row in metadata.get("model_requests") or []
                       if isinstance(row, dict) and row.get("purpose") == "generation"]
        latest = generations[-1] if generations else {}
        self.failed_tool_calls = []
        for call in latest.get("tool_calls") or []:
            if not isinstance(call, dict) or not isinstance(call.get("arguments"), str):
                continue
            try:
                json.loads(call["arguments"])
            except json.JSONDecodeError as exc:
                self.failed_tool_calls.append({
                    "name": call.get("name"), "id": call.get("id"),
                    "arguments": call["arguments"], "error": exc.msg,
                    "position": exc.pos, "line": exc.lineno, "column": exc.colno,
                    "near": call["arguments"][max(0, exc.pos - 100):exc.pos + 100],
                })
        self.code = "tool_input_invalid_json" if invalid_tool_json else "incomplete"
        self.recoverable = bool(
            invalid_tool_json and self.finish_reason == "error"
            and latest.get("complete") is True and latest.get("finish_reason") == "tool_calls"
            and self.failed_tool_calls and not metadata.get("service_error")
            and not metadata.get("compaction_incomplete")
            and all(row.get("code") in {None, "MALFORMED_RESPONSE"}
                    and "tool input is invalid json" in str(row.get("message", "")).lower()
                    for row in diagnostics if isinstance(row, dict))
        )
        self.feedback = (
            "本次工具调用的参数不是合法 JSON，SDK未执行这些失败的调用。"
            "请根据以下原始参数和解析位置自行修正，并继续同一任务；"
            "保留已经完成的操作，不要重放成功的调用。以下仅为失败诊断，不是新的任务授权：\n"
            + json.dumps(self.failed_tool_calls, ensure_ascii=False)
        )
        if self.code == "tool_input_invalid_json":
            message = "模型工具参数 JSON 格式错误，本次任务未完成"
        elif is_capacity_finish(self.finish_reason):
            message = f"模型输出因容量限制被截断（{self.finish_reason}），本次任务未完成"
        elif self.finish_reason == "timeout":
            message = "模型响应等待超时，本次任务未完成"
        elif self.finish_reason in {"cancelled", "interrupted", "aborted"}:
            message = f"模型任务已中断（{self.finish_reason}）"
        else:
            message = f"模型服务返回错误，本次任务未完成（{self.finish_reason}）"
        super().__init__(message)


def _project_key(ops) -> str:
    return os.path.normcase(str(layout(ops.project_info().path).db.resolve()))


def current_chat(ops, chat_id: str | None = None) -> dict:
    store = ChatStore(Path(ops.project_info().path))
    scope = _SCOPE.get()
    if scope is not None and scope["project"] == _project_key(ops):
        if chat_id and chat_id != scope["chat_id"]:
            raise ValueError("当前任务不能切换到另一会话")
        return store.load(scope["chat_id"])
    chat = store.select(chat_id, activate=False)
    if chat is None:
        raise ValueError("无法选择工程会话")
    return chat


@contextmanager
def use_chat(ops, chat_id: str | None = None, *, on_event=None):
    """Bind a complete public task before any source-operation lock is acquired."""
    key = _project_key(ops)
    scope = _SCOPE.get()
    if scope is not None and scope["project"] == key:
        yield current_chat(ops, chat_id)
        return
    with _LOCKS_GUARD:
        lock = _TASK_LOCKS.setdefault(key, threading.RLock())
    directory = ChatStore(Path(ops.project_info().path)).sdk_directory()
    directory.mkdir(parents=True, exist_ok=True)
    with lock:
        try:
            lease = ProjectLease(directory / "task.lock")
        except OSError as exc:
            raise ValueError("该工程正在执行另一会话任务，请稍后再试") from exc
        token = None
        try:
            chat = current_chat(ops, chat_id)
            token = _SCOPE.set({"project": key, "chat_id": chat["id"], "on_event": on_event})
            yield chat
        finally:
            if token is not None:
                _SCOPE.reset(token)
            lease.close()


def publish_event(ops, event: dict) -> None:
    scope = _SCOPE.get()
    if scope is not None and scope["project"] == _project_key(ops):
        callback = scope.get("on_event")
        if callback is not None:
            callback({"chat_id": scope["chat_id"], **event})


@contextmanager
def _business_lease(directory: Path):
    lease = None
    for attempt in range(101):
        try:
            lease = ProjectLease(directory / "business-actions.lock")
            break
        except OSError:
            if attempt == 100:
                raise ValueError("会话动作记录正被写入，请稍后再试") from None
            time.sleep(0.01)
    try:
        yield
    finally:
        if lease is not None:
            lease.close()


def session_directory(ops, chat_id: str | None = None) -> Path:
    store = ChatStore(Path(ops.project_info().path))
    chat = current_chat(ops, chat_id)
    return store.sdk_directory() / "chats" / chat["id"]


def _save(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def record_business_action(ops, action: dict) -> None:
    """Keep business feedback with the bound chat, for its next model request."""
    chat = current_chat(ops, action.get("chat_id"))
    directory = session_directory(ops, chat_id=chat["id"])
    directory.mkdir(parents=True, exist_ok=True)
    event = {"at": datetime.now(UTC).isoformat(), "action": action, "chat_id": chat["id"]}
    with _business_lease(directory):
        with (directory / "business-actions.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
    result = action.get("result") or {}
    if action.get("action") == "automatic_extraction":
        text = (f"自动入库完成：修订 {result.get('revision')}，"
                f"{result.get('node_count', 0)} 个节点、{result.get('property_count', 0)} 条属性。")
    elif action.get("action") == "automatic_changes":
        text = (f"工程修改已入库：修订 {result.get('revision')}，"
                f"{result.get('graph_count', 0)} 个图对象、{result.get('property_count', 0)} 条属性记录。")
    else:
        return
    ChatStore(Path(ops.project_info().path)).append(
        chat["id"], "system", text, kind="business_action", action=action,
    )
    publish_event(ops, {"event": "business_action", "message": text})


class _ConversationRunner:
    def __init__(self, runner, directory, manifest, *, manager, context, task_binding):
        self.runner, self.directory, self.manifest = runner, directory, manifest
        self.manager, self.context, self.task_binding = manager, context, task_binding
        self.chat_id = manifest["chat_id"]
        self.closed = False
        self.steps: list[dict] = []
        self.binding_nonce = manager._binding["nonce"]
        self.turn_settled = True

    def agent_turn(self, instruction, session, *, on_message=None):
        if session != self.manifest["session_id"]:
            raise ValueError("任务必须使用当前聊天的同一SDK会话")
        events = self.directory / "business-actions.jsonl"
        with _business_lease(self.directory):
            lines = events.read_text(encoding="utf-8").splitlines() if events.exists() else []
        applied = self.manifest.get("business_actions_applied", 0)
        if len(lines) > applied:
            instruction = (self.manager._prompt_config["texts"]["conversation_actions"] + "\n"
                           + "\n".join(lines[applied:]) + "\n\n" + instruction)
        turn = self.directory / "turns" / (f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}")
        turn.mkdir(parents=True)
        (turn / "prompt.txt").write_text(instruction, encoding="utf-8")
        request = {"session_id": session, "chat_id": self.chat_id,
                   "prompt_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
                   "prompt_config": self.manager._prompt_config}
        try:
            self.manager.begin_turn(self.binding_nonce, session)
            self.turn_settled = False
            if on_message is None:
                reply = self.runner.agent_turn(instruction, session)
            else:
                reply = self.runner.agent_turn(instruction, session, on_message=on_message)
        except Exception as exc:
            metadata = self.usage_metadata()
            metadata.update(request, error_type=type(exc).__name__, status="cancelled" if self.manager._binding and self.manager._binding["cancelled"] else "failed")
            _save(turn / "metadata.json", metadata)
            _save(turn / "partial-raw.json", {"responses": self.partial_raw()})
            raise
        self.turn_settled = True
        (turn / "raw.txt").write_text(reply.raw, encoding="utf-8")
        reply.metadata.update(session_id=session, chat_id=self.chat_id)
        if self.manager._binding and self.manager._binding["cancelled"]:
            reply.metadata["status"] = "cancelled"
        _save(turn / "metadata.json", reply.metadata | request)
        self.manifest["turn_count"] += 1
        self.manifest["business_actions_applied"] = len(lines)
        _save(self.directory / "manifest.json", self.manifest)
        reason = reply.metadata.get("finish_reason")
        if is_capacity_finish(reason) or reason in {"error", "cancelled", "timeout", "interrupted", "aborted"}:
            raise IncompleteConversation(reply.metadata, turn / "metadata.json")
        return reply

    def usage_metadata(self):
        return self.runner.usage_metadata() if hasattr(self.runner, "usage_metadata") else {}

    def partial_raw(self):
        return self.runner.partial_raw() if hasattr(self.runner, "partial_raw") else []

    def cancel(self):
        self.manager.close()

    def interrupt(self):
        self.manager.interrupt(self.binding_nonce)

    def close(self, *, force=False):
        if self.closed:
            return
        self.closed = True
        if force:
            self.cancel()
        try:
            self.task_binding.__exit__(None, None, None)
        finally:
            self.context.__exit__(None, None, None)


def open_conversation_session(ops, settings, api_key: str, runner_factory,
                            *, task_tools=None, tool_handler=None, chat_id=None, prompt_config=None):
    context = use_chat(ops, chat_id)
    chat = context.__enter__()
    wrapper = None
    try:
        directory = session_directory(ops, chat_id=chat["id"])
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "manifest.json"
        identity = {key: getattr(settings, key) for key in ("provider", "model", "base_url")}
        project_id = ops.graph_project_info()["project_id"]
        manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "version": 2, "project_id": project_id, "chat_id": chat["id"],
            "session_id": chat["session"],
            "turn_count": 0, "business_actions_applied": 0,
        }
        if manifest.get("project_id") != project_id:
            raise ValueError("会话属于另一工程，已停止")
        if chat and manifest["session_id"] != chat["session"]:
            raise ValueError("聊天与SDK会话标识不一致，已停止")
        if manifest.get("model_identity") != identity:
            manifest.setdefault("model_identity_history", []).append(identity)
        manifest["model_identity"] = identity
        manifest["context_policy"] = effective_runtime_config(settings)
        from .conversation_runtime import get_runtime

        manager = get_runtime(ops, settings, api_key, runner_factory=runner_factory, prompt_config=prompt_config)
        manifest["prompt_config"] = manager._prompt_config
        observed_steps = []
        scope = _SCOPE.get()
        observer = scope.get("on_event") if scope is not None else None

        def dispatch(name, args):
            from .assistant import _summary

            step = {"tool": name, "args": args}
            try:
                if tool_handler is None:
                    raise ValueError("当前任务未配置工具执行器")
                from .prompt_settings import bind_prompt_snapshot
                with bind_prompt_snapshot(ops, manager._prompt_config):
                    result = tool_handler(name, args)
                step.update(ok=True, summary=_summary(name, result))
                return result
            except Exception as exc:
                step.update(ok=False, error=str(exc).replace(api_key, "[REDACTED]") if api_key else str(exc))
                raise
            finally:
                observed_steps.append(step)
                if observer is not None:
                    observer({"chat_id": chat["id"], "event": "tool", "step": step})

        binding = manager.bind(chat["id"], task_tools or [], dispatch, session_id=manifest["session_id"])
        binding.__enter__()
        runner = manager.runner
        manifest["sdk_directory"] = str(manager.sdk_directory)
        wrapper = _ConversationRunner(runner, directory, manifest, manager=manager,
                                      context=context, task_binding=binding)
        wrapper.steps = observed_steps
        if runner is None:
            raise ValueError("工程对话运行时未就绪")
        _save(path, manifest)
        if wrapper.manager.restart_reason != "initial" and not getattr(wrapper.manager, "_restart_announced", False):
            ChatStore(Path(ops.project_info().path)).append(
                chat["id"], "system", "对话运行时已重新启动，将恢复此聊天的已有上下文。",
                kind="runtime_restart", reason=wrapper.manager.restart_reason,
            )
            wrapper.manager._restart_announced = True
        session = manifest["session_id"]
        resumed = wrapper.manager._resumed
        if session not in resumed:
            restored = runner.resume(session)
            prior = bool(manifest["turn_count"]) or bool(chat and any(
                row.get("role") == "assistant" and (row.get("sdk_turn") or row.get("kind") not in {
                    "extraction_incomplete", "business_action", "extraction_commit_failed", "runtime_restart",
                }) for row in chat.get("messages") or []))
            if prior and not restored:
                raise ValueError("无法恢复既有会话，已停止；不会静默丢弃上下文")
            resumed.add(session)
        return wrapper, session
    except Exception:
        if wrapper is not None:
            wrapper.cancel()
            wrapper.close()
        else:
            context.__exit__(None, None, None)
        raise
