"""Local-only smoke test for the GraphRAG budget gate; no Ark request."""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from unittest.mock import patch

from graphrag_bounded_run import MAX_TOTAL_TOKENS, BudgetGate


class FakeUpstream:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}], "usage": {"total_tokens": 12}}).encode()


def post(port: int, token: str) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        body = json.dumps({"model": "qwen3.8:iq3-mtp3", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 10})
        conn.request("POST", "/v1/chat/completions", body, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def main() -> None:
    root = Path(".pytest-tmp-graphrag/gate-smoke")
    root.mkdir(parents=True, exist_ok=True)
    gate = BudgetGate(root, "synthetic-upstream-key")
    server = gate.serve()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("graphrag_bounded_run.urlopen", return_value=FakeUpstream()):
            assert post(server.server_port, gate.local_token) == 200
            assert gate.records[0]["usage"]["total_tokens"] == 12
            assert gate.used_tokens == 12
            gate.used_tokens = MAX_TOTAL_TOKENS
            assert post(server.server_port, gate.local_token) == 429
            assert gate.failed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    seen: list[dict] = []

    def capture(request, timeout):  # noqa: ARG001 - inspect the forwarded request
        seen.append(json.loads(request.data))
        return FakeUpstream()

    gate = BudgetGate(root, max_requests=None, max_total_tokens=None, no_thinking_max_tokens=10)
    server = gate.serve()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("graphrag_bounded_run.urlopen", side_effect=capture):
            assert post(server.server_port, gate.local_token) == 200
            assert seen[0]["reasoning_effort"] == "none"
            assert gate.records[0]["reasoning_effort_injected"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print("local budget gate: 200, usage journal, 429 cap, no-thinking injection")


if __name__ == "__main__":
    main()
