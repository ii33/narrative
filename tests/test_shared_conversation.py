"""Shared chat/SDK continuity, checked offline against the real pinned runtime."""

from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from agent_fixtures import TEXT, draft_result

from narrative.chat_store import ChatStore
from narrative.harness import SDK_VERSION, HarnessSettings
from narrative.operations import Operations
from narrative.project_package import layout


def test_selected_chat_shares_real_sdk_history_across_roles_and_isolates_other_chats(tmp_path, monkeypatch):
    try:
        if version("deepseek-harness-sdk") != SDK_VERSION:
            pytest.skip("The pinned Harness SDK is not installed")
        import deepseek_harness_runtime

        if not deepseek_harness_runtime.bundled_runtime_path().is_file():
            pytest.skip("The pinned bundled runtime is not installed")
    except (ImportError, FileNotFoundError, PackageNotFoundError):
        pytest.skip("The pinned Harness runtime is not installed")

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("QT_QUICK_BACKEND", "software")
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-stub-placeholder")
    monkeypatch.setenv("ARK_API_KEY", "offline-stub-placeholder")
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication

    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge

    app = QApplication.instance() or QApplication([])
    entry = tmp_path / "shared.nproj"
    ops = Operations.create_graph_project(entry)
    source = "EXTRACTION_MEMORY_MARKER\n" + TEXT
    source_id = ops.import_source("合成跨角色提取", source)
    ops.create_source_slices(source_id, [{"title": "合成接人任务", "start": 0, "end": len(source)}])['result'][0]
    directories = layout(ops.project_info().path)
    # Synthetic legacy audit and runtime data must never be reused or overwritten.
    legacy = directories.extraction / "conversation"
    (legacy / "sdk/home/sessions/old").mkdir(parents=True)
    (legacy / "manifest.json").write_bytes(b'{"legacy":"do not migrate or overwrite"}')
    (legacy / "sdk/home/sessions/old/session.v4.jsonl").write_bytes(b"legacy conversation bytes\n")
    old_files = {path: path.read_bytes() for path in legacy.rglob("*") if path.is_file()}
    requests, server_errors = [], []
    systems = []

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            try:
                number = len(requests)
                messages = json.dumps(request["messages"], ensure_ascii=False)
                native = {tool["function"]["name"] for tool in request["tools"]}
                assert "read_source" in native
                assert {"list_available_tools", "load_tools"} <= native
                assert "apply_storyboard_changes" not in native
                assert not {"list_projects", "create_project"} & native
                system = [row["content"] for row in request["messages"] if row.get("role") == "system"]
                assert system
                systems.append(system)
                if number == 1:
                    assert "CHAT_A_PRIVATE" in messages
                    assert "create_source_slices" not in native and "submit_extraction" not in native
                    answer = {"answer": "A 的聊天记号已记录。"}
                elif number == 2:
                    assert "CHAT_A_PRIVATE" in messages and "source_id" in messages
                    assert "submit_extraction" not in native and "preview_extraction" not in native
                    answer = None
                    delta = {"role": "assistant", "content": "按需加载提取提交合同。", "tool_calls": [{
                        "index": 0, "id": "load-extraction", "type": "function",
                        "function": {"name": "load_tools", "arguments": json.dumps({"names": ["submit_extraction"]})},
                    }]}
                elif number == 3:
                    assert "CHAT_A_PRIVATE" in messages and "submit_extraction" in native
                    assert "preview_extraction" not in native and "create_source_slices" not in native
                    answer = None
                    delta = {"role": "assistant", "content": "已核对原文。", "tool_calls": [{
                        "index": 0, "id": "read-original", "type": "function",
                        "function": {"name": "read_source", "arguments": json.dumps({
                            "source_id": source_id, "start": 0, "length": len(source),
                        }, ensure_ascii=False)},
                    }]}
                elif number == 4:
                    observations = [row for row in request["messages"] if row.get("role") == "tool"]
                    assert observations
                    assert "EXTRACTION_MEMORY_MARKER" in json.dumps(observations[-1], ensure_ascii=False)
                    answer = None
                    delta = {"role": "assistant", "content": "已核对原文。", "tool_calls": [{
                        "index": 0, "id": "submit-draft", "type": "function",
                        "function": {"name": "submit_extraction", "arguments": json.dumps({
                            "source_id": source_id, "result": draft_result(),
                        }, ensure_ascii=False)},
                    }]}
                elif number == 5:
                    assert 'accepted' in messages
                    answer = {"answer": "提取已自动入库。"}
                elif number == 6:
                    assert "CHAT_A_PRIVATE" in messages
                    assert "EXTRACTION_MEMORY_MARKER" in messages
                    assert "CHAT_B_PRIVATE" not in messages
                    assert "submit_extraction" in native
                    assert "create_source_slices" not in native
                    answer = {"answer": "A 可以继续刚才的提取上下文。"}
                elif number == 7:
                    assert "CHAT_B_PRIVATE" in messages
                    assert "submit_extraction" not in native
                    assert "CHAT_A_PRIVATE" not in messages
                    assert "EXTRACTION_MEMORY_MARKER" not in messages
                    answer = {"answer": "B 的上下文独立。"}
                else:
                    assert number == 8
                    assert "submit_extraction" in native
                    assert "CHAT_A_PRIVATE" in messages
                    assert "EXTRACTION_MEMORY_MARKER" in messages
                    assert "CHAT_B_PRIVATE" not in messages
                    answer = {"answer": "切回 A 后上下文仍在。"}
                if answer is not None:
                    delta = {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}
                chunks = [
                    {"id": "shared-local", "object": "chat.completion.chunk", "model": "local-stub",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"id": "shared-local", "object": "chat.completion.chunk", "model": "local-stub",
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if answer is None else "stop"}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
                ]
                raw = ("".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks)
                       + "data: [DONE]\n\n").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
            except (AssertionError, KeyError, TypeError) as exc:
                server_errors.append(repr(exc))
                raw = json.dumps({"error": {"message": "Offline shared-context assertion failed"}}).encode()
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
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    panel = window.findChild(object, "assistantPanel")
    assert panel is not None

    def panel_messages():
        value = panel.property("messages")
        return value.toVariant() if hasattr(value, "toVariant") else value

    def assert_message_coverage(rendered, chat_id):
        raw = ChatStore(ops.project_info().path).load(chat_id)["messages"]
        expected = [row.get("message_id") or f"{chat_id}:history:{i}" for i, row in enumerate(raw)]
        actual = [key for row in rendered for key in row.get("sourceMessageKeys", [row["messageKey"]])]
        assert actual == expected

    def send(text):
        panel.setProperty("draft", text)
        panel.send()

    def invoke(kind, action):
        results.clear()
        errors.clear()
        action()
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            app.processEvents()
            assert not errors, errors
            assert not server_errors, server_errors
            for observed, payload in reversed(results):
                if observed == kind:
                    return payload
            time.sleep(0.01)
        pytest.fail(f"No {kind} response")

    try:
        invoke("project", lambda: bridge.openProject(str(entry)))
        chat_a = invoke("chat", bridge.newChat)["chat"]
        window.setProperty("assistantOpen", True)
        first = invoke("assistant", lambda: send("请记住 CHAT_A_PRIVATE。"))
        assert panel.property("chatId") == chat_a["id"]
        assert_message_coverage(panel_messages(), chat_a["id"])
        before_messages = len(panel_messages())
        runner = bridge._assistant_runner
        assert runner is not None
        process = runner._harness.client._proc
        assert process is not None and process.pid > 0 and process.poll() is None
        pid = process.pid
        host = bridge._assistant_tool_host
        assert host is not None
        from narrative.leases import ProjectLease

        owner_path = directories.extraction / "assistant/runtime-owner.lock"
        with pytest.raises(OSError):
            ProjectLease(owner_path)
        address = urlsplit(host.config["url"])
        connection = http.client.HTTPConnection(address.hostname, address.port, timeout=2)
        try:
            connection.request("POST", "/call", json.dumps({"name": "read_source", "args": {
                "source_id": source_id, "start": 0, "length": 1,
            }}), {"Authorization": "Bearer " + host.token, "Content-Type": "application/json"})
            denied = connection.getresponse()
            assert denied.status == 400
            denied.read()
        finally:
            connection.close()
        window.setProperty("assistantOpen", True)
        extraction = invoke("assistant", lambda: send(f"请从source_id={source_id}读取原文，提取合成接人任务并自动入库。"))
        assert bridge._assistant_runner is runner
        assert runner._harness.client._proc.pid == pid and process.poll() is None
        assert len(ops.source_slices(source_id)) == 1
        assert window.property("assistantOpen") is True
        assert window.findChild(object, "assistantPanel") is panel
        assert len(window.findChildren(object, "assistantPanel")) == 1
        assert panel.property("chatId") == chat_a["id"]
        assert panel.property("visible") is True
        rendered = panel_messages()
        assert len(rendered) > before_messages
        assert_message_coverage(rendered, chat_a["id"])
        assert "提取" in "\n".join(row["text"] for row in rendered)
        assert "合成接人任务" in "\n".join(row["text"] for row in rendered)
        assert ops.graph_project_info()["revision"] > 0
        outputs = [row for row in ChatStore(ops.project_info().path).load(chat_a["id"])["messages"] if row.get("kind") == "model_output"]
        assert outputs and len({row["message_id"] for row in outputs}) == len(outputs)
        assert sum(row['text'] == '已核对原文。' for row in outputs) == 2
        assert [row['seq'] for row in outputs] == sorted(row['seq'] for row in outputs)
        assert all(row.get('full_output') for row in outputs)
        assert all(row.get('display_kind') in {'answer', 'thought'} for row in outputs)
        assert any(row.get('display_kind') == 'thought' for row in outputs)
        metadata = json.loads((Path(extraction['audit_directory']) / 'metadata-1.json').read_text(encoding='utf-8'))
        task_outputs = [row for row in outputs if row['task_id'] == extraction['task_id']]
        assert [row['message_id'] for row in metadata['model_messages']] == [row['message_id'] for row in task_outputs]
        assert [row['full_output'] for row in metadata['model_messages']] == [row['full_output'] for row in task_outputs]
        before_reload = panel_messages()
        invoke('chat', lambda: bridge.openChat(chat_a['id']))
        assert panel_messages() == before_reload
        following = invoke("assistant", lambda: send("继续刚才同一聊天中的提取任务。"))
        assert bridge._assistant_runner is runner
        assert runner._harness.client._proc.pid == pid and process.poll() is None
        assert_message_coverage(panel_messages(), chat_a["id"])
        assert extraction["chat_id"] == chat_a["id"]
        assert first["chat_id"] == following["chat_id"] == chat_a["id"]
        for result in (first, extraction, following):
            audit = json.loads((Path(result["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
            assert audit["session_id"] == chat_a["session"]
        assert len({result["audit_directory"] for result in (first, extraction, following)}) == 3
        chat_b = invoke("chat", bridge.newChat)["chat"]
        assert chat_b["session"] != chat_a["session"]
        invoke("assistant", lambda: send("请记住 CHAT_B_PRIVATE。"))
        assert panel.property("chatId") == chat_b["id"]
        assert bridge._assistant_runner is runner
        assert runner._harness.client._proc.pid == pid and process.poll() is None
        b_messages = panel_messages()
        bridge.resultReady.emit("conversation_progress", {
            "event": "task_result", "chat_id": chat_a["id"], "current": chat_b["id"],
            "project_path": str(entry), "chat": ChatStore(ops.project_info().path).load(chat_a["id"]),
            "items": ChatStore(ops.project_info().path).list(), "busy": False,
        })
        app.processEvents()
        assert panel.property("chatId") == chat_b["id"]
        assert panel_messages() == b_messages
        invoke("chat", lambda: bridge.openChat(chat_a["id"]))
        returned = invoke("assistant", lambda: send("我们回到刚才 A 的上下文。"))
        assert returned["chat_id"] == chat_a["id"]
        assert panel.property("chatId") == chat_a["id"]
        assert bridge._assistant_runner is runner
        assert runner._harness.client._proc.pid == pid and process.poll() is None
        assert len(requests) == 8
        assert all(system == systems[0] for system in systems[1:])
        assert all(path.read_bytes() == content for path, content in old_files.items())
        assert {path for path in legacy.rglob("*") if path.is_file()} == set(old_files)
        panel = ChatStore(ops.project_info().path).load(chat_a["id"])
        assert extraction["task_id"] in json.dumps(panel["messages"], ensure_ascii=False)
        assert source_id in json.dumps(panel["messages"], ensure_ascii=False)
        assert "CHAT_A_PRIVATE" not in json.dumps(ops.graph_snapshot(), ensure_ascii=False)
        assert "CHAT_B_PRIVATE" not in json.dumps(ops.graph_snapshot(), ensure_ascii=False)
        other = tmp_path / "other.nproj"
        Operations.create_graph_project(other)
        invoke("project", lambda: bridge.openProject(str(other)))
        assert bridge._assistant_runner is None
        assert host._closed is True and process.poll() is not None
        # SDK persistence is asynchronous while the shared process is live;
        # the normal shutdown above must flush both independent sessions.
        sdk_sessions = list((directories.extraction / "assistant/home/sessions").rglob("session.v4.jsonl"))
        assert {path.parent.name for path in sdk_sessions} == {chat_a["session"], chat_b["session"]}
        released = ProjectLease(owner_path)
        released.close()
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()
        model.shutdown()
        model.server_close()
        thread.join(timeout=2)


def test_existing_chat_resume_failure_does_not_create_a_replacement_session(tmp_path, monkeypatch):
    from narrative.conversation_session import open_conversation_session, use_chat

    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "restore.nproj")
    ops.import_source("合成原文", TEXT)
    store = ChatStore(ops.project_info().path)
    chat = store.create("已有聊天")
    store.append(chat["id"], "user", "CHAT_RESTORE_MARKER")
    store.append(chat["id"], "assistant", "已有回答")
    calls, resumed = [], []

    class CannotResume:
        def __init__(self, *_args, **_kwargs):
            pass

        def resume(self, session):
            resumed.append(session)
            return False

        def agent_turn(self, instruction, session):
            calls.append(session)
            raise AssertionError("A failed restore must never start a fresh model turn")

        def close(self):
            pass

    with use_chat(ops, chat["id"]), pytest.raises(ValueError, match="恢复"):
        open_conversation_session(ops, HarnessSettings(), "offline-placeholder", CannotResume)
    assert calls == []
    assert resumed == [chat["session"]]
    assert store.load(chat["id"])["session"] == chat["session"]
    assert [row["id"] for row in store.list()] == [chat["id"]]
    assert ops.graph_snapshot()["nodes"] == []


def test_http_submit_retains_originating_chat_after_selection_changes(tmp_path, monkeypatch):
    from narrative import tool_api
    from narrative.conversation_runtime import close_project_runtime, get_runtime
    from narrative.conversation_session import use_chat

    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    ops = Operations.create_graph_project(tmp_path / 'http-origin.nproj')
    sid = ops.import_source('合成原文', TEXT)
    store = ChatStore(ops.project_info().path)
    a, b = store.create('A'), store.create('B')

    class Runner:
        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

    runtime = get_runtime(ops, HarnessSettings(), 'offline', runner_factory=Runner)
    definition = tool_api.TOOLS['submit_extraction']
    catalog = [{'name': definition.name, 'description': definition.summary, 'parameters': definition.schema()}]
    try:
        with use_chat(ops, a['id']):
            with runtime.bind(a['id'], catalog, lambda name, args: tool_api.call(name, args, project=layout(ops.project_info().path).entry)):
                store.set_active(b['id'])
                address = urlsplit(runtime.host.config['url'])
                connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
                try:
                    connection.request('POST', '/call', json.dumps({'session_id': a['id'], 'nonce': runtime._binding['nonce'], 'name': 'submit_extraction', 'args': {
                        'source_id': sid, 'result': draft_result(),
                    }}), {'Authorization': 'Bearer ' + runtime.host.token, 'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    assert response.status == 200, payload
                    assert payload['result']['status'] == 'accepted'
                    assert 'chat_id' not in payload['result']
                finally:
                    connection.close()
        actions = [row['action'] for row in store.load(a['id'])['messages'] if row.get('kind') == 'business_action']
        assert actions and all(row['chat_id'] == a['id'] for row in actions)
        assert 'automatic_extraction' in json.dumps(store.load(a['id']), ensure_ascii=False)
        assert 'automatic_extraction' not in json.dumps(store.load(b['id']), ensure_ascii=False)
    finally:
        close_project_runtime(ops)
