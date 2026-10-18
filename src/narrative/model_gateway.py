"""Local, authenticated wire adapter between DSH and Chat Completions services.

Translates request parameters and provider session headers. It never retries, follows redirects, stores
prompts or credentials, or changes the model response. The SDK still owns turns.
"""

import http.client
import json
import re
import secrets
import socket
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

COMPACTION_MODEL = "__narrative_compaction__"


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
    if settings.reasoning_effort:
        body["reasoning_effort"] = settings.reasoning_effort
    body["model"] = settings.model
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
        # Usage frames are small; never retain an unbounded malformed event.
        if len(self.buffer) > 1024 * 1024:
            self.buffer = b""


class ModelGateway:
    def __init__(self, settings, api_key: str):
        self.settings, self._key = settings, api_key
        self.token = secrets.token_urlsafe(32)
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._session_lock = threading.Lock()
        self._session_id: str | None = None
        self._usage_records: list[dict] = []
        self._compaction_failure: str | None = None
        self._blocked_generation_count = 0
        self._connections: set[http.client.HTTPConnection] = set()
        self.error: str | None = None
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
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                if not secrets.compare_digest(
                    self.headers.get("Authorization", ""), f"Bearer {gateway.token}"
                ):
                    self._drain_short_unauthorized_body()
                    self.send_error(401)
                    return
                sent_headers = False
                connection = None
                purpose = None
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 16 * 1024 * 1024:
                        raise ValueError("模型请求大小不合法")
                    payload = json.loads(self.rfile.read(size))
                    if not isinstance(payload, dict):
                        raise ValueError("模型请求须为对象")
                    purpose = (
                        "compaction" if payload.get("model") == COMPACTION_MODEL else "generation"
                    )
                    with gateway._lock:
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
                        http.client.HTTPSConnection
                        if url.scheme == "https"
                        else http.client.HTTPConnection
                    )
                    connection = connection_type(
                        url.hostname, url.port, timeout=settings.timeout_seconds
                    )
                    with gateway._lock:
                        if gateway._closed.is_set():
                            raise ValueError("模型调用已停止")
                        gateway._connections.add(connection)
                        usage_record: dict = {
                            "usage": None,
                            "complete": False,
                            "raw": "",
                            "purpose": purpose,
                            "finish_reason": None,
                            "max_tokens": settings.compaction_max_tokens
                            if purpose == "compaction"
                            else settings.max_tokens,
                        }
                        gateway._usage_records.append(usage_record)
                    connection.connect()
                    if gateway._closed.is_set():
                        raise ValueError("模型调用已停止")
                    connection.request(
                        "POST",
                        url.path.rstrip("/") + "/chat/completions",
                        body,
                        headers,
                    )
                    response = connection.getresponse()
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
                        gateway.error = f"HTTP {status}：{str(detail)[:1000]}"
                        if purpose == "compaction":
                            gateway.fail_compaction(gateway.error)
                        self.wfile.write(error)
                    else:
                        observer = UsageObserver()
                        while not gateway._closed.is_set():
                            chunk = response.read1(65536)
                            if not chunk:
                                break
                            observer.feed(chunk)
                            if purpose == "compaction" and observer.finish_reason not in {
                                None,
                                "stop",
                            }:
                                gateway.fail_compaction(
                                    f"摘要流未完整结束（finish_reason={observer.finish_reason}）"
                                )
                            with gateway._lock:
                                usage_record["usage"] = observer.usage
                                usage_record["complete"] = observer.complete
                                usage_record["raw"] = "".join(observer.content)
                                usage_record["finish_reason"] = observer.finish_reason
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        observer.feed(b"\n\n")
                        with gateway._lock:
                            usage_record["usage"] = observer.usage
                            usage_record["complete"] = observer.complete
                            usage_record["raw"] = "".join(observer.content)
                            usage_record["finish_reason"] = observer.finish_reason
                        if purpose == "compaction" and (
                            not observer.complete or observer.finish_reason != "stop"
                        ):
                            gateway.fail_compaction(
                                f"摘要流未完整结束（finish_reason={observer.finish_reason or 'unknown'}，complete={observer.complete}）"
                            )
                except (OSError, ValueError, http.client.HTTPException) as exc:
                    if purpose == "compaction":
                        gateway.fail_compaction(str(exc))
                    if not sent_headers:
                        message = (
                            str(exc).replace(gateway._key, "[REDACTED]")
                            if gateway._key
                            else str(exc)
                        )
                        gateway.error = message[:1000]
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
                    if connection:
                        with gateway._lock:
                            gateway._connections.discard(connection)
                        connection.close()

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
        try:
            with self._lock:
                self._session_id = session_id
                self.error = None
                self._usage_records = []
                self._compaction_failure = None
                self._blocked_generation_count = 0
            yield
        finally:
            with self._lock:
                self._session_id = None
            self._session_lock.release()

    def usage_snapshot(self) -> list[dict | None]:
        with self._lock:
            return deepcopy([record["usage"] for record in self._usage_records])

    def raw_snapshot(self) -> list[str]:
        with self._lock:
            return [record["raw"] for record in self._usage_records]

    def request_snapshot(self) -> list[dict]:
        with self._lock:
            return deepcopy(
                [
                    {
                        key: record[key]
                        for key in (
                            "purpose",
                            "max_tokens",
                            "usage",
                            "complete",
                            "raw",
                            "finish_reason",
                        )
                    }
                    for record in self._usage_records
                ]
            )

    def compaction_snapshot(self) -> list[dict]:
        with self._lock:
            return deepcopy(
                [record for record in self._usage_records if record["purpose"] == "compaction"]
            )

    def usage_incomplete(self) -> bool:
        with self._lock:
            return any(not record["complete"] for record in self._usage_records)

    def fail_compaction(self, detail: str):
        """Fail closed for this turn; the SDK may otherwise warn and continue generation."""
        if self._key:
            detail = detail.replace(self._key, "[REDACTED]")
        detail = detail.replace(self.token, "[REDACTED]")
        with self._lock:
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
            for connection in self._connections:
                if connection.sock:
                    try:
                        connection.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                connection.close()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(1)
        self._key = ""
