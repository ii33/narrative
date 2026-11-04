"""Project-bound fingerprint handles preserve the original Operations checks."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from narrative import tool_api
from narrative.operations import Operations
from narrative.project_package import layout
from narrative.tool_fingerprint_refs import decode_tool_parameters, encode_tool_result

DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def prepare(ops):
    local = ops.edit_adaptation_node(changes={"node_type": "scene", "name": "渡桥"})
    scene_id = local["nodes"][0]["id"]
    source = ops.edit_adaptation_node(changes={"node_type": "event", "name": "甲渡桥"}, parent_id=scene_id)
    event_id = next(n["id"] for n in source["nodes"] if n["node_type"] == "event")
    screenplay = ops.create_screenplay("渡桥剧本", reason="依据编号测试")["screenplay"]
    timeline = ops.append_screenplay_timeline(screenplay["id"], [scene_id], 0, source["revision"], reason="导入独立副本")
    span = ops.create_adaptation_span(screenplay["id"], "渡桥段", selection_ids=[scene_id], expected_timeline_revision=timeline["revision"], reason="建立范围")
    segment = ops.create_script_segment(screenplay["id"], span["span"]["id"], "渡桥", screenplay["structure_revision"], reason="建立段落")
    segment = ops.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {"plan": {"title": "桥头", "summary": "甲在桥头停步。"}}}], segment["outline_revision"], reason="建立场表")
    return screenplay["id"], segment["id"], segment["units"][0]["id"], event_id


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "refs.nproj")
    return ops


def call(ops, name, params):
    return tool_api.call(name, params, project=layout(ops.project_info().path).entry)


def body_args(unit_id, context):
    return {"unit_id": unit_id, "blocks": [{"type": "action", "payload": {"text": "甲停步回头。"}}],
            "expected_plan_revision": context["expected_plan_revision"], "expected_body_revision": context["expected_body_revision"],
            "span_fingerprint": context["span_fingerprint"], "context_fingerprint": context["context_fingerprint"],
            "plan_fingerprint": context["plan_fingerprint"], "dependency_refs": context["dependency_refs"], "reason": "按已读依据提交"}


def test_shared_tool_context_uses_persistent_refs_and_keeps_raw_arguments_and_database(project):
    _, _, unit_id, _ = prepare(project)
    database = Path(project.project_info().path)
    before = database.read_bytes()
    context = call(project, "read_script_unit_context", {"unit_id": unit_id})
    for key, prefix in (("span_fingerprint", "span-basis-"), ("context_fingerprint", "context-basis-"), ("plan_fingerprint", "plan-basis-")):
        assert context[key].startswith(prefix) and not DIGEST.fullmatch(context[key])
    assert context["span"]["fingerprint"] == context["span"]["span_fingerprint"] == context["span_fingerprint"]
    assert database.read_bytes() == before
    assert call(project, "read_script_unit_context", {"unit_id": unit_id})["span_fingerprint"] == context["span_fingerprint"]
    params = body_args(unit_id, context)
    original = copy.deepcopy(params)
    from narrative.assistant import _call

    step = _call(layout(project.project_info().path).entry, "submit_script_unit", params)
    assert step["ok"] and step["result"]["body_presence"] == "present"
    assert step["args"] == original == params
    assert project.read_script_unit_context(unit_id)["unit"]["blocks"][0]["payload"]["text"] == "甲停步回头。"


def test_old_handle_does_not_refresh_changed_source_context(project):
    screenplay_id, _, unit_id, event_id = prepare(project)
    context = call(project, "read_script_unit_context", {"unit_id": unit_id})
    params = body_args(unit_id, context)
    revision = project.read_screenplay_timeline(screenplay_id)["revision"]
    project.edit_screenplay_timeline_node(screenplay_id, event_id, changes={"name": "甲停步后返回"}, expected_timeline_revision=revision, reason="修改实际范围输入")
    with pytest.raises(tool_api.ToolError, match="变化"):
        call(project, "submit_script_unit", params)
    assert project.read_script_unit_context(unit_id)["unit"]["body_presence"] == "empty"
    fresh = call(project, "read_script_unit_context", {"unit_id": unit_id})
    assert fresh["span_fingerprint"] != context["span_fingerprint"]


def test_handles_are_project_bound_even_when_both_projects_have_first_reference(project, tmp_path):
    other = Operations.create_graph_project(tmp_path / "other.nproj")
    a = encode_tool_result(project, {"span_fingerprint": "a" * 64})["span_fingerprint"]
    b = encode_tool_result(other, {"span_fingerprint": "b" * 64})["span_fingerprint"]
    assert a.startswith("span-basis-1@") and b.startswith("span-basis-1@") and a != b
    with pytest.raises(ValueError, match="当前工程"):
        decode_tool_parameters(other, {"span_fingerprint": a})
    assert decode_tool_parameters(project, {"span_fingerprint": a}) == {"span_fingerprint": "a" * 64}


@pytest.mark.parametrize("bad_kind", ["wrong_kind", "unknown", "typo"])
def test_invalid_reference_never_reaches_a_business_write(project, monkeypatch, bad_kind):
    _, _, unit_id, _ = prepare(project)
    context = call(project, "read_script_unit_context", {"unit_id": unit_id})
    params = body_args(unit_id, context)
    if bad_kind == "wrong_kind":
        params["span_fingerprint"] = context["context_fingerprint"]
    elif bad_kind == "unknown":
        params["span_fingerprint"] = context["span_fingerprint"].replace("span-basis-1@", "span-basis-999999@")
    else:
        params["span_fingerprint"] = "span-basis-not-a-valid-reference"
    from narrative.graph_operations import GraphOperations

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid reference executed business write")

    monkeypatch.setattr(GraphOperations, "submit_script_unit", forbidden)
    with pytest.raises(tool_api.ToolError, match="依据编号"):
        call(project, "submit_script_unit", params)


def test_cache_write_failure_keeps_committed_operation_and_usable_raw_basis(project, monkeypatch):
    import narrative.tool_fingerprint_refs as refs

    screenplay_id, _, unit_id, event_id = prepare(project)

    def unavailable(*args, **kwargs):
        raise OSError("simulated cache unavailable")

    monkeypatch.setattr(refs._Registry, "encode", unavailable)
    result = call(project, "create_adaptation_span", {"screenplay_id": screenplay_id, "name": "确已提交的范围", "selection_ids": [event_id],
        "expected_timeline_revision": project.read_screenplay_timeline(screenplay_id)["revision"], "reason": "测试提交后缓存故障"})
    assert result["transport_diagnostics"][0]["code"] == "TOOL_FINGERPRINT_CACHE_UNAVAILABLE"
    assert project.read_adaptation_span(result["span"]["id"])["span"]["name"] == "确已提交的范围"
    assert DIGEST.fullmatch(result["span_fingerprint"])
    context = call(project, "read_script_unit_context", {"unit_id": unit_id})
    assert context["transport_diagnostics"] and DIGEST.fullmatch(context["span_fingerprint"])
    assert call(project, "submit_script_unit", body_args(unit_id, context))["body_presence"] == "present"


def test_cache_removal_cannot_reassign_an_old_handle_to_a_new_basis(project):
    first = encode_tool_result(project, {"span_fingerprint": "a" * 64})["span_fingerprint"]
    cache = layout(project.project_info().path).chats / "tool-fingerprint-refs.sqlite3"
    cache.unlink()
    with pytest.raises(ValueError, match="重新读取"):
        decode_tool_parameters(project, {"span_fingerprint": first})
    assert not cache.exists()
    second = encode_tool_result(project, {"span_fingerprint": "b" * 64})["span_fingerprint"]
    assert second.startswith("span-basis-1@") and second != first
    with pytest.raises(ValueError, match="当前工程"):
        decode_tool_parameters(project, {"span_fingerprint": first})


def test_only_known_metadata_is_encoded_and_parameter_copy_preserves_user_literals(project):
    digest = "f" * 64
    result = {"span_fingerprint": digest, "text": digest, "payload": {"context_fingerprint": digest},
              "creative_brief": {"span_fingerprint": digest}, "value": {"plan_fingerprint": digest},
              "unrelated_hash": digest, "plan": {"story_time": {"fingerprint": digest}}}
    original = copy.deepcopy(result)
    encoded = encode_tool_result(project, result)
    assert result == original
    for key in ("text", "payload", "creative_brief", "value", "unrelated_hash", "plan"):
        assert encoded[key] == original[key]
    decoded = decode_tool_parameters(project, encoded)
    assert decoded == original
    decoded["creative_brief"]["span_fingerprint"] = "user edit"
    assert encoded["creative_brief"]["span_fingerprint"] == digest


def test_storage_snapshot_reference_keeps_original_reset_concurrency_check(project):
    inspection = call(project, "inspect_screenplay_storage", {})
    assert inspection["expected_storage_fingerprint"].startswith("storage-snapshot-")
    project.create_screenplay("创建使存储快照变化", reason="测试旧快照拒绝")
    with pytest.raises(tool_api.ToolError, match="已变化"):
        call(project, "reset_screenplay_storage", {"scope": "screenplay", "confirm_discard": True,
            "expected_storage_fingerprint": inspection["expected_storage_fingerprint"], "reason": "不得刷新旧依据后重建"})
    assert len(project.list_screenplays()) == 1


def test_resident_writing_guard_receives_decoded_copy_while_audit_keeps_model_refs(tmp_path, monkeypatch):
    import http.client
    import json
    from types import SimpleNamespace
    from urllib.parse import urlsplit

    from test_chat_scroll import transcript_shell

    from narrative import native_bridge, screenplay_writing
    from narrative.conversation_runtime import get_existing_runtime
    from narrative.harness import HarnessSettings

    seen_by_guard = []
    instructions = []
    original_guard = screenplay_writing.validate_script_writing_tool_call

    def guard(task, name, args, ops=None):
        if name == "submit_script_unit":
            assert all(DIGEST.fullmatch(args[key]) for key in ("span_fingerprint", "context_fingerprint", "plan_fingerprint"))
            seen_by_guard.append(copy.deepcopy(args))
        return original_guard(task, name, args, ops=ops)

    class Runner:
        def __init__(self, *args, **kwargs):
            self._closed = False
            self.active_session = None

        def resume(self, session):
            return True

        def agent_turn(self, instruction, session, **kwargs):
            instructions.append(instruction)
            runtime = get_existing_runtime(ops)
            address = urlsplit(runtime.host.config["url"])

            def host_call(name, args):
                connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
                try:
                    connection.request("POST", "/call", json.dumps({"session_id": session, "nonce": runtime._binding["nonce"],
                        "name": name, "args": args}), {"Authorization": "Bearer " + runtime.host.token, "Content-Type": "application/json"})
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    assert response.status == 200, payload
                    return payload["result"]
                finally:
                    connection.close()

            try:
                context = host_call("read_script_unit_context", {"unit_id": unit_id})
                assert context["span_fingerprint"].startswith("span-basis-")
                assert host_call("submit_script_unit", body_args(unit_id, context))["body_presence"] == "present"
                return SimpleNamespace(raw="本场已按明确目标提交。", metadata={"finish_reason": "completed"})
            finally:
                self.active_session = None

        def close(self, **kwargs):
            self._closed = True

    monkeypatch.setattr(screenplay_writing, "validate_script_writing_tool_call", guard)
    monkeypatch.setattr(native_bridge, "HarnessRunner", Runner)
    monkeypatch.setenv("NARRATIVE_REFS_TEST_KEY", "offline-stub")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        ops.save_provider_profile(HarnessSettings(provider="custom", model="offline-stub", base_url="http://127.0.0.1:1/v1", key_env="NARRATIVE_REFS_TEST_KEY").model_dump())
        screenplay_id, _, unit_id, _ = prepare(ops)
        ui.bridge.screenplayWritingTurn("写这一场。", {"kind": "unit", "ids": [unit_id], "screenplay_id": screenplay_id, "include_existing": True})
        ui.wait(lambda: any(kind == "assistant" for kind, _ in ui.results))
        result = next(payload for kind, payload in ui.results if kind == "assistant")
        assert len(seen_by_guard) == 1
        audit = json.loads((Path(result["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
        step = next(step for step in audit["steps"] if step["tool"] == "submit_script_unit")
        assert step["args"]["span_fingerprint"].startswith("span-basis-")
        assert not DIGEST.fullmatch(step["args"]["span_fingerprint"])
        assert seen_by_guard[0]["span_fingerprint"] == ops.read_script_unit_context(unit_id)["span_fingerprint"]
        metadata = audit["writing_task_metadata"]
        assert audit["writing_prompt_version"] == native_bridge.SCREENPLAY_WRITING_PROMPT_VERSION
        assert audit["prompt_config"]["texts"]["writing"] in instructions[0]
        assert native_bridge.SCREENPLAY_SCENE_TABLE_RULES not in instructions[0]
        assert metadata["task_id"] not in instructions[0]
        assert metadata["manifest_ref"] not in instructions[0]
        assert "task_id" not in step["args"] and "manifest_ref" not in step["args"]
        from narrative.screenplay_store import read_context
        from narrative.tool_execution_context import current_tool_execution_metadata

        state, _ = read_context(ops)
        basis = next(row for row in state["script_writing_bases"].values() if row["target_id"] == unit_id)
        assert basis["task_id"] == audit["task_id"] == metadata["task_id"]
        assert basis["manifest_ref"] == metadata["manifest_ref"]
        assert Path(basis["manifest_ref"]).is_file()
        assert current_tool_execution_metadata() == {}
        previous_results = sum(kind == "assistant" for kind, _ in ui.results)
        ui.bridge.assistantTurn("在普通任务中按显式目标再提交一次正文。", "")
        ui.wait(lambda: sum(kind == "assistant" for kind, _ in ui.results) > previous_results)
        state, _ = read_context(ops)
        basis = next(row for row in state["script_writing_bases"].values() if row["target_id"] == unit_id)
        assert basis["task_id"] is None and basis["manifest_ref"] is None
        assert current_tool_execution_metadata() == {}
        assert not ui.errors


def test_internal_provenance_overrides_forged_args_without_mutating_the_audit_input(project):
    from narrative.screenplay_store import read_context
    from narrative.tool_execution_context import (
        bind_tool_execution_metadata,
        current_tool_execution_metadata,
    )

    _, _, unit_id, _ = prepare(project)
    params = body_args(unit_id, call(project, "read_script_unit_context", {"unit_id": unit_id}))
    params.update(task_id="model-forged-task", manifest_ref="model-forged-path")
    original = copy.deepcopy(params)
    with bind_tool_execution_metadata({"task_id": "host-task", "manifest_ref": "host-task/writing-task.json"}):
        assert call(project, "submit_script_unit", params)["body_presence"] == "present"
    assert params == original
    state, _ = read_context(project)
    basis = next(row for row in state["script_writing_bases"].values() if row["target_id"] == unit_id)
    assert basis["task_id"] == "host-task" and basis["manifest_ref"] == "host-task/writing-task.json"
    assert current_tool_execution_metadata() == {}
    with pytest.raises(RuntimeError), bind_tool_execution_metadata({"task_id": "failing-task", "manifest_ref": "failing-path"}):
        raise RuntimeError("controlled failure")
    assert current_tool_execution_metadata() == {}
