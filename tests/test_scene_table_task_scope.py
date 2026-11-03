"""The real resident host cannot bypass a scene-table task's narrow source scope."""
from __future__ import annotations

import copy
import http.client
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from test_tool_fingerprint_refs import prepare

from narrative import native_bridge
from narrative.assistant import tool_catalog
from narrative.operations import Operations
from narrative.screenplay_store import read_context
from narrative.screenplay_writing import validate_script_writing_tool_call

ALLOWED = {"read_scene_table_context", "check_screenplay", "submit_scene_table"}
OLD_PLAN = "旧场表哨兵：封缘私下查勘七个入口"
OLD_BODY = "旧正文哨兵：被屏蔽的详细行动不可作为新拆场依据"
RAW_SOURCE = "原文哨兵：场表任务不应读取原始全文"


def setup(ops):
    screenplay_id, segment_id, unit_id, event_id = prepare(ops)
    segment = ops.read_script_segment(segment_id)
    plan = segment["units"][0]["plan"] | {"summary": OLD_PLAN, "outcome": OLD_PLAN}
    ops.apply_scene_table_changes(segment_id, [{"op": "update", "unit_id": unit_id, "changes": {"plan": plan}}],
                                  segment["outline_revision"], reason="隔离旧稿fixture")
    unit = ops.read_script_segment(segment_id)["units"][0]
    ops.apply_script_block_changes(unit_id, [{"op": "insert", "block": {"type": "action", "payload": {"text": OLD_BODY}}}],
                                   [], unit["body_revision"], reason="保留旧正文fixture")
    source_id = ops.import_source("禁止旁路原文", RAW_SOURCE)
    current = ops.read_screenplay(screenplay_id)["screenplay"]
    other = ops.create_script_segment(screenplay_id, segment["span_id"], "非目标段", current["structure_revision"], "隔离非目标段")
    other_screenplay = ops.create_screenplay("非目标剧本", reason="隔离检查目标")["screenplay"]["id"]
    target = {"kind": "scene_table", "ids": [segment_id], "screenplay_id": screenplay_id}
    task = ops.prepare_script_writing_task(target)
    return {"screenplay_id": screenplay_id, "segment_id": segment_id, "unit_id": unit_id, "event_id": event_id,
            "other_segment_id": other["id"], "other_screenplay_id": other_screenplay, "source_id": source_id,
            "target": target, "task": task}


def forbidden_calls(fixture):
    return [
        ("read_source", {"source_id": fixture["source_id"]}),
        ("read_screenplay", {"screenplay_id": fixture["screenplay_id"]}),
        ("read_screenplay_timeline", {"screenplay_id": fixture["screenplay_id"]}),
        ("adaptation_snapshot", {}),
        ("read_script_unit_context", {"unit_id": fixture["unit_id"]}),
        ("read_scene_table_context", {"segment_id": fixture["other_segment_id"]}),
        ("read_scene_table_context", {"segment_id": fixture["segment_id"], "dependency_refs": []}),
        ("read_scene_table_context", {"segment_id": fixture["segment_id"], "dependency_refs": [{"kind": "unit_body", "id": fixture["unit_id"]}]}),
        ("check_screenplay", {"screenplay_id": fixture["other_screenplay_id"]}),
        ("submit_scene_table", {"segment_id": fixture["other_segment_id"], "units": []}),
        ("submit_scene_table", {"segment_id": fixture["segment_id"], "dependency_refs": [{"kind": "unit_body", "id": fixture["unit_id"]}]}),
        ("submit_scene_table", {"segment_id": fixture["segment_id"], "dependency_refs": [{"kind": "segment", "id": fixture["other_segment_id"]}]}),
        ("submit_scene_table", {"segment_id": fixture["segment_id"], "dependency_refs": [{"kind": "adaptation_entity", "id": fixture["event_id"]}]}),
        ("adopt_scene_table", {"segment_id": fixture["segment_id"], "expected_outline_revision": 1, "reason": "不得借用采用"}),
        ("mark_writing_basis_current", {"target_kind": "segment", "target_id": fixture["segment_id"], "expected_revision": 1, "reason": "不得借用新依据"}),
    ]


def test_direct_guard_rejects_read_and_write_bypasses_without_business_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "scene-scope.nproj")
    fixture = setup(ops)
    before = copy.deepcopy(read_context(ops))
    assert {row["name"] for row in tool_catalog(ALLOWED)} == ALLOWED
    for name, args in forbidden_calls(fixture):
        with pytest.raises(ValueError):
            validate_script_writing_tool_call(fixture["task"], name, args, ops=ops)
    validate_script_writing_tool_call(fixture["task"], "read_scene_table_context", {"segment_id": fixture["segment_id"]}, ops=ops)
    validate_script_writing_tool_call(fixture["task"], "check_screenplay", {"screenplay_id": fixture["screenplay_id"]}, ops=ops)
    defaults = ops.read_scene_table_context(fixture["segment_id"])["dependency_refs"]
    for deps in (None, [], defaults[:1], defaults):
        validate_script_writing_tool_call(fixture["task"], "submit_scene_table", {"segment_id": fixture["segment_id"], "dependency_refs": deps}, ops=ops)
    with pytest.raises(ValueError):
        validate_script_writing_tool_call(fixture["task"], "submit_scene_table", {"segment_id": fixture["segment_id"], "dependency_refs": defaults}, ops=None)
    assert read_context(ops) == before


def test_resident_scene_table_host_is_scoped_and_write_ack_never_returns_old_prose(tmp_path, monkeypatch):
    from test_chat_scroll import transcript_shell

    from narrative import assistant
    from narrative.conversation_runtime import get_existing_runtime
    from narrative.harness import HarnessSettings
    from narrative.tool_execution_context import (
        current_tool_execution_metadata,
        is_scene_table_generation,
    )

    invoked, replies, catalogs = [], [], []
    original_call = assistant._call
    def observed_call(project, name, args):
        assert is_scene_table_generation()
        invoked.append(name)
        return original_call(project, name, args)

    class Runner:
        def __init__(self, *args, **kwargs):
            self._closed = False
            self.active_session = None
        def resume(self, _session):
            return True
        def agent_turn(self, _instruction, session, **_kwargs):
            runtime = get_existing_runtime(ops)
            catalogs.append(set(runtime._binding["allowed"]))
            address = urlsplit(runtime.host.config["url"])
            def post(name, args):
                connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
                try:
                    connection.request("POST", "/call", json.dumps({"session_id": session, "nonce": runtime._binding["nonce"],
                        "name": name, "args": args}), {"Authorization": "Bearer " + runtime.host.token, "Content-Type": "application/json"})
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    replies.append((name, response.status, payload))
                    return response.status, payload
                finally:
                    connection.close()
            try:
                for name, args in forbidden_calls(fixture):
                    status, payload = post(name, args)
                    assert status == 400 and payload["ok"] is False and "result" not in payload
                    assert all(marker not in json.dumps(payload, ensure_ascii=False) for marker in (OLD_PLAN, OLD_BODY, RAW_SOURCE))
                status, payload = post("read_scene_table_context", {"segment_id": fixture["segment_id"]})
                assert status == 200
                context = payload["result"]
                assert all(marker not in json.dumps(context, ensure_ascii=False) for marker in (OLD_PLAN, OLD_BODY, RAW_SOURCE))
                assert "plan" not in context["segment"]["units"][0]
                status, payload = post("check_screenplay", {"screenplay_id": fixture["screenplay_id"]})
                assert status == 200
                assert all(marker not in json.dumps(payload, ensure_ascii=False) for marker in (OLD_PLAN, OLD_BODY, RAW_SOURCE))
                submit = {"segment_id": fixture["segment_id"], "units": [{"id": fixture["unit_id"], "plan": {"title": "新场表", "summary": "甲渡桥后停步。"}}],
                    "expected_outline_revision": context["expected_outline_revision"], "span_fingerprint": context["span_fingerprint"],
                    "context_fingerprint": context["context_fingerprint"], "dependency_refs": context["dependency_refs"], "reason": "仅重拆目标场表"}
                status, payload = post("submit_scene_table", submit)
                assert status == 200, payload
                ack = payload["result"]
                assert all(marker not in json.dumps(ack, ensure_ascii=False) for marker in (OLD_PLAN, OLD_BODY, RAW_SOURCE))
                assert "plan" not in ack["segment"]["units"][0] and "blocks" not in ack["segment"]["units"][0]
                return SimpleNamespace(raw="目标场表已更新，旧正文保留。", metadata={"finish_reason": "completed"})
            finally:
                self.active_session = None
        def close(self, **_kwargs):
            self._closed = True

    monkeypatch.setattr(assistant, "_call", observed_call)
    monkeypatch.setattr(native_bridge, "HarnessRunner", Runner)
    monkeypatch.setenv("NARRATIVE_SCENE_SCOPE_TEST_KEY", "offline-stub")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        ops.save_provider_profile(HarnessSettings(provider="custom", model="offline-stub", base_url="http://127.0.0.1:1/v1", key_env="NARRATIVE_SCENE_SCOPE_TEST_KEY").model_dump())
        fixture = setup(ops)
        original_body = copy.deepcopy(ops.read_script_segment(fixture["segment_id"])["units"][0]["blocks"])
        ui.bridge.screenplayWritingTurn("只根据有效来源重新拆场。", fixture["target"])
        ui.wait(lambda: any(kind == "assistant" for kind, _ in ui.results))
        assert not ui.errors
        assert catalogs == [ALLOWED]
        assert invoked == ["read_scene_table_context", "check_screenplay", "submit_scene_table"]
        assert sum(status == 400 for _, status, _ in replies) == len(forbidden_calls(fixture))
        result = next(payload for kind, payload in ui.results if kind == "assistant")
        audit = json.loads((Path(result["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
        assert set(audit["task_tool_names"]) == ALLOWED
        stored = ops.read_script_segment(fixture["segment_id"])["units"][0]
        assert stored["plan"]["summary"] == "甲渡桥后停步。"
        assert stored["plan"]["source_event_ids"] == [fixture["event_id"]]
        assert stored["blocks"] == original_body
        assert current_tool_execution_metadata() == {} and not is_scene_table_generation()
