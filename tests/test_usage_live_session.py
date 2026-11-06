"""One real installed local SDK feeds the same usage ledger with or without UI."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from narrative.chat_store import ChatStore
from narrative.conversation_runtime import close_project_runtime
from narrative.conversation_session import open_conversation_session, use_chat
from narrative.harness import HarnessRunner, HarnessSettings
from narrative.operations import Operations


def test_live_counter_samples_replace_turns_and_headless_uses_same_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    requests = []

    class LocalModel(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            raw = {"prompt_tokens": 150, "completion_tokens": 7, "total_tokens": 157,
                   "prompt_tokens_details": {"cached_tokens": 50}}
            frames = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]},
                      {"choices": [], "usage": raw}]
            body = ("".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalModel)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ops = Operations.create_graph_project(tmp_path / "usage-headless.nproj")
    settings = HarnessSettings(provider="custom", model="local-usage", max_tokens=512,
                               base_url=f"http://127.0.0.1:{server.server_port}/v1")
    events = []
    try:
        with use_chat(ops, on_event=events.append):
            wrapper, session = open_conversation_session(ops, settings, "local-only", HarnessRunner, task_id="observed-task")
            chat_id = wrapper.chat_id
            try:
                first = wrapper.agent_turn("respond done", session)
                second = wrapper.agent_turn("respond done again", session)
            finally:
                wrapper.close()
        assert first.metadata["usage_turn_id"] != second.metadata["usage_turn_id"]
        updates = [event["usage_statistics"] for event in events if event.get("event") == "usage_update"]
        assert updates and any(s["task_usage"]["known_requests"] == 1 for s in updates)
        stats = ops.read_usage_statistics(chat_id, "observed-task")
        assert stats["chat_usage"]["requests"] == stats["task_usage"]["requests"] == 2
        assert stats["task_usage"]["input_tokens"] == 300
        assert stats["task_usage"]["cache_read_tokens"] == 100
        assert stats["task_usage"]["total_tokens"] == 314
        assert stats["context"]["last_generation_input_tokens"] == 150
        assert stats["context"]["source"] == "actual" and stats["context"]["stale"]
        # No on_event callback and no desktop bridge: the identical registry
        # still receives and exposes the new task's request exactly once.
        with use_chat(ops, chat_id):
            wrapper, session = open_conversation_session(ops, settings, "local-only", HarnessRunner, task_id="headless-task")
            try:
                wrapper.agent_turn("headless done", session)
            finally:
                wrapper.close()
        stats = ops.read_usage_statistics(chat_id, "headless-task")
        assert stats["task_usage"]["requests"] == 1 and stats["task_usage"]["total_tokens"] == 157
        assert stats["chat_usage"]["requests"] == 3 and stats["chat_usage"]["input_tokens"] == 450
        assert len(requests) == 3
        assert len(ChatStore(ops.project_info().path).list()) == 1
        # The per-turn metadata is durable authority even without desktop task
        # audits. A fresh process can rebuild statistics without the registry.
        from narrative import usage_statistics
        monkeypatch.setattr(usage_statistics, "_LIVE", {})
        monkeypatch.setattr(usage_statistics, "_HISTORY", {})
        restored = ops.read_usage_statistics(chat_id, "headless-task")
        assert restored["chat_usage"]["requests"] == 3
        assert restored["task_usage"]["total_tokens"] == 157
        assert restored["context"]["last_generation_input_tokens"] == 150
    finally:
        close_project_runtime(ops)
        server.shutdown()
        server.server_close()
        thread.join(2)
