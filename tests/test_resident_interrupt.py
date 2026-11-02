"""Pinned runtime turn interruption preserves its process and real Session history."""
import threading

import pytest
from test_native_model_diagnostics import require_runtime
from test_unbounded_conversation import streamed_provider

from narrative.harness import HarnessRunner, HarnessSettings
from narrative.task_tool_host import TaskToolHost


def test_pinned_plugin_cancels_one_turn_then_continues_same_pid_and_session(tmp_path):
    require_runtime()
    active, release = threading.Event(), threading.Event()
    def frames(number, request):
        if number == 1:
            yield {"role": "assistant", "content": "中止前正文"}, None, 0
            active.set()
            while not release.wait(.05):
                yield {"content": "。"}, None, 0
            yield {}, "stop", 0
        else:
            assert "UNIQUE-CONTEXT-MARKER" in str(request["messages"])
            yield {"role": "assistant", "content": "同会话继续成功"}, "stop", 0
    with streamed_provider(frames) as (url, requests, _disconnected):
        host = TaskToolHost([], lambda name, args: {})
        runner = HarnessRunner(HarnessSettings(provider="custom", model="local-stub", base_url=url, timeout_seconds=5), "offline", tmp_path / "sdk", task_tools=host.config)
        result = {}
        def work():
            try:
                result["reply"] = runner.agent_turn("UNIQUE-CONTEXT-MARKER", "resident-stop")
            except Exception as error:
                result["error"] = error
        thread = threading.Thread(target=work)
        thread.start()
        try:
            assert active.wait(10)
            process = runner._harness.client._proc
            ack = host.interrupt("resident-stop", "exact-turn-nonce", lambda: runner.active_session == "resident-stop")
            assert ack["accepted"]
            thread.join(5)
            assert not thread.is_alive(), result
            assert "error" not in result, result
            assert result["reply"].metadata["finish_reason"] in {"cancelled", "interrupted", "aborted"}, result["reply"].metadata
            assert runner.turn_settled and process.poll() is None
            reply = runner.agent_turn("继续此前任务上下文", "resident-stop")
            assert reply.raw == "同会话继续成功"
            assert runner._harness.client._proc is process and process.poll() is None
            assert len(requests) == 2
        finally:
            release.set()
            runner.close()
            host.close()
            thread.join(5)


def test_manager_stop_admission_nonce_and_authority_are_exact(tmp_path):
    from narrative.conversation_runtime import close_project_runtime, get_runtime
    from narrative.operations import Operations
    ops = Operations.create_graph_project(tmp_path / "nonce.nproj")
    class Runner:
        def __init__(self, *args, **kwargs):
            self.active_session = None
        def close(self):
            pass
    manager = get_runtime(ops, HarnessSettings(), "offline", runner_factory=Runner)
    try:
        manager.activate("chat", [{"name":"read"}], lambda name,args: args, session_id="sdk-chat")
        nonce = manager._binding["nonce"]
        assert manager._authority("sdk-chat") == nonce
        manager.signal_stop(nonce)
        import pytest
        with pytest.raises(ValueError):
            manager.begin_turn(nonce, "sdk-chat")
        with pytest.raises(ValueError):
            manager._dispatch("read", {}, "sdk-chat", nonce)
        manager.deactivate()
        manager.activate("chat", [{"name":"read"}], lambda name,args: args, session_id="sdk-chat")
        new = manager._binding["nonce"]
        manager.interrupt(nonce)
        assert not manager._binding["cancelled"]
        with pytest.raises(ValueError):
            manager._dispatch("read", {}, "sdk-chat", nonce)
        assert manager._dispatch("read", {"current":True}, "sdk-chat", new) == {"current":True}
    finally:
        close_project_runtime(ops)


def test_expired_control_command_cannot_cancel_later_turn_and_close_wakes_waiter():
    import time
    active = threading.Event()
    active.set()
    host = TaskToolHost([], lambda name,args: {})
    result = {}
    thread = threading.Thread(target=lambda: result.update(host.interrupt("same-session", "old-task", active.is_set)))
    thread.start()
    command = host._controls.get(timeout=2)
    assert host._valid_control(command["id"], "old-task")
    active.clear()
    thread.join(2)
    assert not thread.is_alive() and result["settled"]
    active.set()  # same Session, next task: old command identity remains invalid
    assert not host._valid_control(command["id"], "old-task")
    second = threading.Thread(target=lambda: host.interrupt("same-session", "new-task", active.is_set))
    second.start()
    host._controls.get(timeout=2)
    started = time.monotonic()
    host.close()
    second.join(2)
    assert not second.is_alive() and time.monotonic()-started < 2


def test_stop_between_turn_admission_and_sdk_prompt_is_not_lost(tmp_path):
    require_runtime()
    from narrative.conversation_runtime import close_project_runtime, get_runtime
    from narrative.operations import Operations
    ops = Operations.create_graph_project(tmp_path / "arm-race.nproj")
    blocked, release, stream_done, continuing = threading.Event(), threading.Event(), threading.Event(), threading.Event()
    def frames(number, request):
        if not continuing.is_set():
            yield {"role":"assistant", "content":"可取消的首轮"}, None, 0
            while not stream_done.wait(.05):
                yield {"content":"。"}, None, 0
        else:
            yield {"role":"assistant", "content":"下一轮未被旧停止取消"}, "stop", 0
    with streamed_provider(frames) as (url, _requests, _disconnected):
        manager = get_runtime(ops, HarnessSettings(provider="custom", model="local-stub", base_url=url, timeout_seconds=5), "offline")
        runner = manager.runner
        original = runner._run
        def delayed(*args, **kwargs):
            blocked.set()
            assert release.wait(5)
            return original(*args, **kwargs)
        runner._run = delayed
        manager.activate("chat", [], lambda name,args: {}, session_id="arm-race")
        nonce = manager._binding["nonce"]
        result = {}
        def first():
            manager.begin_turn(nonce, "arm-race")
            result["reply"] = runner.agent_turn("首轮上下文", "arm-race")
        thread = threading.Thread(target=first)
        thread.start()
        stop = threading.Thread(target=lambda: manager.interrupt(nonce))
        try:
            assert blocked.wait(5)
            manager.signal_stop(nonce)
            stop.start()
            assert not manager._interrupt_done.is_set()
            release.set()
            thread.join(8)
            stop.join(8)
            assert not thread.is_alive() and not stop.is_alive()
            assert result["reply"].metadata["finish_reason"] in {"aborted", "interrupted", "cancelled"}
            process = runner._harness.client._proc
            manager.deactivate()
            runner._run = original
            manager.activate("chat", [], lambda name,args: {}, session_id="arm-race")
            new_nonce = manager._binding["nonce"]
            manager.interrupt(nonce)  # late stop for the old turn is inert
            continuing.set()
            manager.begin_turn(new_nonce, "arm-race")
            reply = runner.agent_turn("新轮继续", "arm-race")
            assert reply.raw == "下一轮未被旧停止取消"
            assert runner._harness.client._proc is process and process.poll() is None
        finally:
            release.set()
            stream_done.set()
            close_project_runtime(ops)
            thread.join(5)
            if stop.ident:
                stop.join(5)


@pytest.mark.parametrize("phase", ["resume", "attach"])
def test_stop_during_session_startup_does_not_send_a_prompt_or_close_runtime(tmp_path, monkeypatch, phase):
    from types import SimpleNamespace

    from test_chat_scroll import transcript_shell

    from narrative import native_bridge
    entered, release = threading.Event(), threading.Event()
    instances = []
    class Runner:
        def __init__(self, *args, **kwargs):
            self._closed = False
            self.calls = []
            instances.append(self)
        def resume(self, session):
            if phase == "resume":
                entered.set()
                assert release.wait(5)
            return True
        def agent_turn(self, instruction, session, **kwargs):
            self.calls.append((instruction,session))
            return SimpleNamespace(raw="启动取消后正常继续", metadata={"finish_reason":"completed"})
        def close(self, **kwargs):
            self._closed = True
    monkeypatch.setattr(native_bridge, "HarnessRunner", Runner)
    if phase == "attach":
        original_attach = native_bridge.NativeBridge._attach_conversation_runtime
        def paused_attach(self, *args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original_attach(self, *args, **kwargs)
        monkeypatch.setattr(native_bridge.NativeBridge, "_attach_conversation_runtime", paused_attach)
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.bridge.assistantTurn("启动时停止", "")
        ui.wait(entered.is_set)
        job = next(iter(ui.bridge._assistant_jobs.values()))
        if phase == "attach":
            assert job["runtime"] is None
        ui.bridge.assistantStop(job["chat_id"], job["task_id"])
        release.set()
        ui.wait(lambda:not ui.window.property("busy"))
        assert instances[0].calls == [] and not instances[0]._closed
        assert not ui.bridge._assistant_runtime.closed
        ui.bridge.assistantTurn("继续", "")
        ui.wait(lambda:bool(instances[0].calls))
        ui.wait(lambda:not ui.window.property("busy"))
        assert len(instances) == 1 and len(instances[0].calls) == 1


def test_manager_close_invalidates_pending_interrupt_without_waiting_for_ack(tmp_path):
    import time

    from narrative.conversation_runtime import close_project_runtime, get_runtime
    from narrative.operations import Operations
    ops = Operations.create_graph_project(tmp_path / "close-during-stop.nproj")
    class Runner:
        def __init__(self, *args, **kwargs):
            self.active_session = None
        def close(self):
            self.active_session = None
    manager = get_runtime(ops, HarnessSettings(), "offline", runner_factory=Runner)
    manager.activate("chat", [], lambda name,args: {}, session_id="sdk")
    nonce = manager._binding["nonce"]
    manager.begin_turn(nonce, "sdk")
    stopping = threading.Thread(target=lambda: manager.interrupt(nonce))
    stopping.start()
    manager.host._controls.get(timeout=2)
    started = time.monotonic()
    close_project_runtime(ops)
    stopping.join(2)
    assert not stopping.is_alive() and time.monotonic()-started < 2
    assert manager.closed and manager.host._closed


def test_control_routes_require_host_auth_and_never_enter_model_catalog():
    import http.client
    import json
    from urllib.parse import urlsplit
    host = TaskToolHost([], lambda name,args: {})
    address = urlsplit(host.config["url"])
    try:
        assert host.config["tools"] == []
        for path in ["/control/poll", "/control/claim", "/control/result", "/authority"]:
            connection = http.client.HTTPConnection(address.hostname, address.port, timeout=2)
            connection.request("POST", path, "{}", {"Content-Type":"application/json", "Authorization":"Bearer wrong"})
            response = connection.getresponse()
            assert response.status == 401
            response.read()
            connection.close()
        connection = http.client.HTTPConnection(address.hostname, address.port, timeout=2)
        connection.request("POST", "/call", json.dumps({"name":"session/cancel","args":{}}), {"Authorization":"Bearer "+host.token})
        response = connection.getresponse()
        assert response.status == 400
        response.read()
        connection.close()
    finally:
        host.close()


def test_manager_interrupt_drains_quiet_upstream_before_same_process_continuation(tmp_path):
    require_runtime()
    from narrative.conversation_runtime import close_project_runtime, get_runtime
    from narrative.operations import Operations
    ops = Operations.create_graph_project(tmp_path / "quiet-cancel.nproj")
    quiet, release = threading.Event(), threading.Event()
    def frames(number, request):
        if number == 1:
            yield {"role":"assistant", "content":"连接已进入静默"}, None, 0
            quiet.set()
            release.wait(8)
            yield {"content":"旧连接迟到内容"}, "stop", 0
        else:
            assert "QUIET-SESSION-MARKER" in str(request["messages"])
            yield {"role":"assistant", "content":"静默取消后继续"}, "stop", 0
    with streamed_provider(frames) as (url, requests, _disconnected):
        manager = get_runtime(ops, HarnessSettings(provider="custom", model="local-stub", base_url=url, timeout_seconds=120), "offline")
        manager.activate("chat", [], lambda name,args: {}, session_id="quiet-cancel")
        nonce = manager._binding["nonce"]
        manager.begin_turn(nonce, "quiet-cancel")
        result = {}
        thread = threading.Thread(target=lambda: result.update(reply=manager.runner.agent_turn("QUIET-SESSION-MARKER", "quiet-cancel")))
        thread.start()
        try:
            assert quiet.wait(10)
            process = manager.runner._harness.client._proc
            manager.signal_stop(nonce)
            manager.interrupt(nonce)
            thread.join(5)
            assert not thread.is_alive()
            assert result["reply"].metadata["finish_reason"] in {"aborted", "interrupted", "cancelled"}
            assert not any(manager.runner._gateway._handler_counts.values())
            assert process.poll() is None and not manager.closed
            manager.deactivate()
            manager.activate("chat", [], lambda name,args: {}, session_id="quiet-cancel")
            manager.begin_turn(manager._binding["nonce"], "quiet-cancel")
            reply = manager.runner.agent_turn("继续，不重启", "quiet-cancel")
            assert reply.raw == "静默取消后继续"
            assert manager.runner._harness.client._proc is process
            assert len(requests) == 2 and not manager.runner._gateway.error
        finally:
            release.set()
            close_project_runtime(ops)
            thread.join(5)
