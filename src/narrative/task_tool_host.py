"""Task-scoped authenticated Operations-tool service for the local Harness plugin."""

from __future__ import annotations

import hmac
import json
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class TaskToolHost:
    """Expose only the supplied task handler, never filesystem or shell access."""

    def __init__(self, catalog: list[dict], handler, *, redact=None, authority=None):
        self.token = secrets.token_urlsafe(32)
        self._closed = False
        self._controls = queue.Queue()
        self._control_waiters = {}
        self._control_lock = threading.Lock()
        host = self
        allowed = {item["name"] for item in catalog}
        token = self.token

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size < 0:
                        raise ValueError("Invalid request size")
                except ValueError:
                    self.send_error(400)
                    return
                # Drain the body before closing a rejected request. Leaving it
                # unread can reset the Windows TCP connection before 401 arrives.
                raw_request = self.rfile.read(size)
                authorization = self.headers.get("Authorization", "")
                if not hmac.compare_digest(authorization, "Bearer " + token):
                    self.send_error(401)
                    return
                if self.path in {"/control/poll", "/control/result", "/control/claim", "/authority"}:
                    try:
                        request = json.loads(raw_request)
                        if self.path == "/authority":
                            result = {"nonce": authority(str(request.get("session_id") or ""))} if authority else {"nonce": None}
                        elif self.path == "/control/poll":
                            try:
                                result = host._controls.get(timeout=25)
                            except queue.Empty:
                                result = None
                            if result and not host._valid_control(result["id"]):
                                result = None
                        elif self.path == "/control/claim":
                            result = {"active": host._valid_control(request.get("id"), request.get("nonce"))}
                        else:
                            with host._control_lock:
                                waiter = host._control_waiters.get(request.get("id"))
                                if waiter and request.get("nonce") == waiter["nonce"]:
                                    waiter["result"] = request
                                    waiter["event"].set()
                            result = {"accepted": True}
                        raw = json.dumps(result).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        pass
                    except Exception:
                        self.send_error(400)
                    return
                if self.path != "/call":
                    self.send_error(404)
                    return
                status = 200
                try:
                    request = json.loads(raw_request)
                    name = str(request.get("name") or "")
                    args = request.get("args") or {}
                    if name not in allowed:
                        raise ValueError("Tool is not available in this task")
                    if not isinstance(args, dict):
                        raise ValueError("Tool args must be an object")
                    result = {"ok": True, "result": handler(name, args, request.get("session_id"), request.get("nonce")) if authority else handler(name, args)}
                except Exception as exc:
                    status = 400
                    message = str(exc).replace(token, "[REDACTED]")
                    if redact is not None:
                        message = redact(message)
                    result = {"ok": False, "error": message, "error_type": type(exc).__name__}
                raw = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05), daemon=True,
        )
        self._thread.start()
        self.config = {
            "url": f"http://127.0.0.1:{self._server.server_port}",
            "token": token,
            "tools": catalog,
        }

    def _valid_control(self, identifier, nonce=None) -> bool:
        with self._control_lock:
            waiter = self._control_waiters.get(identifier)
            return bool(not self._closed and waiter and (nonce is None or nonce == waiter["nonce"])
                        and waiter["active"]())

    def interrupt(self, session_id: str, nonce: str, active) -> dict:
        """Request one exact active turn; keep waiting only while that turn exists."""
        identifier = secrets.token_hex(12)
        waiter = {"event": threading.Event(), "result": None, "nonce": nonce, "active": active}
        with self._control_lock:
            if self._closed:
                raise ValueError("工具宿主已关闭")
            self._control_waiters[identifier] = waiter
        self._controls.put({"id": identifier, "session_id": session_id, "nonce": nonce})
        deadline = time.monotonic() + 10  # Local control transport, never a model task deadline.
        try:
            while not waiter["event"].wait(.1):
                if time.monotonic() >= deadline:
                    raise TimeoutError("运行时停止控制通道未响应")
                if not self._valid_control(identifier):
                    return {"accepted": False, "settled": True}
            result = waiter["result"]
            if not result or not result.get("accepted"):
                if not active():
                    return {"accepted": False, "settled": True}
                raise ValueError((result or {}).get("error") or "运行时未接受停止请求")
            return result
        finally:
            with self._control_lock:
                self._control_waiters.pop(identifier, None)

    def close(self):
        if not self._closed:
            self._closed = True
            self._controls.put(None)
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=2)
