"""All writing stages can inspect project materials while writes stay targeted."""
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
def test_scene_table_guard_allows_project_reads(task, name, args):
    validate_script_writing_tool_call(task, name, args)
    ordinary_unit = task | {"target": {"kind": "unit", "ids": ["U1"]}}
    validate_script_writing_tool_call(ordinary_unit, name, args)


def test_scene_table_guard_allows_reference_reads_but_checks_write_target(task):
    validate_script_writing_tool_call(task, "read_scene_table_context", {"segment_id": "SEG1"})
    validate_script_writing_tool_call(task, "check_screenplay", {"screenplay_id": "SP1"})
    validate_script_writing_tool_call(task, "read_scene_table_context", {"segment_id": "SEG2"})
    validate_script_writing_tool_call(task, "check_screenplay", {"screenplay_id": "SP2"})
    for refs in ([], [{"kind": "unit_body", "id": "U2"}], [{"kind": "unit_plan", "id": "U2"}]):
        validate_script_writing_tool_call(task, "read_scene_table_context", {"segment_id": "SEG1", "dependency_refs": refs})
    with pytest.raises(ValueError, match="非目标场表"):
        validate_script_writing_tool_call(task, "submit_scene_table", {"segment_id": "SEG2"})


def test_scene_table_runtime_binds_full_catalog_and_reads_project_materials(tmp_path, monkeypatch):
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
                status, payload = tool("read_screenplay_timeline", {"screenplay_id": screenplay_id})
                assert status == 200 and payload["result"]["nodes"]
                status, payload = tool("read_source", {"source_id": source_id})
                assert status == 200 and "开放读取原文" in json.dumps(payload, ensure_ascii=False)
                status, payload = tool("submit_scene_table", {"segment_id": "outside-segment", "units": []})
                assert status == 400 and "result" not in payload
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
        source_id = ops.import_source("开放资料", "开放读取原文")
        ui.bridge.screenplayWritingTurn("只核对本段场表。", {"kind": "scene_table", "ids": [segment_id], "screenplay_id": screenplay_id})
        ui.wait(lambda: any(kind == "assistant" for kind, _ in ui.results))
        assert observed["allowed"] == {row["name"] for row in tool_catalog()}
        assert not ui.errors



def test_material_reads_match_cli_and_mcp_without_stage_filter(tmp_path, monkeypatch, capsys):
    import json

    from narrative import mcp_server, tool_cli
    from narrative.operations import Operations

    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "open-materials.nproj"
    ops = Operations.create_graph_project(entry)
    source_id = ops.import_source("完整参考资料", "原文细节可以在任何阶段查阅。")
    assert tool_cli.main(["read-source", source_id, "--project", str(entry), "--json"]) == 0
    cli = json.loads(capsys.readouterr().out)
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "read_source", "arguments": {"source_id": source_id}}}, default_project=str(entry))
    assert not reply["result"]["isError"]
    assert json.loads(reply["result"]["content"][0]["text"]) == cli
