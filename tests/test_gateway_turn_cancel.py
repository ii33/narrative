"""A manual stop interrupts only the active gateway turn's upstream socket."""

import http.client
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from narrative.harness import HarnessSettings
from narrative.model_gateway import COMPACTION_MODEL, ModelGateway


def _frame(value):
    return ("data: " + json.dumps(value) + "\n\n").encode()


COMPLETE = (
    _frame({"choices": [{"delta": {"content": "next"}, "finish_reason": "stop"}]})
    + _frame({"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 1}})
    + b"data: [DONE]\n\n"
)


@contextmanager
def _upstream(first_chunk: bytes | None):
    first_seen = threading.Event()
    release_first = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if len(requests) == 1:
                if first_chunk is not None:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(first_chunk)
                    self.wfile.flush()
                first_seen.set()
                release_first.wait(5)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(COMPLETE)))
            self.end_headers()
            self.wfile.write(COMPLETE)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, first_seen, release_first, requests
    finally:
        release_first.set()
        server.shutdown()
        server.server_close()
        thread.join(1)


def _local_request(gateway, model, result):
    connection = http.client.HTTPConnection("127.0.0.1", gateway._server.server_port, timeout=4)
    try:
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": "test"}],
                           "stream": True}).encode()
        connection.request("POST", "/v1/chat/completions", body,
                           {"Authorization": f"Bearer {gateway.token}",
                            "Content-Type": "application/json"})
        response = connection.getresponse()
        result["status"] = response.status
        result["body"] = response.read()
    except (OSError, http.client.HTTPException) as exc:
        result["error"] = type(exc).__name__
    finally:
        connection.close()


def _wait_until(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("local HTTP gateway did not reach the expected state")


@pytest.mark.parametrize("first_chunk", [
    _frame({"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]})
    + _frame({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}),
    None,
])
def test_cancel_stalled_upstream_and_reuse_gateway_without_old_turn_pollution(first_chunk):
    with _upstream(first_chunk) as (port, first_seen, release_first, requests):
        settings = HarnessSettings(provider="custom", base_url=f"http://127.0.0.1:{port}/v1",
                                   model="stub", timeout_seconds=5)
        gateway = ModelGateway(settings, "stub-key")
        try:
            gateway.cancel_active_turn()  # No active turn.
            first_result = {}
            with gateway.session("first"):
                client = threading.Thread(target=_local_request,
                                          args=(gateway, COMPACTION_MODEL, first_result))
                client.start()
                assert first_seen.wait(3)
                _wait_until(lambda: len(gateway.request_snapshot()) == 1)
                if first_chunk is not None:
                    _wait_until(lambda: gateway.raw_snapshot() == ["partial"])
                    assert gateway.usage_snapshot() == [{"prompt_tokens": 7,
                                                        "completion_tokens": 2}]
                else:
                    assert gateway.usage_snapshot() == [None]
                before = gateway.request_snapshot()[0]
                gateway.cancel_active_turn()
                gateway.cancel_active_turn()  # Idempotent for the same turn.
                client.join(3)
                assert not client.is_alive(), "cancel must unblock read1/getresponse promptly"
                assert gateway.request_snapshot()[0]["raw"] == before["raw"]
                assert gateway.usage_snapshot()[0] == before["usage"]
                assert gateway.compaction_failure_snapshot()["compaction_incomplete"] is False
                rejected = {}
                _local_request(gateway, "stub", rejected)
                assert len(requests) == 1, "cancelled turn must not issue another upstream request"
            # The first upstream handler is still stalled deliberately. Its
            # connection was closed, so the resident gateway can serve a new turn.
            assert not release_first.is_set()
            with gateway.session("second"):
                second_result = {}
                _local_request(gateway, "stub", second_result)
                assert second_result["status"] == 200
                assert b"next" in second_result["body"]
                assert gateway.raw_snapshot() == ["next"]
                assert gateway.usage_snapshot() == [{"prompt_tokens": 2,
                                                    "completion_tokens": 1}]
                assert gateway.error is None
                assert gateway.compaction_failure_snapshot()["compaction_incomplete"] is False
                activity = gateway.upstream_activity_snapshot()
                assert activity["response_events"] >= 2
                gateway._set_error("late old error", 1)
                gateway.fail_compaction("late old failure", epoch=1)
                gateway._note_upstream_activity(999, epoch=1)
                assert gateway.error is None
                assert gateway.compaction_failure_snapshot()["compaction_incomplete"] is False
                assert gateway.upstream_activity_snapshot()["response_events"] == activity["response_events"]
            assert len(requests) == 2
            gateway.cancel_active_turn()  # A stale stop cannot affect the next turn.
        finally:
            gateway.close()
