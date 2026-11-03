"""Ordinary resident-chat cancellation reports committed screenplay writes accurately."""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from test_chat_scroll import transcript_shell
from test_screenplay_ui import create_fixture


def test_ordinary_assistant_append_then_cancel_keeps_copy_body_and_resident_session(tmp_path, monkeypatch):
    from narrative import native_bridge
    from narrative.conversation_runtime import get_existing_runtime
    from narrative.harness import HarnessSettings
    from narrative.operations import Operations

    appended, release = threading.Event(), threading.Event()
    instances = []
    append_arguments = {}

    class Runner:
        def __init__(self, *args, **kwargs):
            self._closed = False
            self.active_session = None
            self.calls = []
            instances.append(self)

        def resume(self, session):
            return True

        def agent_turn(self, instruction, session, **kwargs):
            self.calls.append((instruction, session))
            try:
                if len(self.calls) == 1:
                    runtime = get_existing_runtime(ops)
                    address = urlsplit(runtime.host.config["url"])
                    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
                    try:
                        connection.request("POST", "/call", json.dumps({
                            "session_id": session, "nonce": runtime._binding["nonce"],
                            "name": "append_screenplay_timeline", "args": append_arguments,
                        }), {"Authorization": "Bearer " + runtime.host.token, "Content-Type": "application/json"})
                        response = connection.getresponse()
                        result = json.loads(response.read())
                        assert response.status == 200, result
                        assert result["result"]["status"] == "accepted"
                    finally:
                        connection.close()
                    appended.set()
                    assert release.wait(5)
                    return SimpleNamespace(raw="", metadata={"finish_reason": "cancelled"})
                return SimpleNamespace(raw="仍在同一会话中继续。", metadata={"finish_reason": "completed"})
            finally:
                self.active_session = None

        def close(self, **kwargs):
            self._closed = True

    monkeypatch.setattr(native_bridge, "HarnessRunner", Runner)
    monkeypatch.setenv("NARRATIVE_APPEND_CANCEL_TEST_KEY", "offline-stub")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        ops.save_provider_profile(HarnessSettings(provider="custom", model="offline-stub",
            base_url="http://127.0.0.1:1/v1", key_env="NARRATIVE_APPEND_CANCEL_TEST_KEY").model_dump())
        screenplay_id = create_fixture(ops)
        segment = ops.read_screenplay(screenplay_id)["segments"][0]
        segment = ops.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {
            "plan": {"title": "桥头", "summary": "甲停步回头。"}}}], segment["outline_revision"], reason="取消回归场表")
        unit_id = segment["units"][0]["id"]
        ops.apply_script_block_changes(unit_id, [{"op": "insert", "block": {
            "type": "action", "payload": {"text": "这是取消前已经手工编辑的正文。"}}}], [], 0, reason="取消回归正文")
        old_units = ops.read_script_segment(segment["id"])["units"]
        source = ops.adaptation_snapshot()
        scene_id = next(n["id"] for n in source["nodes"] if n["node_type"] == "scene")
        source = ops.edit_adaptation_node(changes={"node_type": "event", "name": "乙来到桥头"}, parent_id=scene_id)
        new_event = next(n["id"] for n in source["nodes"] if n["name"] == "乙来到桥头")
        local_before = ops.read_screenplay_timeline(screenplay_id)
        graph_revision = ops.graph_project_info()["revision"]
        append_arguments.update(screenplay_id=screenplay_id, moment_ids=[new_event],
            expected_timeline_revision=local_before["revision"], expected_adaptation_revision=source["revision"],
            reason="普通聊天追加后手动停止")
        ui.bridge.assistantTurn("将指定新时间点追加到这套剧本。", "")
        try:
            ui.wait(appended.is_set)
            job = next(iter(ui.bridge._assistant_jobs.values()))
            runtime = ui.bridge._assistant_runtime
            ui.bridge.assistantStop(job["chat_id"], job["task_id"])
        finally:
            release.set()
        ui.wait(lambda: any(kind == "assistant" and payload.get("task_id") == job["task_id"]
                            for kind, payload in ui.results))
        result = next(payload for kind, payload in ui.results
                      if kind == "assistant" and payload.get("task_id") == job["task_id"])
        assert result["status"] == "cancelled"
        assert ops.graph_project_info()["revision"] == graph_revision
        assert any(n["id"] == new_event for n in ops.read_screenplay_timeline(screenplay_id)["nodes"])
        assert ops.read_script_segment(segment["id"])["units"] == old_units
        messages = ui.store.load(job["chat_id"])["messages"]
        stopped = next(m for m in reversed(messages) if m.get("kind") == "assistant_cancelled")
        assert "1 项剧本写入仍保留" in stopped["text"]
        assert "未产生新的入库变更" not in stopped["text"]
        audit = json.loads((Path(result["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
        assert "writing_task" not in audit
        assert audit["status"] == "cancelled"
        assert [step["tool"] for step in audit["completed_screenplay_actions"]] == ["append_screenplay_timeline"]
        assert runtime is ui.bridge._assistant_runtime and not runtime.closed and not instances[0]._closed
        ui.bridge.assistantTurn("继续同一上下文。", "")
        ui.wait(lambda: len(instances[0].calls) == 2)
        ui.wait(lambda: not ui.window.property("busy"))
        assert len(instances) == 1
        assert instances[0].calls[0][1] == instances[0].calls[1][1]
        assert not ui.errors
