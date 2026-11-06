"""Real assistant entry plus pinned SDK/native tools against a loopback stub."""

from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlsplit

import pytest
from current_format_assertions import assert_no_staging

from narrative.harness import SDK_VERSION, HarnessSettings
from narrative.operations import Operations
from narrative.project_package import layout


def contains_value(value, target):
    """Decode SDK tool-result strings without relying on one rendering wrapper."""
    if value == target:
        return True
    if isinstance(value, dict):
        return any(contains_value(item, target) for item in value.values())
    if isinstance(value, list):
        return any(contains_value(item, target) for item in value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return False
        return decoded != value and contains_value(decoded, target)
    return False


def test_bridge_assistant_uses_native_source_tools_and_closes_host_on_project_switch(tmp_path, monkeypatch):
    try:
        if version("deepseek-harness-sdk") != SDK_VERSION:
            pytest.skip("The pinned Harness SDK is not installed")
        import deepseek_harness_runtime

        if not deepseek_harness_runtime.bundled_runtime_path().is_file():
            pytest.skip("The pinned bundled runtime is not installed")
    except (ImportError, FileNotFoundError, PackageNotFoundError):
        pytest.skip("The pinned Harness runtime is not installed")

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-stub-placeholder")
    monkeypatch.setenv("ARK_API_KEY", "local-stub-placeholder")
    from PySide6.QtWidgets import QApplication

    from narrative.native_bridge import NativeBridge

    app = QApplication.instance() or QApplication([])
    entry = tmp_path / "assistant.nproj"
    ops = Operations.create_graph_project(entry)
    text = "第一章 起行\n甲出门。\n第二章 渡河\n甲渡河。\n第三章 抵达\n甲抵达。\n"
    source_id = ops.import_source("合成三章原文", text)
    titles = ["第一章 起行", "第二章 渡河", "第三章 抵达"]
    starts = [text.index(title) for title in titles]
    ends = starts[1:] + [len(text)]
    proposed = [{"title": title, "start": start, "end": end}
                for title, start, end in zip(titles, starts, ends, strict=True)]
    before = ops.graph_snapshot()
    requests, server_errors = [], []
    tool_names = ["list_sources", "read_source", "list_source_slices", "create_source_slices"]
    actions = [
        ("list_sources", {}),
        ("read_source", {"source_id": source_id, "start": 0, "length": len(text)}),
        ("list_source_slices", {"source_id": source_id}),
        ("create_source_slices", {"source_id": source_id, "slices": proposed}),
    ]
    answer = "已按三个章节创建连续切片，未提取或修改图中事实。"

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            try:
                number = len(requests) - 1
                native = {tool["function"]["name"] for tool in request["tools"]}
                assert set(tool_names) <= native
                assert "locate_source" in native
                assert {"read_project_state", "preview_changes", "apply_changes", "add_property", "revise_node"} <= native
                assert not {"list_projects", "create_project"} & native
                if number:
                    observations = [message for message in request["messages"] if message.get("role") == "tool"]
                    assert len(observations) == number
                    if number == 1:
                        assert contains_value(observations[-1], source_id)
                    elif number == 2:
                        assert contains_value(observations[-1], text)
                    elif number == 3:
                        assert contains_value(observations[-1], [])
                    elif number == 4:
                        for title in titles:
                            assert contains_value(observations[-1], title)
                if number < len(actions):
                    name, args = actions[number]
                    delta = {"role": "assistant", "tool_calls": [{
                        "index": 0, "id": f"assistant-tool-{number}", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                    }]}
                    finish = "tool_calls"
                else:
                    assert number == 4
                    delta = {"role": "assistant", "content": answer}
                    finish = "stop"
                chunks = [
                    {"id": "assistant-local", "object": "chat.completion.chunk", "model": "local-stub",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"id": "assistant-local", "object": "chat.completion.chunk", "model": "local-stub",
                     "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
                ]
                raw = ("".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks)
                       + "data: [DONE]\n\n").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
            except (AssertionError, KeyError, TypeError) as exc:
                server_errors.append(repr(exc))
                raw = json.dumps({"error": {"message": "Offline assistant assertion failed"}}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()

    model = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    model.daemon_threads = True
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    ops.save_provider_profile(HarnessSettings(
        provider="custom", model="local-stub", reasoning_effort="low",
        base_url=f"http://127.0.0.1:{model.server_port}/v1", max_tokens=4096, timeout_seconds=30,
    ).model_dump())
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    def wait(kind):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            app.processEvents()
            assert not errors, errors
            for observed, payload in reversed(results):
                if observed == kind:
                    return payload
            time.sleep(0.01)
        pytest.fail(f"No {kind} response; server_errors={server_errors}")

    try:
        bridge.openProject(str(entry))
        wait("project")
        bridge.assistantTurn("请按合成原文的三个章节创建连续切片。", "")
        result = wait("assistant")
        assert not server_errors, server_errors
        assert result["answer"] == answer
        assert len(requests) == 5
        assert [step["tool"] for step in result["steps"]] == tool_names
        assert all(step["ok"] and step["summary"] for step in result["steps"])
        slices = ops.source_slices(source_id)
        assert [{key: row[key] for key in ("title", "start", "end")} for row in slices] == proposed
        assert slices[0]["start"] == 0 and slices[-1]["end"] == len(text)
        assert all(left["end"] == right["start"] for left, right in zip(slices[:-1], slices[1:], strict=True))
        assert ops.graph_snapshot() == before
        assert ops.graph_project_info()["revision"] == 1
        assert_no_staging(ops)
        bridge.openChat(result["chat_id"])
        chat = wait("chat")["chat"]
        saved = [message for message in chat["messages"] if message["role"] == "assistant"][-1]
        assert [step["tool"] for step in saved["steps"]] == tool_names
        host = bridge._assistant_tool_host
        assert host is not None
        address = urlsplit(host.config["url"])
        connection = http.client.HTTPConnection(address.hostname, address.port, timeout=2)
        try:
            connection.request("POST", "/call", json.dumps({"name": "add_property", "args": {}}),
                               {"Authorization": "Bearer " + host.token, "Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 400
            response.read()
        finally:
            connection.close()
        other = tmp_path / "other.nproj"
        Operations.create_graph_project(other)
        results.clear()
        bridge.openProject(str(other))
        wait("project")
        assert bridge._assistant_tool_host is None
        assert host._closed is True
        assert ops.graph_snapshot() == before
    finally:
        bridge.close()
        app.processEvents()
        model.shutdown()
        model.server_close()
        thread.join(timeout=2)


def test_incomplete_assistant_reply_is_audited_without_a_success_message(tmp_path, monkeypatch):
    """Failure-path engineering check; only the first test runs the real SDK."""
    from types import SimpleNamespace

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-placeholder")
    monkeypatch.setenv("ARK_API_KEY", "offline-placeholder")
    from PySide6.QtWidgets import QApplication

    from narrative import native_bridge

    instances = []

    class TruncatedRunner:
        def __init__(self, *_args, **_kwargs):
            self.closed = False
            instances.append(self)

        def resume(self, session):
            return False

        def agent_turn(self, instruction, session, *, on_message=None):
            return SimpleNamespace(raw='{"answer":"incomplete output"}', metadata={
                "finish_reason": "length", "usage": [{"total_tokens": 7}],
            })

        def usage_metadata(self):
            return {"finish_reason": "length", "usage": [{"total_tokens": 7}]}

        def partial_raw(self):
            return ['{"answer":"incomplete output"}']

        def close(self, *, force=False):
            self.closed = True

    monkeypatch.setattr(native_bridge, "HarnessRunner", TruncatedRunner)
    app = QApplication.instance() or QApplication([])
    entry = tmp_path / "failure.nproj"
    ops = Operations.create_graph_project(entry)
    revision = ops.graph_project_info()["revision"]
    bridge = native_bridge.NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    def wait(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        pytest.fail("Offline assistant did not finish")

    try:
        bridge.openProject(str(entry))
        wait(lambda: any(kind == "project" for kind, _ in results))
        bridge.assistantTurn("合成失败路径验证。", "")
        wait(lambda: bool(errors))
        assert errors[0][0] == "assistant" and "length" in errors[0][1]
        assert not any(kind == "assistant" for kind, _ in results)
        assert instances and all(runner.closed for runner in instances)
        assert bridge._assistant_tool_host is None
        directory = layout(ops.project_info().path).extraction / "assistant/tasks"
        task_file = next(directory.glob("*/task.json"))
        task = json.loads(task_file.read_text(encoding="utf-8"))
        assert task["status"] == "incomplete"
        assert task["model_calls"] == 1
        assert "length" in task["error"]
        raw_file = task_file.parent / "raw-1.txt"
        if raw_file.exists():
            assert raw_file.read_text(encoding="utf-8") == '{"answer":"incomplete output"}'
        else:
            partial = json.loads((task_file.parent / "partial-raw-1.json").read_text(encoding="utf-8"))
            assert partial["responses"] == ['{"answer":"incomplete output"}']
        metadata = json.loads((task_file.parent / "metadata-1.json").read_text(encoding="utf-8"))
        assert metadata["usage"] == [{"total_tokens": 7}]
        bridge.loadChats()
        wait(lambda: any(kind == "chats" for kind, _ in results))
        chat_id = next(payload["items"][0]["id"] for kind, payload in results if kind == "chats")
        bridge.openChat(chat_id)
        wait(lambda: any(kind == "chat" for kind, _ in results))
        chat = next(payload["chat"] for kind, payload in results if kind == "chat")
        assert sum(message["role"] == "user" for message in chat["messages"]) == 1
        assert not any(message["role"] == "assistant" for message in chat["messages"])
        assert all(message["role"] in {"user", "system"} for message in chat["messages"])
        assert ops.graph_project_info()["revision"] == revision
    finally:
        bridge.close()
        app.processEvents()


def test_source_locator_returns_all_unicode_boundaries_without_mutation(tmp_path, monkeypatch):
    from narrative import tool_api

    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "coordinates.nproj"
    ops = Operations.create_graph_project(entry)
    title = "第一章 起行"
    text = "😀" + title + "\n甲出门。\n😀" + title + "\n乙出门。"
    source_id = ops.import_source("重复标题合成原文", text)
    before = ops.graph_snapshot()
    result = tool_api.call("locate_source", {"source_id": source_id, "quotes": [title]}, project=entry)
    first = text.index(title)
    second = text.index(title, first + len(title))
    assert result["results"][0]["matches"] == [{"start": start, "end": start + len(title)} for start in (first, second)]
    assert first == 1  # Python codepoints, not UTF-16 units after the emoji.
    assert all(text[row["start"]:row["end"]] == title for row in result["results"][0]["matches"])
    assert "source_hash" not in result
    assert result["results"][0]["count_is_exact"] is True
    assert ops.graph_snapshot() == before
    assert ops.source_slices(source_id) == []


def test_successful_submit_then_final_truncation_keeps_facts_and_refreshes_revision(tmp_path, monkeypatch):
    import http.client
    from types import SimpleNamespace
    from urllib.parse import urlsplit

    from agent_fixtures import TEXT, draft_result
    from PySide6.QtWidgets import QApplication

    from narrative import native_bridge

    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    monkeypatch.setenv('ARK_API_KEY', 'offline-placeholder')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'offline-placeholder')
    ops = Operations.create_graph_project(tmp_path / 'partial-success.nproj')
    sid = ops.import_source('合成原文', TEXT)
    initial = ops.graph_project_info()['revision']

    class Runner:
        def __init__(self, *_args, task_tools=None, **_kwargs):
            self.config = task_tools

        def resume(self, _session):
            return False

        def agent_turn(self, _instruction, _session, *, on_message=None):
            address = urlsplit(self.config['url'])
            connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
            try:
                connection.request('POST', '/authority', json.dumps({'session_id': _session}), {'Authorization': 'Bearer ' + self.config['token'], 'Content-Type': 'application/json'})
                authority = json.loads(connection.getresponse().read())
                connection.request('POST', '/call', json.dumps({'session_id': _session, 'nonce': authority['nonce'], 'name': 'submit_extraction', 'args': {
                    'source_id': sid, 'result': draft_result(),
                }}), {'Authorization': 'Bearer ' + self.config['token'], 'Content-Type': 'application/json'})
                response = connection.getresponse()
                body = json.loads(response.read())
                assert response.status == 200 and body['result']['status'] == 'accepted', body
            finally:
                connection.close()
            return SimpleNamespace(raw='未完成的最终说明', metadata={'finish_reason': 'length', 'usage': []})

        def close(self, **_kwargs):
            pass

    monkeypatch.setattr(native_bridge, 'HarnessRunner', Runner)
    app = QApplication.instance() or QApplication([])
    bridge = native_bridge.NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, text: errors.append((kind, text)))

    def wait(check):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return
            time.sleep(.01)
        pytest.fail('bridge did not finish')

    try:
        bridge.openProject(str(layout(ops.project_info().path).entry))
        wait(lambda: any(k == 'project' for k, _ in results))
        bridge.assistantTurn('合成提交后失败验证', '')
        wait(lambda: errors)
        assert 'length' in errors[0][1], errors
        assert '未入库' not in errors[0][1]
        revision = ops.graph_project_info()['revision']
        assert revision > initial
        project_names = {row['name'] for row in ops.graph_snapshot()['nodes']}
        assert {'甲', '乙'} <= project_names
        assert_no_staging(ops)
        progress = [p for k, p in results if k == 'conversation_progress' and p.get('graph_reset_required')]
        assert progress and progress[-1]['revision'] == revision
        assert bridge._assistant_tool_host is None
    finally:
        bridge.close()
        app.processEvents()
