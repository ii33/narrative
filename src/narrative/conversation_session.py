"""Project conversations in one persistent runtime."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
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
        rate_limited = any(isinstance(row, dict) and row.get("code") == "RATE_LIMIT"
                           for row in diagnostics) or str(metadata.get("service_error", "")).startswith("HTTP 429")
        server_status = re.match(r"^HTTP\s+(5\d\d)\b", str(metadata.get("service_error", "")))
        server_error = bool(server_status) or any(isinstance(row, dict) and row.get("code") == "SERVER"
                                                   for row in diagnostics)
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
        self.code = ("tool_input_invalid_json" if invalid_tool_json else "rate_limited" if rate_limited
                     else "provider_server_error" if server_error else "incomplete")
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
        elif self.code == "rate_limited":
            message = "模型服务限流（HTTP 429），本次任务未完成；未自动重试，请稍后重新发送"
        elif self.code == "provider_server_error":
            status = f"（HTTP {server_status.group(1)}）" if server_status else ""
            message = f"模型服务内部错误{status}，本次任务未完成；未自动重试"
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


def current_access_mode(ops):
    """Read the active task snapshot, never a later chat preference."""
    scope = _SCOPE.get()
    if scope is None or scope["project"] != _project_key(ops):
        return "standard"
    from .conversation_runtime import get_existing_runtime
    manager = get_existing_runtime(ops)
    if manager is None:
        return "standard"
    with manager._lock:
        binding = manager._binding
        if manager.closed or not binding or binding["cancelled"] or binding["chat_id"] != scope["chat_id"]:
            return "standard"
        return binding["access_mode"]


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
    from .assistant_audit import sanitize_host_audit
    payload = sanitize_host_audit(payload)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def record_business_action(ops, action: dict) -> None:
    """Keep business feedback with the bound chat, for its next model request."""
    from .graph_store import defer_after_commit
    deferred_action = deepcopy(action)
    def committed_feedback():
        try:
            record_business_action(ops, deferred_action)
        except Exception as exc:
            # The business transaction is already committed. A failed feedback
            # projection must never claim its durable facts were rolled back.
            try:
                publish_event(ops, {'event': 'business_feedback_error', 'severity': 'warning',
                                   'message': str(exc), 'error_type': type(exc).__name__})
            except Exception:
                logging.getLogger(__name__).exception('已提交业务的反馈记录失败')
    if defer_after_commit(ops._store.path, committed_feedback):
        return
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
    def __init__(self, runner, directory, manifest, *, manager, context, task_binding,
                 ops, observer=None, task_id=None, output_presentation="answer", access_mode="standard"):
        self.access_mode = access_mode
        self._host_task_id = manager._binding["task_id"]
        self._task_completed = False
        self.host_cleanup = None
        self._latest_turn_metadata = None
        self._latest_turn_path = None
        self._latest_turn_id = None
        self.output_presentation = output_presentation
        self.runner, self.directory, self.manifest = runner, directory, manifest
        self.manager, self.context, self.task_binding = manager, context, task_binding
        self.chat_id = manifest["chat_id"]
        self.closed = False
        self.steps: list[dict] = []
        self.binding_nonce = manager._binding["nonce"]
        self.turn_settled = True
        self.ops, self.observer, self.task_id = ops, observer, task_id
        self._usage_signature = None
        self._latest_native_context = {}
        self.commit_summary = None
        self.commit_summary_text = ""

    def _audit_text(self, value: str) -> str:
        redact = getattr(self.runner, "redact", None)
        return str(redact(value)) if callable(redact) else value

    def _save_turn_audit(self, path, payload):
        safe = json.loads(self._audit_text(json.dumps(payload, ensure_ascii=False)))
        _save(path, safe)

    def _set_commit_summary(self, metadata, status, turn_id):
        from .task_write_summary import build_submission_summary

        self.commit_summary = build_submission_summary(self.steps, status=status, task_id=self.task_id or turn_id, host_cleanup=metadata.get("host_cleanup"))
        self.commit_summary_text = self.commit_summary["text"]
        metadata.update(commit_summary=self.commit_summary, commit_summary_text=self.commit_summary_text)

    def _publish_usage(self, metadata, event, turn_id):
        from .usage_statistics import publish_live_usage

        if isinstance(metadata.get("native_context"), dict):
            self._latest_native_context = dict(metadata["native_context"])
        sample = {key: metadata[key] for key in ("provider", "model", "context_policy") if key in metadata}
        sample.update(usage_turn_id=turn_id, native_context=self._latest_native_context,
                      model_requests=[{key: row[key] for key in ("request_id", "usage_sampled_at", "purpose", "usage", "complete", "finish_reason") if key in row}
                                      for row in metadata.get("model_requests") or [] if isinstance(row, dict)])
        signature = json.dumps(sample, sort_keys=True, ensure_ascii=False)
        if signature == self._usage_signature:
            return
        self._usage_signature = signature
        statistics = publish_live_usage(self.ops.project_info().path, self.chat_id,
            self.task_id or turn_id, sample, turn_id=turn_id, event=event)
        if self.observer is not None:
            self.observer({"chat_id": self.chat_id, "task_id": self.task_id or turn_id,
                           "event": "usage_update", "usage_statistics": statistics})

    def agent_turn(self, instruction, session, *, on_message=None):
        if session != self.manifest["session_id"]:
            raise ValueError("任务必须使用当前聊天的同一SDK会话")
        from .conversation_steering import instruction_audit, pack_instruction
        from .output_presentation import OUTPUT_CONTRACT, OUTPUT_CONTRACT_VERSION, present_message
        from .tool_loading import TOOL_LOADING_GUIDE
        instruction = pack_instruction(instruction, OUTPUT_CONTRACT)
        instruction = pack_instruction(instruction, TOOL_LOADING_GUIDE)
        instruction = pack_instruction(instruction, "本任务访问模式：" + self.access_mode + ("。当前页面目标仅为创作焦点提示，不构成工具或写入范围限制；可按用户要求操作其他工程业务，并通过获授权的宿主工具访问工程外文件及系统命令。业务静态校验、并发修订和操作审计照常保留。" if self.access_mode == "full" else "。仅使用当前任务获授权的工程工具，工程外文件及系统命令未开放。"))
        def visible_message(event):
            if on_message is not None:
                on_message(present_message(event, self.output_presentation))
        events = self.directory / "business-actions.jsonl"
        with _business_lease(self.directory):
            lines = events.read_text(encoding="utf-8").splitlines() if events.exists() else []
        applied = self.manifest.get("business_actions_applied", 0)
        if len(lines) > applied:
            instruction = pack_instruction(self.manager._prompt_config["texts"]["conversation_actions"] + "\n" + "\n".join(lines[applied:])) + instruction
        turn = self.directory / "turns" / (f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}")
        turn.mkdir(parents=True)
        turn_id = turn.name
        self._task_completed = False
        # Without an explicit business task, one SDK call is one task. Its
        # receipt must not inherit another independently identified call.
        if self.task_id is None:
            self.steps.clear()
        self._usage_signature = None
        (turn / "prompt.txt").write_text(self._audit_text(instruction_audit(instruction)), encoding="utf-8")
        request = {"session_id": session, "chat_id": self.chat_id,
                   "task_id": self.task_id or turn_id,
                   "usage_turn_id": turn_id, "output_contract_version": OUTPUT_CONTRACT_VERSION,
                   "output_presentation": self.output_presentation, "access_mode": self.access_mode, "host_task_id": self._host_task_id,
                   "prompt_sha256": hashlib.sha256(json.dumps(instruction, ensure_ascii=False).encode()).hexdigest(),
                   "prompt_config": self.manager._prompt_config}
        try:
            self.manager.begin_turn(self.binding_nonce, session, task_id=self._host_task_id)
            self.turn_settled = False
            observe_usage = getattr(self.runner, "usage_events", None)
            telemetry = observe_usage(lambda data, event: self._publish_usage(data, event, turn_id)) if observe_usage else nullcontext()
            with telemetry:
                if on_message is None:
                    reply = self.runner.agent_turn(instruction, session)
                else:
                    reply = self.runner.agent_turn(instruction, session, on_message=visible_message)
        except Exception as exc:
            metadata = self.usage_metadata()
            if self._latest_native_context:
                metadata["native_context"] = dict(self._latest_native_context)
            metadata.update(request, error_type=type(exc).__name__, status="cancelled" if self.manager._binding and self.manager._binding["cancelled"] else "failed")
            self._latest_turn_metadata, self._latest_turn_path, self._latest_turn_id = metadata, turn, turn_id
            self._set_commit_summary(metadata, metadata["status"], turn_id)
            self._save_turn_audit(turn / "metadata.json", metadata)
            self._save_turn_audit(turn / "partial-raw.json", {"responses": self.partial_raw()})
            self._publish_usage(metadata, "turn_error", turn_id)
            exc.__dict__["metadata"] = metadata
            raise
        self.turn_settled = True
        (turn / "raw.txt").write_text(self._audit_text(reply.raw), encoding="utf-8")
        reply.metadata["model_messages"] = [present_message(event, self.output_presentation) for event in reply.metadata.get("model_messages", [])]
        reply.metadata.update(session_id=session, chat_id=self.chat_id, task_id=self.task_id or turn_id, access_mode=self.access_mode)
        if self._latest_native_context:
            reply.metadata["native_context"] = dict(self._latest_native_context)
        if self.manager._binding and self.manager._binding["cancelled"]:
            reply.metadata["status"] = "cancelled"
        reason = reply.metadata.get("finish_reason")
        status = "cancelled" if reply.metadata.get("status") == "cancelled" else "incomplete" if (
            is_capacity_finish(reason) or reason in {"error", "cancelled", "timeout", "interrupted", "aborted"}
        ) else "completed"
        self._task_completed = status == "completed"
        reply.metadata.update(request)
        self._latest_turn_metadata, self._latest_turn_path, self._latest_turn_id = reply.metadata, turn, turn_id
        self._set_commit_summary(reply.metadata, status, turn_id)
        self._save_turn_audit(turn / "metadata.json", reply.metadata | request)
        # The final SDK call shares the same identity as its live observations;
        # historical statistics replace that turn rather than adding a copy.
        reply.metadata["usage_turn_id"] = turn_id
        self._publish_usage(reply.metadata, "turn_end", turn_id)
        self.manifest["turn_count"] += 1
        self.manifest["business_actions_applied"] = len(lines)
        _save(self.directory / "manifest.json", self.manifest)
        if is_capacity_finish(reason) or reason in {"error", "cancelled", "timeout", "interrupted", "aborted"}:
            raise IncompleteConversation(reply.metadata, turn / "metadata.json")
        return reply

    def steer(self, instruction, *, message_id=None, on_status=None):
        if self.closed:
            raise ValueError("任务已结束，未发送插话")
        from .conversation_steering import pack_instruction
        return self.manager.steer(self.chat_id, self._host_task_id, self.binding_nonce,
                                  pack_instruction(instruction), message_id=message_id, on_status=on_status)

    def usage_metadata(self):
        return self.runner.usage_metadata() if hasattr(self.runner, "usage_metadata") else {}

    def partial_raw(self):
        return self.runner.partial_raw() if hasattr(self.runner, "partial_raw") else []

    def cancel(self):
        self.manager.close()

    def interrupt(self):
        self.manager.interrupt(self.binding_nonce)

    def _persist_host_cleanup(self):
        metadata, turn, turn_id = self._latest_turn_metadata, self._latest_turn_path, self._latest_turn_id
        if metadata is None or turn is None:
            return
        metadata["host_cleanup"] = self.host_cleanup
        binding = self.manager._binding
        if binding and binding["nonce"] == self.binding_nonce and binding["cancelled"]:
            metadata["status"] = "cancelled"
        if self.host_cleanup and any(row.get("reason") == "task_completed_before_command_finished"
                                     for row in self.host_cleanup.get("cleanup_receipts", [])):
            metadata["status"] = "incomplete"
            self._task_completed = False
        status = metadata.get("status") or ("completed" if self._task_completed else "failed")
        self._set_commit_summary(metadata, status, turn_id)
        self._save_turn_audit(turn / "metadata.json", metadata)

    def close(self, *, force=False, completed=None):
        if self.closed:
            return self.host_cleanup
        self.closed = True
        if force:
            self.cancel()
        try:
            try:
                if self._host_task_id is not None:
                    from .assistant_host import finish_host_commands
                    cleanup_completed = (self._task_completed if completed is None else completed) and not force
                    if not cleanup_completed:
                        self._task_completed = False
                    self.host_cleanup = finish_host_commands(self.ops.project_info().path, self._host_task_id,
                        completed=cleanup_completed)
            except Exception as exc:
                self.host_cleanup = {"task_id": self._host_task_id, "commands": [], "cleanup_receipts": [],
                    "diagnostics": [{"severity": "error", "code": "host_cleanup_failed", "message": "主机命令收束失败：" + type(exc).__name__}]}
                raise
            finally:
                try:
                    self._persist_host_cleanup()
                finally:
                    self.task_binding.__exit__(None, None, None)
        finally:
            self.context.__exit__(None, None, None)
        return self.host_cleanup


def open_conversation_session(ops, settings, api_key: str, runner_factory,
                            *, task_tools=None, tool_handler=None, chat_id=None, prompt_config=None, task_id=None, output_presentation="answer", access_mode=None, cancel_event=None):
    context = use_chat(ops, chat_id)
    chat = context.__enter__()
    wrapper = None
    try:
        if access_mode is None:
            access_mode = ops.read_assistant_access(chat["id"])["access_mode"]
        if access_mode not in {"standard", "full"}:
            raise ValueError("访问模式须为standard或full")
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
            from .assistant_audit import sanitize_host_audit
            step = sanitize_host_audit({"tool": name, "args": args})
            try:
                if tool_handler is None:
                    raise ValueError("当前任务未配置工具执行器")
                from .prompt_settings import bind_prompt_snapshot
                with bind_prompt_snapshot(ops, manager._prompt_config):
                    result = tool_handler(name, args)
                step.update(ok=True, summary=_summary(name, result), result=result)
                return result
            except Exception as exc:
                step.update(ok=False, error=str(exc).replace(api_key, "[REDACTED]") if api_key else str(exc))
                raise
            finally:
                observed_steps.append(step)
                if observer is not None:
                    observer({"chat_id": chat["id"], "event": "tool", "step": step})

        from .assistant import tool_catalog
        task_catalog = tool_catalog(access_mode=access_mode) if task_tools is None else task_tools
        binding = manager.bind(chat["id"], task_catalog, dispatch, session_id=manifest["session_id"],
            access_mode=access_mode, task_id=task_id, cancel_event=cancel_event,
            secrets_to_redact=tuple(secret for secret in (api_key, manager.host.token if manager.host else "") if secret))
        binding.__enter__()
        runner = manager.runner
        manifest["sdk_directory"] = str(manager.sdk_directory)
        wrapper = _ConversationRunner(runner, directory, manifest, manager=manager,
                                      context=context, task_binding=binding, ops=ops,
                                      observer=observer, task_id=task_id, output_presentation=output_presentation, access_mode=access_mode)
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
