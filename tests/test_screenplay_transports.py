"""Screenplay operations are identical through CLI, MCP and resident tools."""
import inspect
import json

import pytest
from agent_fixtures import TEXT, draft_result

from narrative import mcp_server, tool_api, tool_cli
from narrative.assistant import ASSISTANT_TOOLS
from narrative.operations import Operations
from narrative.screenplay_writing import validate_script_writing_tool_call


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "剧本接口.nproj"
    ops = Operations.create_graph_project(entry)
    sid = ops.import_source("合成", TEXT)
    assert ops.submit_extraction(sid, draft_result())["status"] == "accepted"
    ops.sync_adaptation()
    return entry, ops, ops.adaptation_snapshot()


def rpc(entry, name, args):
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": args}}, default_project=str(entry))
    assert not reply["result"]["isError"], reply
    return json.loads(reply["result"]["content"][0]["text"])


def test_catalog_signature_parity(project):
    _entry, ops, _state = project
    for name in tool_cli._SCREENPLAY_ALIASES.values():
        assert name in ASSISTANT_TOOLS
        params = inspect.signature(getattr(ops, name)).parameters
        assert set(params) == set(tool_api.TOOLS[name].params), name
        required = {key for key, param in params.items() if param.default is inspect.Parameter.empty}
        assert required <= set(tool_api.TOOLS[name].schema()["required"]), name


def test_cli_mcp_scene_identity_and_local_body_edit(project, capsys):
    entry, ops, adaptation = project
    before = ops.graph_snapshot()
    ids = [n["id"] for n in adaptation["nodes"] if n["node_type"] == "event"]
    screenplay = tool_api.call("create_screenplay", {"title": "接人", "reason": "接口验收"}, project=entry)["screenplay"]
    timeline = rpc(entry, "import_screenplay_timeline", {"screenplay_id": screenplay["id"], "moment_ids": ids,
                "expected_timeline_revision": 0, "expected_adaptation_revision": adaptation["revision"], "reason": "接口导入"})
    span = rpc(entry, "create_adaptation_span", {"screenplay_id": screenplay["id"], "name": "接人范围", "selection_ids": ids,
                "expected_timeline_revision": timeline["revision"], "reason": "接口验收"})["span"]
    assert tool_cli.main(["script-segment-create", "--screenplay-id", screenplay["id"], "--span-id", span["id"],
                          "--title", "接人段", "--expected-structure-revision", str(screenplay["structure_revision"]),
                          "--reason", "接口验收", "--project", str(entry), "--json"]) == 0
    segment = json.loads(capsys.readouterr().out)
    context = rpc(entry, "read_scene_table_context", {"segment_id": segment["id"]})
    submit = {key: context[key] for key in ("span_fingerprint", "context_fingerprint", "expected_outline_revision", "dependency_refs")}
    table = rpc(entry, "submit_scene_table", submit | {"segment_id": segment["id"], "units": [
        {"plan": {"title": "约定", "summary": "甲约定接人"}},
        {"plan": {"title": "接到", "summary": "甲接到乙后离开"}}], "reason": "接口验收"})
    unit = table["units"][0]
    context = rpc(entry, "read_script_unit_context", {"unit_id": unit["id"]})
    params = {key: context[key] for key in ("span_fingerprint", "context_fingerprint", "plan_fingerprint", "expected_plan_revision", "expected_body_revision", "dependency_refs")}
    body = rpc(entry, "submit_script_unit", params | {"unit_id": unit["id"], "blocks": [
        {"type": "action", "payload": {"text": "甲在门外等候。"}},
        {"type": "action", "payload": {"text": "乙走来。"}}], "reason": "接口验收"})
    a, b = body["blocks"]
    changed = rpc(entry, "apply_script_block_changes", {"unit_id": unit["id"], "expected_body_revision": body["body_revision"],
        "target_block_ids": [a["id"]], "changes": [{"op": "update", "block_id": a["id"],
        "block": {"type": "action", "payload": {"text": "甲望向路口。"}}}], "reason": "局部编辑"})
    assert next(row for row in changed["blocks"] if row["id"] == b["id"]) == b
    assert ops.read_script_segment(segment["id"])["units"][0]["id"] == unit["id"]
    exported = rpc(entry, "export_screenplay", {"screenplay_id": screenplay["id"], "format": "text"})
    assert "甲望向路口" in exported["text"]
    assert ops.graph_snapshot() == before and ops.adaptation_snapshot() == adaptation


def test_host_scope_cannot_be_changed_by_another_manifest(project):
    entry, ops, adaptation = project
    node = next(n for n in adaptation["nodes"] if n["node_type"] == "event")
    screenplay = ops.create_screenplay("目标", reason="验收")["screenplay"]
    timeline = ops.import_screenplay_timeline(screenplay["id"], [node["id"]], expected_timeline_revision=0,
        expected_adaptation_revision=adaptation["revision"], reason="验收导入")
    span = ops.create_adaptation_span(screenplay["id"], "目标范围", selection_ids=[node["id"]],
        expected_timeline_revision=timeline["revision"], reason="验收")["span"]
    segment = ops.create_script_segment(screenplay["id"], span["id"], "目标段", screenplay["structure_revision"], "验收")
    task = ops.prepare_script_writing_task({"kind": "segment", "ids": [segment["id"]]})
    with pytest.raises(ValueError):
        validate_script_writing_tool_call(task, "edit_adaptation_node", {"node_id": node["id"], "changes": {"name": "越权"}}, ops=ops)


def test_local_timeline_import_and_edit_are_isolated_across_transports(project):
    entry, ops, adaptation = project
    events = [n["id"] for n in adaptation["nodes"] if n["node_type"] == "event"]
    a = ops.create_screenplay("甲稿", reason="隔离验收")["screenplay"]
    b = ops.create_screenplay("乙稿", reason="隔离验收")["screenplay"]
    for screenplay in (a, b):
        result = rpc(entry, "import_screenplay_timeline", {"screenplay_id": screenplay["id"], "moment_ids": events,
            "expected_timeline_revision": 0, "expected_adaptation_revision": adaptation["revision"], "reason": "隔离导入"})
        assert result["nodes"]
    a_before = ops.read_screenplay_timeline(a["id"])
    b_before = ops.read_screenplay_timeline(b["id"])
    assert a_before["timeline_id"] != b_before["timeline_id"]
    changed = rpc(entry, "edit_screenplay_timeline_node", {"screenplay_id": a["id"], "node_id": events[0],
        "changes": {"name": "甲稿本地改动"}, "expected_timeline_revision": a_before["revision"], "reason": "本地修改"})
    assert next(n for n in changed["nodes"] if n["id"] == events[0])["name"] == "甲稿本地改动"
    assert ops.read_screenplay_timeline(b["id"]) == b_before
    assert ops.adaptation_snapshot() == adaptation
    with pytest.raises(tool_api.ToolError):
        tool_api.call("import_screenplay_timeline", {"screenplay_id": a["id"], "moment_ids": events,
            "expected_timeline_revision": 0, "expected_adaptation_revision": adaptation["revision"], "reason": "过期拒绝"}, project=entry)


def test_cli_append_then_mcp_repeat_keeps_old_local_content(project, capsys):
    entry, ops, adaptation = project
    screenplay = ops.create_screenplay("追加剧本", reason="接口验收")["screenplay"]
    events = [n["id"] for n in adaptation["nodes"] if n["node_type"] == "event"]
    timeline = ops.import_screenplay_timeline(screenplay["id"], events, 0, adaptation["revision"], "首次导入")
    ops.edit_screenplay_timeline_node(screenplay["id"], events[0], {"name": "本地稿保留"}, expected_timeline_revision=timeline["revision"], reason="本地编辑")
    before = ops.read_screenplay_timeline(screenplay["id"])
    parent = next(n["id"] for n in adaptation["nodes"] if n["node_type"] == "scene")
    source = ops.edit_adaptation_node(changes={"node_type": "event", "name": "待追加节点"}, parent_id=parent,
        after_id=max(adaptation["nodes"], key=lambda n: n["sort_id"])["id"], reason="源新增fixture")
    added_id = next(n["id"] for n in source["nodes"] if n["name"] == "待追加节点")
    assert tool_cli.main(["screenplay-timeline-append", "--screenplay-id", screenplay["id"],
        "--moment-ids", json.dumps([added_id]), "--expected-timeline-revision", str(before["revision"]),
        "--expected-adaptation-revision", str(source["revision"]), "--reason", "追加接口验收",
        "--project", str(entry), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert next(n for n in result["nodes"] if n["id"] == events[0])["name"] == "本地稿保留"
    assert next(n for n in result["nodes"] if n["id"] == added_id)["sort_id"] > max(n["sort_id"] for n in before["nodes"])
    repeated = rpc(entry, "append_screenplay_timeline", {"screenplay_id": screenplay["id"], "moment_ids": [added_id],
        "expected_timeline_revision": result["revision"], "expected_adaptation_revision": source["revision"], "reason": "重复追加去重"})
    assert repeated["nodes"] == result["nodes"] and repeated["properties"] == result["properties"]
    assert ops.adaptation_snapshot()["nodes"] == source["nodes"]


def test_storage_inspection_mcp_reset_cli_and_create(project, capsys):
    entry, ops, _adaptation = project
    ops.create_screenplay("丢弃的旧剧本", reason="构建隔离旧结构")
    with ops._store.connect(write=True) as db:
        db.execute("DROP TABLE script_timelines")
    inspection = rpc(entry, "inspect_screenplay_storage", {})
    assert inspection["status"] == "incomplete"
    assert tool_cli.main(["screenplay-storage-reset", "--scope", "screenplay",
        "--expected-storage-fingerprint", inspection["expected_storage_fingerprint"],
        "--confirm-discard", "true", "--reason", "明确重建隔离剧本域", "--project", str(entry), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "reset"
    created = rpc(entry, "create_screenplay", {"title": "新剧本", "reason": "重建后新建"})
    assert ops.read_screenplay(created["screenplay"]["id"])["screenplay"]["title"] == "新剧本"
