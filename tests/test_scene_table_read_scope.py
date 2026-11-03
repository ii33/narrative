"""Scene-table tasks cannot reread full timelines, raw text or another segment."""
from __future__ import annotations

import pytest

from narrative.assistant import tool_catalog
from narrative.screenplay_writing import validate_script_writing_tool_call


@pytest.fixture
def task():
    return {"target": {"kind": "scene_table", "ids": ["SEG1"]}, "screenplay_id": "SP1",
            "segment_ids": ["SEG1"], "unit_ids": [], "block_ids": []}


def test_optional_catalog_is_an_exact_subset_without_widening_normal_chat():
    selected = {"read_scene_table_context", "check_screenplay", "submit_scene_table"}
    assert {row["name"] for row in tool_catalog(selected)} == selected
    assert tool_catalog([]) == []
    assert "read_source" in {row["name"] for row in tool_catalog()}
    assert "read_screenplay_timeline" in {row["name"] for row in tool_catalog()}


@pytest.mark.parametrize("name,args", [
    ("read_screenplay", {"screenplay_id": "SP1"}),
    ("read_screenplay_timeline", {"screenplay_id": "SP1"}),
    ("read_source", {"source_id": "SRC1"}),
    ("get_node", {"node_id": "E51"}),
    ("read_script_unit_context", {"unit_id": "U1"}),
    ("read_script_segment", {"segment_id": "SEG1"}),
    ("adaptation_snapshot", {}),
    ("export_screenplay", {"screenplay_id": "SP1"}),
])
def test_scene_table_guard_refuses_full_or_unscoped_reads(task, name, args):
    with pytest.raises(ValueError, match="只允许读取"):
        validate_script_writing_tool_call(task, name, args)
    ordinary_unit = task | {"target": {"kind": "unit", "ids": ["U1"]}}
    validate_script_writing_tool_call(ordinary_unit, name, args)


def test_scene_table_guard_checks_target_and_disallows_custom_dependency_reads(task):
    validate_script_writing_tool_call(task, "read_scene_table_context", {"segment_id": "SEG1"})
    validate_script_writing_tool_call(task, "check_screenplay", {"screenplay_id": "SP1"})
    with pytest.raises(ValueError, match="非目标段落"):
        validate_script_writing_tool_call(task, "read_scene_table_context", {"segment_id": "SEG2"})
    with pytest.raises(ValueError, match="其他剧本"):
        validate_script_writing_tool_call(task, "check_screenplay", {"screenplay_id": "SP2"})
    for refs in ([], [{"kind": "unit_body", "id": "U2"}], [{"kind": "unit_plan", "id": "U2"}]):
        with pytest.raises(ValueError, match="不得自行扩大依赖"):
            validate_script_writing_tool_call(task, "read_scene_table_context", {"segment_id": "SEG1", "dependency_refs": refs})


def test_scene_table_runtime_binds_minimal_catalog_and_denies_out_of_scope_reads(tmp_path, monkeypatch):
    import http.client
    import json
    from types import SimpleNamespace
    from urllib.parse import urlsplit

    from test_chat_scroll import transcript_shell
    from test_tool_fingerprint_refs import prepare

    from narrative import native_bridge
    from narrative.conversation_runtime import get_existing_runtime
    from narrative.harness import HarnessSettings
    from narrative.operations import Operations

    observed = {}

    class Runner:
        def __init__(self, *args, **kwargs):
            self._closed = False
            self.active_session = None

        def resume(self, session):
            return True

        def agent_turn(self, instruction, session, **kwargs):
            runtime = get_existing_runtime(ops)
            observed["allowed"] = set(runtime._binding["allowed"])
            address = urlsplit(runtime.host.config["url"])

            def tool(name, args):
                connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
                try:
                    connection.request("POST", "/call", json.dumps({"session_id": session, "nonce": runtime._binding["nonce"],
                        "name": name, "args": args}), {"Authorization": "Bearer " + runtime.host.token, "Content-Type": "application/json"})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()

            try:
                for name, args in (("read_screenplay_timeline", {"screenplay_id": screenplay_id}),
                                   ("read_source", {"source_id": "SRC1"}),
                                   ("read_scene_table_context", {"segment_id": "outside-segment"})):
                    status, payload = tool(name, args)
                    assert status == 400, payload
                    assert "result" not in payload
                status, payload = tool("read_scene_table_context", {"segment_id": segment_id})
                assert status == 200, payload
                observed["context"] = payload["result"]
                return SimpleNamespace(raw="已按目标场表上下文核对。", metadata={"finish_reason": "completed"})
            finally:
                self.active_session = None

        def close(self, **kwargs):
            self._closed = True

    monkeypatch.setattr(native_bridge, "HarnessRunner", Runner)
    monkeypatch.setenv("NARRATIVE_SCENE_SCOPE_TEST_KEY", "offline-stub")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        ops.save_provider_profile(HarnessSettings(provider="custom", model="offline-stub", base_url="http://127.0.0.1:1/v1", key_env="NARRATIVE_SCENE_SCOPE_TEST_KEY").model_dump())
        screenplay_id, segment_id, _, _ = prepare(ops)
        ui.bridge.screenplayWritingTurn("只核对本段场表。", {"kind": "scene_table", "ids": [segment_id], "screenplay_id": screenplay_id})
        ui.wait(lambda: any(kind == "assistant" for kind, _ in ui.results))
        assert observed["allowed"] == {"read_scene_table_context", "check_screenplay", "submit_scene_table"}
        assert "plan" not in observed["context"]["segment"]["units"][0]
        assert not ui.errors

