"""Project style and local screenplay scope are actual writing dependencies."""
import copy
import json

import pytest
from agent_fixtures import project as project
from test_tool_fingerprint_refs import prepare

from narrative import mcp_server, tool_cli
from narrative.operations import Operations
from narrative.screenplay_store import read_context
from narrative.screenplay_writing import validate_script_writing_tool_call


def submit(ops, unit, context):
    return ops.submit_script_unit(unit, [{"type": "action", "payload": {"text": "画面停留在人物整理桌面的动作上。"}}],
        context["expected_plan_revision"], context["expected_body_revision"], context["span_fingerprint"],
        context["context_fingerprint"], context["plan_fingerprint"], dependency_refs=context["dependency_refs"], reason="按当前风格与场表写作")


def test_style_persistence_isolation_revision_and_audit(project, tmp_path):
    assert project.load_project_style() == {"revision": 0, "text": ""}
    before = project.graph_snapshot()
    style = project.save_project_style("克制对白，以空间、动作和画面组织场面。", 0)
    other = Operations.create_graph_project(tmp_path / "other.nproj")
    assert other.load_project_style() == {"revision": 0, "text": ""}
    persisted = Operations.open(tmp_path / "unified.nproj").load_project_style()
    assert style["status"] == "accepted" and {key: style[key] for key in persisted} == persisted
    style = persisted
    with pytest.raises(ValueError, match="已变化"):
        project.save_project_style("过期", 0)
    assert project.load_project_style() == style
    assert project.graph_snapshot() == before
    assert project.save_project_style("", style["revision"])["text"] == ""
    with project._store.connect() as db:
        assert db.execute("SELECT count(*) FROM actions WHERE action='save_project_style'").fetchone()[0] == 2


def test_style_changes_invalidate_context_and_basis_without_erasing_body(project):
    _script, _segment, unit, _event = prepare(project)
    first = project.read_script_unit_context(unit)
    submit(project, unit, first)
    old = copy.deepcopy(read_context(project)[0]["script_blocks"])
    project.save_project_style("用视觉和动作表达，避免解释式复述。", 0)
    current = project.read_script_unit_context(unit)
    assert current["project_style"]["text"] == "用视觉和动作表达，避免解释式复述。"
    assert current["context_fingerprint"] != first["context_fingerprint"]
    assert current["unit"]["basis_state"] == "changed"
    # Use current object revisions with the old context to isolate style concurrency.
    stale = {**first, "expected_body_revision": current["expected_body_revision"]}
    with pytest.raises(ValueError, match="上下文依赖已变化"):
        submit(project, unit, stale)
    assert read_context(project)[0]["script_blocks"] == old
    submit(project, unit, current)
    basis = next(iter(read_context(project)[0]["script_writing_bases"].values()))
    assert basis["project_style"] == project.load_project_style()
    assert basis["creation_scope"]["timeline_id"] == current["timeline_id"]


def test_target_task_binds_style_but_keeps_all_reference_reads_open(project):
    script, _segment, unit, _event = prepare(project)
    task = project.prepare_script_writing_task({"kind": "unit", "ids": [unit], "screenplay_id": script})
    assert task["project_style"] == project.load_project_style()
    project.save_project_style("新的全工程约定", 0)
    validate_script_writing_tool_call(task, "read_source", {"source_id": "arbitrary"}, ops=project)
    validate_script_writing_tool_call(task, "adaptation_snapshot", {}, ops=project)
    with pytest.raises(ValueError, match="项目风格约定已变化"):
        validate_script_writing_tool_call(task, "submit_script_unit", {"unit_id": unit}, ops=project)


def test_body_scope_rejects_deleted_plan_event_and_keeps_original_readable(project):
    from test_screenplay_core import script, seed, span

    ids = seed(project)
    script(project, ids)
    bound = span(project, ids, first="甲敲门", last="甲坐下")
    document = project.read_screenplay(ids["_script"])["screenplay"]
    segment = project.create_script_segment(ids["_script"], bound["span"]["id"], "当前段", document["structure_revision"], "测试事件权威")
    segment = project.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {"plan": {
        "summary": "人物在门口整理物品。", "source_event_ids": [ids["甲开门"]]}}}], segment["outline_revision"], reason="有明确事件绑定")
    unit = segment["units"][0]["id"]
    context = project.read_script_unit_context(unit)
    assert context["creation_scope"]["effective_event_ids"] == [ids["甲开门"]]
    assert context["creation_scope"]["plot_authority"][0] == "specified_screenplay_timeline_and_bound_range"
    local = project.read_screenplay_timeline(ids["_script"])
    project.delete_screenplay_timeline_node(ids["_script"], ids["甲开门"], local["revision"], "从当前剧本删除该情节")
    updated = project.read_script_unit_context(unit)
    assert not updated["generation_allowed"]
    assert updated["creation_scope"]["invalid_event_ids"] == [ids["甲开门"]]
    before = copy.deepcopy(read_context(project))
    with pytest.raises(ValueError, match="本场场表引用的剧本事件"):
        submit(project, unit, updated)
    assert read_context(project) == before
    assert ids["甲开门"] in {node["id"] for node in project.adaptation_snapshot()["nodes"]}
    assert ids["甲开门"] in {node["id"] for node in project.graph_snapshot()["nodes"]}


def test_style_cli_mcp_contract(project, tmp_path, capsys):
    entry = tmp_path / "unified.nproj"
    assert tool_cli.main(["project-style-save", "--text", "以当代舞台空间组织画面", "--expected-revision", "0", "--project", str(entry), "--json"]) == 0
    saved = json.loads(capsys.readouterr().out)
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "load_project_style", "arguments": {}}}, default_project=str(entry))
    assert not reply["result"]["isError"]
    persisted = json.loads(reply["result"]["content"][0]["text"])
    assert saved["status"] == "accepted" and {key: saved[key] for key in persisted} == persisted
