"""Real pinned Harness/native-tool integration against a local model stub."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version

import pytest

from narrative import tool_api
from narrative.harness import SDK_VERSION, HarnessRunner, HarnessSettings
from narrative.operations import Operations
from narrative.task_tool_host import TaskToolHost


def test_pinned_runtime_calls_operations_tool_natively(tmp_path):
    try:
        if version("deepseek-harness-sdk") != SDK_VERSION:
            pytest.skip("The pinned Harness SDK is not installed")
        import deepseek_harness_runtime

        if not deepseek_harness_runtime.bundled_runtime_path().is_file():
            pytest.skip("The pinned bundled runtime is not installed")
    except (ImportError, FileNotFoundError, PackageNotFoundError):
        pytest.skip("The pinned Harness runtime is not installed")

    project = tmp_path / "native-tool.nproj"
    ops = Operations.create_graph_project(project)
    source_text = "甲约乙次日渡河。次日甲接到乙，两人已渡过河。"
    source_id = ops.import_source("原生工具验证", source_text)
    definition = tool_api.TOOLS["read_source"]
    catalog = [{"name": definition.name, "description": definition.summary,
                "parameters": definition.schema()}]
    tool_calls = []
    requests = []
    server_errors = []
    final = json.dumps({"answer": "native read completed"}, ensure_ascii=False)

    def call_tool(name, args):
        tool_calls.append({"name": name, "args": args})
        return tool_api.call(name, args, project=project)

    class ModelStub(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            try:
                if len(requests) == 1:
                    names = {tool["function"]["name"] for tool in request["tools"]}
                    assert "read_source" in names
                    assert "extract_commit" not in names
                    delta = {"role": "assistant", "tool_calls": [{
                        "index": 0, "id": "native-read", "type": "function",
                        "function": {"name": "read_source", "arguments": json.dumps({
                            "source_id": source_id, "start": 0, "length": len(source_text),
                        })},
                    }]}
                    finish = "tool_calls"
                else:
                    assert len(requests) == 2
                    # The actual runtime must execute the native function and put
                    # the Operations observation into its next model request.
                    tool_messages = [row for row in request["messages"] if row.get("role") == "tool"]
                    assert tool_messages
                    assert source_text in json.dumps(tool_messages, ensure_ascii=False)
                    delta = {"role": "assistant", "content": final}
                    finish = "stop"
                chunks = [
                    {"id": "local-native-probe", "object": "chat.completion.chunk", "model": "local-stub",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"id": "local-native-probe", "object": "chat.completion.chunk", "model": "local-stub",
                     "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
                ]
                raw = ("".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks)
                       + "data: [DONE]\n\n").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
            except (AssertionError, KeyError, TypeError) as exc:
                server_errors.append(repr(exc))
                raw = json.dumps({"error": {"message": "Local native-tool assertion failed"}}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()

    model = ThreadingHTTPServer(("127.0.0.1", 0), ModelStub)
    model.daemon_threads = True
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    host = TaskToolHost(catalog, call_tool)
    runner = None
    try:
        settings = HarnessSettings(provider="custom", model="local-stub", reasoning_effort="low",
                                   base_url=f"http://127.0.0.1:{model.server_port}/v1",
                                   max_tokens=4096, timeout_seconds=30)
        runner = HarnessRunner(settings, "local-stub-key", tmp_path / "sdk", task_tools=host.config)
        reply = runner.agent_turn("Use read_source, then return the final JSON.", "native-integration")
        assert not server_errors, server_errors
        assert json.loads(reply.raw) == json.loads(final)
        assert len(requests) == 2
        assert tool_calls == [{"name": "read_source", "args": {
            "source_id": source_id, "start": 0, "length": len(source_text),
        }}]
        assert len(runner.usage_metadata()["model_requests"]) == 2
        assert ops.graph_project_info()["revision"] == 1
    finally:
        if runner is not None:
            runner.close()
        host.close()
        model.shutdown()
        model.server_close()
        thread.join(timeout=2)
