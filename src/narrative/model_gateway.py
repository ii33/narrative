"""Local, authenticated wire adapter between DSH and Chat Completions services.

Translates request parameters and provider session headers. It never retries, follows redirects, stores
prompts or credentials, or changes the model response. The SDK still owns turns.
"""

import ctypes
import hashlib
import http.client
import json
import os
import re
import secrets
import socket
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

COMPACTION_MODEL = "__narrative_compaction__"

# 运行时（SDK）只认这三档；别的值它直接拒绝，所以这些档由网关自己写进请求体。
RUNTIME_EFFORTS = {"low", "medium", "high"}


def adapt_request(body: dict, settings) -> dict:
    body = dict(body)
    output_limit = (
        settings.compaction_max_tokens
        if body.get("model") == COMPACTION_MODEL
        else settings.max_tokens
    )
    body.pop("max_tokens", None)
    body.pop("max_completion_tokens", None)
    if output_limit is not None:
        body[settings.token_parameter] = output_limit
    body.pop("reasoning_effort", None)
    body.pop("thinking", None)
    if str(settings.reasoning_effort or "") == "off":
        # 「不思考」不是推理等级的下一档：运行时只认 low/medium/high，传别的直接拒。
        # 供应商自己的开关是 `thinking.type = disabled`，在这里补上——推理 token 归零，
        # 一章从几分钟降到几十秒。
        body["thinking"] = {"type": "disabled"}
    elif settings.reasoning_effort:
        body["reasoning_effort"] = settings.reasoning_effort
    body["model"] = settings.model
    temperature = getattr(settings, "temperature", None)
    if temperature is not None:
        # 运行时不带 temperature，这里补上；配了就用配的，不配就保持供应商默认。
        body["temperature"] = float(temperature)
    if body.get("stream"):
        body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
    return body


class UsageObserver:
    """Observe SSE usage without changing the stream; last cumulative report wins."""

    def __init__(self):
        self.buffer = b""
        self.usage: dict | None = None
        self.complete = False
        self.content: list[str] = []
        self.finish_reason: str | None = None
        self.tools: dict[tuple[int, int], dict] = {}

    def feed(self, chunk: bytes):
        frames = re.split(rb"\r?\n\r?\n", self.buffer + chunk)
        self.buffer = frames.pop()
        for frame in frames:
            data = b"\n".join(
                line[5:].lstrip() for line in frame.splitlines() if line.startswith(b"data:")
            )
            if data.strip() == b"[DONE]":
                self.complete = True
                continue
            try:
                payload = json.loads(data)
            except (ValueError, UnicodeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
                self.usage = payload["usage"]
            if isinstance(payload, dict):
                for choice in payload.get("choices", []):
                    if isinstance(choice, dict) and isinstance(choice.get("finish_reason"), str):
                        self.finish_reason = choice["finish_reason"]
                    delta = choice.get("delta", {}) if isinstance(choice, dict) else {}
                    part = delta.get("content") if isinstance(delta, dict) else None
                    if isinstance(part, str):
                        self.content.append(part)
                    if not isinstance(delta, dict):
                        continue
                    for call in delta.get("tool_calls") or []:
                        if not isinstance(call, dict):
                            continue
                        index = call.get("index", 0)
                        choice_index = choice.get("index", 0)
                        if type(index) is not int or type(choice_index) is not int:
                            continue
                        tool = self.tools.setdefault((choice_index, index), {
                            "choice_index": choice_index, "index": index,
                            "id": "", "name": "", "argument_fragments": [],
                        })
                        if isinstance(call.get("id"), str) and call["id"]:
                            tool["id"] = tool["id"] or call["id"]
                        function = call.get("function") or {}
                        if not isinstance(function, dict):
                            continue
                        if isinstance(function.get("name"), str) and function["name"]:
                            tool["name"] = tool["name"] or function["name"]
                        arguments = function.get("arguments")
                        if isinstance(arguments, str):
                            tool["argument_fragments"].append(arguments)
        # Usage frames are small; never retain an unbounded malformed event.
        if len(self.buffer) > 1024 * 1024:
            self.buffer = b""


    def tool_snapshot(self) -> list[dict]:
        """Preserve provider arguments verbatim; validation never repairs them."""
        result = []
        for key in sorted(self.tools):
            tool = self.tools[key]
            raw = "".join(tool["argument_fragments"])
            error = None
            try:
                json.loads(raw)
            except json.JSONDecodeError as exc:
                error = {"message": exc.msg, "position": exc.pos,
                         "line": exc.lineno, "column": exc.colno}
            result.append(deepcopy(tool) | {"arguments": raw,
                "arguments_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "arguments_json_valid": error is None, "arguments_json_error": error})
        return result


class _KeepResponseSocket(http.client.HTTPConnection):
    """Keep the socket reachable after http.client transfers it to HTTPResponse."""

    def getresponse(self):
        self._getting_response = True
        try:
            return super().getresponse()
        finally:
            self._getting_response = False

    def close(self):
        if getattr(self, "_getting_response", False):
            # HTTPConnection.getresponse() calls close() for Connection: close.
            # HTTPResponse's file still reads this socket; the handler owns its
            # final close and a concurrent stop must be able to shutdown it.
            self.sock = None
            return
        super().close()


class _UpstreamHTTPConnection(_KeepResponseSocket):
    pass


class _UpstreamHTTPSConnection(_KeepResponseSocket, http.client.HTTPSConnection):
    pass


class ModelGateway:
    def __init__(self, settings, api_key: str, *, secrets_to_redact: tuple[str, ...] = ()):
        self.settings, self._key = settings, api_key
        self._extra_secrets = secrets_to_redact
        self.token = secrets.token_urlsafe(32)
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._handlers_done = threading.Condition(self._lock)
        self._session_lock = threading.Lock()
        self._session_id: str | None = None
        self._turn_epoch = 0
        self._session_active = False
        self._cancelled_epoch: int | None = None
        self._handler_counts: dict[int, int] = {}
        self._usage_records: list[dict] = []
        self._compaction_failure: str | None = None
        self._blocked_generation_count = 0
        self._connections: set[http.client.HTTPConnection] = set()
        self._connection_epochs: dict[http.client.HTTPConnection, int] = {}
        # getresponse() moves the socket into HTTPResponse. Keep a reference to
        # the original socket so shutdown interrupts read1 on every platform.
        self._connection_sockets: dict[http.client.HTTPConnection, socket.socket] = {}
        self.error: str | None = None
        # Last upstream response-byte activity, recorded from the socket read
        # itself. This is deliberately separate from any local heartbeat.
        self._upstream_activity_utc: str | None = None
        self._upstream_activity_monotonic: float | None = None
        self._upstream_response_bytes = 0
        self._upstream_response_events = 0
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args):
                pass

            def _drain_short_unauthorized_body(self):
                """Avoid a Windows TCP reset when rejecting a short POST with unread body."""
                declared = self.headers.get("Content-Length", "")
                if not re.fullmatch(r"[0-9]{1,20}", declared):
                    return
                remaining = int(declared)
                if not 0 < remaining <= 65536:
                    return
                previous_timeout = self.connection.gettimeout()
                deadline = time.monotonic() + 0.25
                try:
                    while remaining:
                        timeout = deadline - time.monotonic()
                        if timeout <= 0:
                            break
                        self.connection.settimeout(timeout)
                        chunk = self.rfile.read1(min(remaining, 8192))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                except OSError:
                    pass
                finally:
                    self.connection.settimeout(previous_timeout)

            def do_POST(self):
                # 0.2.x 的运行时说 Messages；两种都收，翻译在 messages_bridge 里。
                if self.path not in {"/v1/chat/completions", "/v1/messages"}:
                    self.send_error(404)
                    return
                messages_mode = self.path == "/v1/messages"
                debug = os.environ.get("NARRATIVE_GATEWAY_DEBUG")
                # Messages 用 x-api-key，chat/completions 用 Bearer：两种都认。
                authorized = secrets.compare_digest(
                    self.headers.get("Authorization", ""), f"Bearer {gateway.token}"
                ) or secrets.compare_digest(
                    self.headers.get("x-api-key", ""), gateway.token
                )
                if not authorized:
                    self._drain_short_unauthorized_body()
                    self.send_error(401)
                    return
                sent_headers = False
                connection = None
                response = None
                purpose = None
                observer = None
                usage_record: dict | None = None
                with gateway._lock:
                    turn_epoch = gateway._turn_epoch
                    gateway._handler_counts[turn_epoch] = gateway._handler_counts.get(turn_epoch, 0) + 1
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 16 * 1024 * 1024:
                        raise ValueError("模型请求大小不合法")
                    payload = json.loads(self.rfile.read(size))
                    if not isinstance(payload, dict):
                        raise ValueError("模型请求须为对象")
                    if messages_mode:
                        from .messages_bridge import to_chat_request

                        if debug:
                            with open(debug, "a", encoding="utf-8") as dump:
                                dump.write(
                                    "\n=== messages request ===\n"
                                    + json.dumps(payload, ensure_ascii=False)[:4000]
                                    + "\n"
                                )
                        payload = to_chat_request(payload)
                    purpose = (
                        "compaction" if payload.get("model") == COMPACTION_MODEL else "generation"
                    )
                    with gateway._lock:
                        if not gateway._turn_live_locked(turn_epoch):
                            raise ValueError("当前模型任务已取消")
                        if gateway._compaction_failure is not None:
                            if purpose == "generation":
                                gateway._blocked_generation_count += 1
                            raise ValueError(
                                gateway._compaction_failure + "；已阻止后续模型请求，没有自动重试"
                            )
                    body = json.dumps(adapt_request(payload, settings), ensure_ascii=False).encode()
                    headers = gateway._upstream_headers()
                    url = urlsplit(settings.base_url)
                    connection_type = (
                        _UpstreamHTTPSConnection
                        if url.scheme == "https"
                        else _UpstreamHTTPConnection
                    )
                    connection = connection_type(
                        url.hostname, url.port, timeout=settings.timeout_seconds
                    )
                    with gateway._lock:
                        if not gateway._turn_live_locked(turn_epoch):
                            raise ValueError("当前模型任务已取消")
                        gateway._connections.add(connection)
                        gateway._connection_epochs[connection] = turn_epoch
                        usage_record = {
                            "usage": None,
                            "complete": False,
                            "raw": "",
                            "tool_calls": [],
                            "purpose": purpose,
                            "finish_reason": None,
                            "max_tokens": settings.compaction_max_tokens
                            if purpose == "compaction"
                            else settings.max_tokens,
                        }
                        gateway._usage_records.append(usage_record)
                    if not gateway._turn_live(turn_epoch):
                        raise ValueError("当前模型任务已取消")
                    connection.connect()
                    with gateway._lock:
                        upstream_socket = connection.sock
                        if upstream_socket is not None:
                            gateway._connection_sockets[connection] = upstream_socket
                        live = gateway._turn_live_locked(turn_epoch)
                    if not live:
                        raise ValueError("当前模型任务已取消")
                    connection.request(
                        "POST",
                        url.path.rstrip("/") + "/chat/completions",
                        body,
                        headers,
                    )
                    response = connection.getresponse()
                    # A response header is the first upstream byte activity.
                    if not gateway._turn_live(turn_epoch):
                        raise ValueError("当前模型任务已取消")
                    gateway._note_upstream_activity(epoch=turn_epoch)
                    # Never forward a redirect carrying the provider's credential.
                    status = response.status
                    if 300 <= status < 400:
                        raise ValueError("模型服务返回重定向，请填写最终 API 地址")
                    self.send_response(status)
                    self.send_header(
                        "Content-Type", response.getheader("Content-Type", "application/json")
                    )
                    self.send_header("Connection", "close")
                    self.end_headers()
                    sent_headers = True
                    if status >= 400:
                        error = response.read(65536)
                        gateway._note_upstream_activity(len(error), epoch=turn_epoch)
                        if gateway._key:
                            error = error.replace(gateway._key.encode(), b"[REDACTED]")
                        with gateway._lock:
                            usage_record["raw"] = error.decode("utf-8", errors="replace")
                        try:
                            detail = json.loads(error).get("error", {})
                            detail = (
                                detail.get("message", detail)
                                if isinstance(detail, dict)
                                else detail
                            )
                        except (ValueError, AttributeError):
                            detail = error.decode("utf-8", errors="replace")
                        message = f"HTTP {status}：{str(detail)[:1000]}"
                        gateway._set_error(message, turn_epoch)
                        if purpose == "compaction":
                            gateway.fail_compaction(message, epoch=turn_epoch)
                        if gateway._turn_live(turn_epoch):
                            self.wfile.write(error)
                    else:
                        observer = UsageObserver()
                        converter = None
                        if messages_mode:
                            from .messages_bridge import MessagesStream

                            converter = MessagesStream(model=settings.model)
                        while gateway._turn_live(turn_epoch):
                            chunk = response.read1(65536)
                            if not gateway._turn_live(turn_epoch):
                                break
                            if not chunk:
                                break
                            observer.feed(chunk)
                            gateway._note_upstream_activity(len(chunk), epoch=turn_epoch)
                            if purpose == "compaction" and observer.finish_reason not in {
                                None,
                                "stop",
                            }:
                                gateway.fail_compaction(
                                    f"摘要流未完整结束（finish_reason={observer.finish_reason}）",
                                    epoch=turn_epoch,
                                )
                            with gateway._lock:
                                usage_record["usage"] = observer.usage
                                usage_record["complete"] = observer.complete
                                usage_record["raw"] = "".join(observer.content)
                                usage_record["finish_reason"] = observer.finish_reason
                            if converter is not None:
                                out = converter.feed(chunk)
                                if not out:
                                    # Real upstream bytes can contain reasoning
                                    # or a partial SSE frame without visible text.
                                    # Reflect that activity without exposing it;
                                    # never generate traffic while upstream is idle.
                                    out = b'event: ping\ndata: {"type":"ping"}\n\n'
                                if debug:
                                    with open(debug, "a", encoding="utf-8") as dump:
                                        dump.write(out.decode("utf-8", "replace"))
                                self.wfile.write(out)
                            else:
                                self.wfile.write(chunk)
                            self.wfile.flush()
                        # Persist the upstream arguments before block-stop makes
                        # the SDK validate them and potentially end the turn.
                        with gateway._lock:
                            usage_record["tool_calls"] = observer.tool_snapshot()
                        if converter is not None and gateway._turn_live(turn_epoch):
                            self.wfile.write(converter.finish())
                            self.wfile.flush()
                        if gateway._turn_live(turn_epoch):
                            observer.feed(b"\n\n")
                        with gateway._lock:
                            usage_record["usage"] = observer.usage
                            usage_record["complete"] = observer.complete
                            usage_record["raw"] = "".join(observer.content)
                            usage_record["finish_reason"] = observer.finish_reason
                        if gateway._turn_live(turn_epoch) and purpose == "compaction" and (
                            not observer.complete or observer.finish_reason != "stop"
                        ):
                            gateway.fail_compaction(
                                f"摘要流未完整结束（finish_reason={observer.finish_reason or 'unknown'}，complete={observer.complete}）",
                                epoch=turn_epoch,
                            )
                except (OSError, ValueError, http.client.HTTPException) as exc:
                    if purpose == "compaction":
                        gateway.fail_compaction(str(exc), epoch=turn_epoch)
                    if not sent_headers and gateway._turn_live(turn_epoch):
                        message = (
                            str(exc).replace(gateway._key, "[REDACTED]")
                            if gateway._key
                            else str(exc)
                        )
                        gateway._set_error(message[:1000], turn_epoch)
                        content = json.dumps(
                            {"error": {"message": message}}, ensure_ascii=False
                        ).encode()
                        try:
                            self.send_response(502)
                            self.send_header("Content-Type", "application/json")
                            self.send_header("Content-Length", str(len(content)))
                            self.end_headers()
                            self.wfile.write(content)
                        except OSError:
                            pass
                finally:
                    if observer is not None and usage_record is not None:
                        with gateway._lock:
                            usage_record["tool_calls"] = observer.tool_snapshot()
                    if connection:
                        with gateway._lock:
                            gateway._connections.discard(connection)
                            gateway._connection_epochs.pop(connection, None)
                            interrupt_socket = gateway._connection_sockets.pop(connection, None)
                        if response is not None:
                            response.close()
                        if interrupt_socket is not None:
                            interrupt_socket.close()
                        connection.close()
                    with gateway._handlers_done:
                        gateway._handler_counts[turn_epoch] -= 1
                        if not gateway._handler_counts[turn_epoch]:
                            del gateway._handler_counts[turn_epoch]
                        gateway._handlers_done.notify_all()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_port}/v1"

    @contextmanager
    def session(self, session_id: str):
        """Bind every SDK request, including auxiliary calls, to its actual conversation."""
        if self.settings.provider == "opencode-go" and (
            not session_id
            or len(session_id) > 256
            or any(not 33 <= ord(char) <= 126 for char in session_id)
        ):
            raise ValueError("OpenCode Go 会话标识须为 1–256 个非空白 ASCII 字符")
        if not self._session_lock.acquire(blocking=False):
            raise ValueError("同一模型运行器不能同时执行多个会话")
        turn_epoch: int | None = None
        try:
            with self._lock:
                self._turn_epoch += 1
                self._session_active = True
                self._cancelled_epoch = None
                self._session_id = session_id
                self.error = None
                self._usage_records = []
                self._compaction_failure = None
                self._blocked_generation_count = 0
                self._upstream_activity_utc = None
                self._upstream_activity_monotonic = None
                self._upstream_response_bytes = 0
                self._upstream_response_events = 0
                turn_epoch = self._turn_epoch
            yield
        finally:
            # An ended SDK turn must not leave an old handler writing into the
            # next turn. Cancellation shuts down its sockets to drain promptly.
            with self._handlers_done:
                while turn_epoch is not None and self._handler_counts.get(turn_epoch, 0):
                    self._handlers_done.wait()
                self._session_active = False
                self._session_id = None
            self._session_lock.release()

    def _turn_live_locked(self, epoch: int) -> bool:
        return (
            not self._closed.is_set()
            and epoch == self._turn_epoch
            and epoch != self._cancelled_epoch
            and (epoch == 0 or self._session_active)
        )

    def _turn_live(self, epoch: int) -> bool:
        with self._lock:
            return self._turn_live_locked(epoch)

    @staticmethod
    def _shutdown_socket(upstream_socket: socket.socket) -> None:
        try:
            upstream_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        if os.name == "nt":
            # Winsock shutdown does not reliably wake a synchronous recv in
            # another thread while socket.makefile() still owns the handle.
            # Detach first so its later cleanup cannot close a reused handle.
            handle = upstream_socket.detach()
            if handle != -1:
                ctypes.windll.ws2_32.closesocket(ctypes.c_size_t(handle))
        else:
            upstream_socket.close()

    def cancel_active_turn(self) -> None:
        """Interrupt only this SDK turn's upstream requests, keeping the gateway resident."""
        with self._lock:
            if not self._session_active or self._cancelled_epoch == self._turn_epoch:
                return
            epoch = self._turn_epoch
            self._cancelled_epoch = epoch
            connections = [
                connection for connection in self._connections
                if self._connection_epochs.get(connection) == epoch
            ]
            sockets = [
                self._connection_sockets.get(connection) or connection.sock
                for connection in connections
            ]
        for upstream_socket in sockets:
            if upstream_socket is not None:
                self._shutdown_socket(upstream_socket)

    def _set_error(self, detail: str, epoch: int) -> None:
        with self._lock:
            if self._turn_live_locked(epoch):
                self.error = detail

    def usage_snapshot(self) -> list[dict | None]:
        with self._lock:
            return deepcopy([record["usage"] for record in self._usage_records])

    def raw_snapshot(self) -> list[str]:
        with self._lock:
            return self._redact([record["raw"] for record in self._usage_records])

    def request_snapshot(self) -> list[dict]:
        with self._lock:
            return self._redact(deepcopy(
                [
                    {
                        key: record[key]
                        for key in (
                            "purpose",
                            "max_tokens",
                            "usage",
                            "complete",
                            "raw",
                            "tool_calls",
                            "finish_reason",
                        )
                    }
                    for record in self._usage_records
                ]
            ))

    def _redact(self, value) -> Any:
        secrets_to_hide = [secret for secret in (self._key, self.token, *self._extra_secrets) if secret]
        if isinstance(value, str):
            for secret in secrets_to_hide:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            result = {key: self._redact(item) for key, item in value.items()}
            arguments = value.get("arguments")
            if isinstance(arguments, str) and any(secret in arguments for secret in secrets_to_hide):
                # A credential may cross fragment boundaries. Never expose its
                # pieces merely because each separate piece was not a full key.
                result["argument_fragments"] = ["[REDACTED: credential-containing arguments]"]
                result["arguments_redacted"] = True
            return result
        return value

    def compaction_snapshot(self) -> list[dict]:
        with self._lock:
            return self._redact(deepcopy(
                [record for record in self._usage_records if record["purpose"] == "compaction"]
            ))

    def usage_incomplete(self) -> bool:
        with self._lock:
            return any(not record["complete"] for record in self._usage_records)

    def _note_upstream_activity(self, byte_count: int = 0, *, epoch: int | None = None) -> None:
        """Record bytes received from upstream; never derived from a local timer."""
        received_at = time.monotonic()
        timestamp = datetime.now(UTC).isoformat()
        with self._lock:
            if epoch is not None and not self._turn_live_locked(epoch):
                return
            self._upstream_activity_monotonic = received_at
            self._upstream_activity_utc = timestamp
            self._upstream_response_bytes += max(0, int(byte_count))
            self._upstream_response_events += 1

    def upstream_activity_snapshot(self) -> dict:
        """Last upstream response-byte activity, independent of local heartbeats."""
        with self._lock:
            last_monotonic = self._upstream_activity_monotonic
            return {
                "last_response_utc": self._upstream_activity_utc,
                "last_response_age_seconds": None
                if last_monotonic is None
                else round(max(0.0, time.monotonic() - last_monotonic), 3),
                "response_bytes": self._upstream_response_bytes,
                "response_events": self._upstream_response_events,
                "source": "model_gateway_upstream",
            }

    def fail_compaction(self, detail: str, *, epoch: int | None = None):
        """Fail closed for this turn; the SDK may otherwise warn and continue generation."""
        if self._key:
            detail = detail.replace(self._key, "[REDACTED]")
        detail = detail.replace(self.token, "[REDACTED]")
        with self._lock:
            if epoch is not None and not self._turn_live_locked(epoch):
                return
            if self._compaction_failure is None:
                self._compaction_failure = f"上下文压缩未完成：{detail[:1000]}"
            self.error = self._compaction_failure

    def compaction_failure_snapshot(self) -> dict:
        with self._lock:
            return {
                "compaction_incomplete": self._compaction_failure is not None,
                "compaction_failure": self._compaction_failure,
                "blocked_generation_count": self._blocked_generation_count,
            }

    def _upstream_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._key}",
            "Accept": "text/event-stream",
        }
        if self.settings.provider == "opencode-go":
            with self._lock:
                session_id = self._session_id
            if not session_id:
                raise ValueError("OpenCode Go 请求缺少提取会话标识，未发送模型请求")
            headers["x-opencode-session"] = session_id
            headers["User-Agent"] = "narrative-core/0.1.0"
        return headers

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        with self._lock:
            connections = list(self._connections)
            sockets = [
                self._connection_sockets.get(connection) or connection.sock
                for connection in connections
            ]
        for upstream_socket in sockets:
            if upstream_socket is not None:
                self._shutdown_socket(upstream_socket)
        for connection in connections:
            connection.close()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(1)
        self._key = ""
