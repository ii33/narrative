"""Provider SSE usage reaches the installed SDK's native meter and compactor.

The model server is local and deterministic; this is not real-model acceptance.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from narrative.harness import HarnessRunner, HarnessSettings
from narrative.messages_bridge import MessagesStream, to_messages_response
from narrative.model_gateway import UsageObserver


def sse(payload):
    return ("data: " + json.dumps(payload) + "\n\n").encode()


@pytest.mark.parametrize("cached", [0, 100000])
def test_final_usage_frame_keeps_input_and_cache_without_double_count(cached):
    raw = {"prompt_tokens": 150000, "completion_tokens": 7, "total_tokens": 150007,
           "prompt_tokens_details": {"cached_tokens": cached},
           "completion_tokens_details": {"reasoning_tokens": 3}}
    wire = (sse({"choices": [{"delta": {"content": "结果"}, "finish_reason": "stop"}]})
            + sse({"choices": [], "usage": raw}) + b"data: [DONE]\n\n")
    converter, observer = MessagesStream("offline"), UsageObserver()
    output = b""
    for offset in range(0, len(wire), 13):
        chunk = wire[offset:offset+13]
        observer.feed(chunk)
        output += converter.feed(chunk)
    output += converter.finish()
    frames = [json.loads(line[6:]) for line in output.decode().splitlines() if line.startswith("data: ")]
    usage = next(frame["usage"] for frame in frames if frame["type"] == "message_delta")
    assert usage["input_tokens"] == 150000-cached
    assert usage.get("cache_read_input_tokens", 0) == cached
    assert "cache_creation_input_tokens" not in usage
    assert sum(usage.values()) == 150007  # reasoning is already in completion_tokens
    assert observer.usage == raw  # provider audit remains unmodified
    assert to_messages_response({"choices": [{"message": {"content": "结果"}}], "usage": raw})["usage"] == usage


@pytest.mark.parametrize("cached", [0, 100000])
def test_installed_sdk_records_usage_and_runs_native_pressure_compaction(tmp_path, cached):
    requests = []
    raw_usage = {"prompt_tokens": 150000, "completion_tokens": 7, "total_tokens": 150007,
                 "prompt_tokens_details": {"cached_tokens": cached}}

    class LocalModel(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            summary = body.get("max_tokens") == 128
            usage = raw_usage if len(requests) <= 2 else {"prompt_tokens": 1000, "completion_tokens": 7, "total_tokens": 1007}
            wire = (sse({"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Earlier records summarized." if summary else "done"}, "finish_reason": "stop"}]})
                    + sse({"choices": [], "usage": usage}) + b"data: [DONE]\n\n")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalModel)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runner = None
    try:
        settings = HarnessSettings(provider="custom", model="offline-usage-meter",
            base_url=f"http://127.0.0.1:{server.server_port}/v1", max_tokens=512,
            context_window=200000, output_reserve=512, compaction_reserve=65536,
            compaction_max_tokens=128, timeout_seconds=15)
        runner = HarnessRunner(settings, "local-only-test", tmp_path / "runtime")
        samples = []
        subscription = runner.usage_events(lambda data, event: samples.append((event, data)))
        subscription.__enter__()
        first = runner.agent_turn("record "*45000, "usage-meter-test")
        assert first.raw == "done" and first.metadata["model_requests"][0]["usage"] == raw_usage
        # One prior message alone is retained verbatim, so no safe useful
        # prefix exists yet. A second completed turn gives the SDK a prefix
        # and a sufficiently large recent tail, without editing its log.
        second = runner.agent_turn("tail "*30000, "usage-meter-test")
        assert second.raw == "done"
        third = runner.agent_turn("finish", "usage-meter-test")
        assert third.raw == "done"
        assert len(requests) == 4 and requests[2]["max_tokens"] == 128
        assert third.metadata["compaction_request_count"] == 1
        assert not third.metadata["compaction_incomplete"]
        assert any(event["type"] == "compaction/start" for event in third.metadata["compaction_events"])
        assert any(event["type"] == "compaction/end" for event in third.metadata["compaction_events"])
        assert any(event == "compaction_start" and data["native_context"]["state"] == "compacting" for event, data in samples)
        assert any(event == "compaction_end" and data["native_context"]["current_estimate_tokens"] is None for event, data in samples)
        assert samples[-1][1]["native_context"]["latest_input_tokens"] == 1000
        assert samples[-1][1]["native_context"]["stale"]
        assert all(set(row) <= {"request_id", "usage_sampled_at", "purpose", "usage", "complete", "finish_reason"} for _, data in samples for row in data["model_requests"])
        assert len(samples) < 30  # step-boundary facts, never one event per token
        subscription.__exit__(None, None, None)
        assert runner._usage_listener is None
        runner.close()  # flush the real SDK's durable session log before reading
        runner = None
        logs = [p for p in (tmp_path / "runtime").rglob("*.jsonl") if p.stat().st_size > 10000]
        events = [json.loads(line) for p in logs for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        usage = next(event["data"]["usage"] for event in events if event.get("type") == "assistant/message")
        assert usage["inputTokens"] == 150000-cached
        assert usage.get("cacheReadTokens", 0) == cached
        assert usage["totalTokens"] == 150007
    finally:
        if runner is not None:
            runner.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
