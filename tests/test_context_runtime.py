"""Context policy tests against the installed, pinned SDK and a local fake provider."""

import json
import math
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from deepseek_harness_runtime import bundled_runtime_path
from pydantic import ValidationError
from test_providers import provider_server as provider_server

from narrative.harness import (
    HarnessRunner,
    HarnessSettings,
    effective_runtime_config,
    runtime_patch,
)
from narrative.model_gateway import COMPACTION_MODEL, adapt_request


def test_context_defaults_and_output_cap_are_independent():
    settings = HarnessSettings()
    assert (
        settings.context_window,
        settings.output_reserve,
        settings.compaction_reserve,
        settings.compaction_max_tokens,
        settings.max_tokens,
    ) == (1_000_000, 131_072, 65_536, 32_768, 131_072)
    assert settings.compaction_threshold == 803_392
    raised = HarnessSettings(output_reserve=512, max_tokens=200_000)
    assert raised.effective_output_reserve == 200_000
    assert raised.compaction_threshold == 734_464
    unlimited = HarnessSettings(max_tokens=None, output_reserve=0, compaction_reserve=0)
    assert unlimited.compaction_threshold == 1_000_000


@pytest.mark.parametrize(
    "field", ["context_window", "output_reserve", "compaction_reserve", "compaction_max_tokens"]
)
@pytest.mark.parametrize("invalid", [True, "1024", 1024.0, -1, None])
def test_context_policy_requires_strict_integers(field, invalid):
    with pytest.raises(ValidationError):
        HarnessSettings(**{field: invalid})


@pytest.mark.parametrize("field", ["context_window", "compaction_max_tokens"])
def test_window_and_summary_output_must_be_positive(field):
    with pytest.raises(ValidationError):
        HarnessSettings(**{field: 0})


def test_reserves_need_positive_input_space_and_summary_cap_is_independent():
    with pytest.raises(ValidationError, match="上下文窗口"):
        HarnessSettings(context_window=200_000, output_reserve=100_000, compaction_reserve=68_928)
    settings = HarnessSettings(compaction_reserve=0, compaction_max_tokens=100_000)
    assert settings.compaction_max_tokens > settings.compaction_reserve
    assert effective_runtime_config(settings)["compaction_threshold_tokens"] == 868_928
    with pytest.raises(ValidationError, match="压缩最大输出"):
        HarnessSettings(compaction_max_tokens=1_000_000)


def test_compaction_request_has_its_own_limit_and_alias_stays_local():
    settings = HarnessSettings(
        provider="openai", model="remote-model", max_tokens=999, compaction_max_tokens=123
    )
    for model, limit in [("remote-model", 999), (COMPACTION_MODEL, 123)]:
        request = adapt_request({"model": model, "max_tokens": 77, "stream": True}, settings)
        assert request["model"] == "remote-model"
        assert request["max_completion_tokens"] == limit
        assert "max_tokens" not in request
    settings = settings.model_copy(update={"max_tokens": None})
    assert "max_completion_tokens" not in adapt_request({"model": "remote-model"}, settings)
    assert adapt_request({"model": COMPACTION_MODEL}, settings)["max_completion_tokens"] == 123


def test_ratio_rounding_keeps_the_integer_trigger_exact():
    settings = HarnessSettings(
        context_window=22,
        max_tokens=3,
        output_reserve=3,
        compaction_reserve=4,
        compaction_max_tokens=2,
    )
    ratio = runtime_patch(settings)[-1]["insert"][-1]["config"]["thresholdRatio"]
    assert math.floor(settings.context_window * ratio) == 15


@pytest.mark.parametrize(
    "optional_limits", [{}, {"max_tokens": None, "output_reserve": 0, "compaction_reserve": 0}]
)
def test_runtime_composes_context_plugins_without_restoring_shell_or_retry(
    tmp_path, optional_limits
):
    settings = HarnessSettings(model="deepseek-v4-flash", context_window=999_983, **optional_limits)
    runner = HarnessRunner(settings, "test-only-secret", tmp_path)
    try:
        patch_path = runner._harness.config.patches[-1]
        process = subprocess.run(
            [
                str(bundled_runtime_path()),
                "--profile",
                "sdk-minimal",
                "--patch",
                runner._harness.config.patches[0],
                "--patch",
                patch_path,
                "--dump-config",
            ],
            env={**os.environ, "DSH_HOME": str(tmp_path / "dump-home")},
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        config = process.stdout
        assert "test-only-secret" not in config
        assert "name: '@deepseek-ai/dsh-compaction-basic'" in config
        assert "name: '@deepseek-ai/dsh-token-meter'" in config
        assert f"defaultContextWindow: {settings.context_window}" in config
        assert "maxOverflowRetries: 0" in config and "compactionRetries: 0" in config
        for plugin in ("persistent-bash", "persistent-pwsh", "llm-retry", "session-log-deepseek"):
            block = config.split(f"- id: {plugin}\n", 1)[1].split("\n- id:", 1)[0]
            assert "disabled: true" in block
        assert "maxTokensAsSuccess: false" in config
        # Boot validates injection dependencies and supported plugin config, without a model call.
        runner._harness.start()
        assert runner.usage_metadata()["gateway_upstream_attempt_count"] == 0
        policy = effective_runtime_config(settings)
        assert (
            math.floor(settings.context_window * policy["threshold_ratio"])
            == settings.compaction_threshold
        )
        assert (
            runtime_patch(settings)[0]["config"]["models"][0]["contextWindow"]
            == settings.context_window
        )
    finally:
        runner.close()


@pytest.mark.parametrize(
    "window,reserve,headroom,main_cap,summary_cap",
    [
        (256, 128, 116, 128, 77),
        (1_000_000, 999_000, 988, 131_072, 32_768),
    ],
)
def test_real_runtime_compaction_routes_separate_cap_and_records_all_usage(
    tmp_path, provider_server, window, reserve, headroom, main_cap, summary_cap
):
    address, received, policy = provider_server
    policy["usage"] = {"prompt_tokens": 5_000, "completion_tokens": 12, "total_tokens": 5_012}
    settings = HarnessSettings(
        provider="custom",
        base_url=address,
        model="remote-model",
        max_tokens=main_cap,
        context_window=window,
        output_reserve=reserve,
        compaction_reserve=headroom,
        compaction_max_tokens=summary_cap,
    )
    runner = HarnessRunner(settings, "test-only-secret", tmp_path)
    try:
        first = runner.agent_turn("history " * 2000, "context-test")
        assert first.raw and len(received) == 1
        second = runner.agent_turn("continue", "context-test")
        assert second.raw
        metadata = second.metadata
        assert metadata["compaction_request_count"] == 1
        assert metadata["gateway_upstream_attempt_count"] == 2
        assert [row["purpose"] for row in metadata["model_requests"]] == [
            "compaction",
            "generation",
        ]
        assert [row["body"]["max_tokens"] for row in received] == [main_cap, summary_cap, main_cap]
        assert all(row["body"]["model"] == "remote-model" for row in received)
        assert len(metadata["usage"]) == 2
        assert [row["type"] for row in metadata["compaction_events"]] == [
            "compaction/start",
            "compaction/summary",
            "compaction/end",
        ]
        assert all(
            row["raw"] and row["complete"] and row["finish_reason"] == "stop"
            for row in metadata["model_requests"]
        )
        assert "test-only-secret" not in json.dumps(metadata)
    finally:
        runner.close()


@pytest.fixture
def failing_compaction_server():
    received = []
    control = {"mode": "length"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(body)
            compaction = body.get("max_tokens") == 77
            if compaction and control["mode"] == "http":
                wire = b'{"error":{"message":"summary rejected test-only-secret"}}'
                self.send_response(400)
                self.send_header("Content-Length", str(len(wire)))
                self.end_headers()
                self.wfile.write(wire)
                return
            text = (
                "partial summary"
                if compaction
                else json.dumps({"plan": "history", "actions": [], "stage": [], "done": False})
            )
            frames = [
                {
                    "id": "fake",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                },
                {
                    "id": "fake",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "length" if compaction else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 5000, "completion_tokens": 12, "total_tokens": 5012},
                },
            ]
            wire = (
                "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", received, control
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("mode", ["length", "http"])
def test_failed_summary_blocks_generation_and_graph_candidate(
    tmp_path, monkeypatch, failing_compaction_server, mode
):
    from narrative import graph_agent
    from narrative.graph_operations import GraphOperations

    address, received, control = failing_compaction_server
    control["mode"] = mode
    settings = HarnessSettings(
        provider="custom",
        base_url=address,
        model="remote-model",
        max_tokens=128,
        context_window=256,
        output_reserve=128,
        compaction_reserve=116,
        compaction_max_tokens=77,
    )
    runner = HarnessRunner(settings, "test-only-secret", tmp_path / "sdk")
    try:
        assert runner.agent_turn("history " * 2000, "failure-test").raw
        reply = runner.agent_turn("continue", "failure-test")
        assert reply.metadata["finish_reason"] == "error"
        assert reply.metadata["compaction_incomplete"] is True
        assert "压缩未完成" in reply.metadata["service_error"]
        assert reply.metadata["blocked_generation_count"] == 1
        assert (
            len(received) == 2
        )  # First-turn generation and one failed summary; no retry/root call.
        assert reply.metadata["gateway_upstream_attempt_count"] == 1
        assert reply.metadata["compaction_request_count"] == 1
        request = reply.metadata["model_requests"][0]
        assert request["purpose"] == "compaction" and request["raw"]
        assert request["finish_reason"] == ("length" if mode == "length" else None)
        assert request["usage"] is not None if mode == "length" else request["usage"] is None
        assert "test-only-secret" not in json.dumps(reply.metadata)
        assert [event["type"] for event in reply.metadata["compaction_events"]] == [
            "compaction/start",
            "compaction/end",
        ]
    finally:
        runner.close()

    # Exercise the Operations job's existing failure gate with this actual SDK result.
    class RecordedFailureRunner:
        def __init__(self, *args):
            pass

        def agent_turn(self, instruction, session_id):
            return reply

        def close(self):
            pass

        def redact(self, message):
            return message

    monkeypatch.setattr(graph_agent, "HarnessRunner", RecordedFailureRunner)
    monkeypatch.setattr(graph_agent, "resolve_key", lambda *args: "test-placeholder")
    operations = GraphOperations(tmp_path / "graph.sqlite3", create=True)
    source = operations.import_source("隔离短篇", "甲来了。")
    run_id = operations.start_graph_analysis([source])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = operations.graph_analysis_status(run_id)
        if state["status"] not in {"queued", "running"}:
            break
        time.sleep(0.05)
    assert state["status"] == "incomplete"
    assert state["candidate_ids"] == []
    assert state["rounds"] == 1
    assert state["steps"][0]["finish_reason"] == "error"
    assert "压缩未完成" in state["error"]
