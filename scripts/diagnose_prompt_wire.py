"""Offline real-SDK two-turn wire diagnostic; every model request stays on localhost."""
from __future__ import annotations

import argparse
import copy
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--empty-system", action="store_true", help="One local turn with an explicit empty system override")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ["NARRATIVE_PROVIDER_DB"] = str(root / "providers.sqlite3")
    from narrative import model_gateway, tool_api
    from narrative.assistant import _first_prompt, tool_catalog
    from narrative.chat_store import ChatStore
    from narrative.conversation_runtime import close_project_runtime
    from narrative.conversation_session import open_conversation_session
    from narrative.harness import HarnessRunner, HarnessSettings
    from narrative.operations import Operations
    from narrative.prompt_settings import prompt_snapshot

    def save(name, data):
        (root / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    received, adapted, turns, calls = [], [], [], []
    original_adapt = model_gateway.adapt_request
    def capture(payload, settings, **kwargs):
        result = original_adapt(payload, settings, **kwargs)
        adapted.append({"sdk_payload": copy.deepcopy(payload), "forwarded_payload": copy.deepcopy(result),
                        "configured_system": kwargs.get("system_prompt")})
        return result
    model_gateway.adapt_request = capture
    entry = root / "wire-fixture.nproj"
    ops = Operations.create_graph_project(entry)
    source_id = ops.import_source("短便签", "窗台放着蓝杯，便签校验码QZ-4827。")
    chat = ChatStore(ops.project_info().path).create("本地线缆诊断")
    default_system = prompt_snapshot(ops)["texts"]["system"]
    markers = ["工程提示甲已生效", "工程提示乙已生效"]
    configured_markers = markers[:1] if args.empty_system else markers

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append({"path": self.path, "payload": payload})
            index = len(received)
            if index in (1, 3):
                name = "read_source" if index == 1 else "project_info"
                arguments = {"source_id": source_id} if index == 1 else {}
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"wire-{index}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)}}]}
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": markers[0 if index == 2 else 1] + "。蓝杯，QZ-4827。"}
                finish = "stop"
            chunks = [
                {"id": f"local-{index}", "object": "chat.completion.chunk", "model": "local-stub",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": f"local-{index}", "object": "chat.completion.chunk", "model": "local-stub",
                 "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
            ]
            raw = ("".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = HarnessSettings(provider="custom", model="local-stub", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                               max_tokens=4096, timeout_seconds=10)
    def dispatch(name, arguments):
        calls.append({"name": name, "arguments": arguments})
        return tool_api.call(name, arguments, project=entry)
    try:
        for index, marker in enumerate(configured_markers):
            configured = "" if args.empty_system else default_system + "\n工程自定义格式要求：最终回复的第一行必须逐字为：" + marker
            ops.save_prompt_override("system", configured, ops.load_prompt_settings()["revision"], "本地SDK线缆诊断")
            snapshot = prompt_snapshot(ops)
            wrapper, session = open_conversation_session(ops, settings, "offline-fixture-key", HarnessRunner,
                task_tools=tool_catalog(), tool_handler=dispatch, chat_id=chat["id"], prompt_config=snapshot)
            try:
                message = "读取给定原文并记住事实，source_id=" + source_id if index == 0 else "读取工程概况，回忆上一轮事实，不重读原文。"
                reply = wrapper.agent_turn(_first_prompt(message, prompt_config=snapshot), session)
                turns.append({"session": session, "prompt_config": snapshot, "response": reply.raw,
                              "restart": wrapper.manager.restart_reason, "metadata": reply.metadata})
            finally:
                wrapper.close()
        summary = []
        for index, record in enumerate(adapted):
            def shape(payload):
                result = []
                if "system" in payload:
                    text = json.dumps(payload["system"], ensure_ascii=False)
                    result.append({"role": "top-level-system", "marker_a": markers[0] in text, "marker_b": markers[1] in text, "content": payload["system"]})
                for message in payload.get("messages", []):
                    text = json.dumps(message, ensure_ascii=False)
                    result.append({"role": message.get("role"), "marker_a": markers[0] in text, "marker_b": markers[1] in text,
                                   "content": message.get("content"), "tool_calls": message.get("tool_calls")})
                return result
            summary.append({"request": index + 1, "sdk": shape(record["sdk_payload"]),
                            "forwarded": shape(record["forwarded_payload"]), "http_wire": shape(received[index]["payload"])})
        save("wire-role-summary.json", summary)
        save("report.json", {"kind": "offline-real-sdk-local-http-stub", "external_model_requests": 0,
            "service_request_count": len(received), "empty_system_override": args.empty_system, "same_session": len({turn["session"] for turn in turns}) == 1,
            "turns": turns, "calls": calls})
    finally:
        save("sdk-and-adapted-payloads.json", adapted)
        save("http-wire-payloads.json", received)
        close_project_runtime(ops)
        model_gateway.adapt_request = original_adapt
        server.shutdown()
        server.server_close()
        thread.join(2)
    print(str(root))


if __name__ == "__main__":
    main()
