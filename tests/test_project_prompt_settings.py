"""Project prompt overrides are owned, audited, plain text and runtime-effective."""
import json

import pytest
from agent_fixtures import project as project

from narrative import mcp_server, tool_cli
from narrative.assistant import _first_prompt
from narrative.conversation_runtime import close_project_runtime, get_runtime
from narrative.harness import HarnessSettings
from narrative.operations import Operations
from narrative.prompt_settings import effective_prompt, prompt_snapshot


def save(ops, key, text):
    return ops.save_prompt_override(key, text, ops.load_prompt_settings()["revision"], "隔离提示设置测试")


def test_defaults_plain_text_override_reset_stale_and_project_isolation(project, tmp_path):
    original = project.load_prompt_settings()
    other = Operations.create_graph_project(tmp_path / "other.nproj")
    text = '任意题材；保留字面JSON {"value": "{not_a_template}"}。'
    changed = save(project, "extraction", text)
    assert changed["revision"] == original["revision"] + 1
    assert effective_prompt(project, "extraction") == text
    assert text in _first_prompt("用户任务", prompt_config=prompt_snapshot(project))
    assert other.load_prompt_settings() == original
    with pytest.raises(ValueError, match="已变化"):
        project.save_prompt_override("extraction", "过期写入", 0)
    persisted = project.load_prompt_settings()
    assert changed["status"] == "accepted" and {key: changed[key] for key in persisted} == persisted
    restored = save(project, "extraction", None)
    entry = next(row for row in restored["prompts"] if row["id"] == "extraction")
    assert not entry["overridden"] and entry["effective"] == entry["default"]
    with project._store.connect() as db:
        rows = [json.loads(row[0]) for row in db.execute("SELECT summary FROM actions WHERE action='save_prompt_override'")]
    assert len(rows) == 2 and rows[-1]["restored_default"]


def test_runtime_rebuild_uses_effective_system_and_keeps_chat_scope(project):
    captured = []
    class Runner:
        def __init__(self, _settings, _key, _directory, **kwargs):
            captured.append(kwargs["system_prompt"])
            self.closed = False
        def close(self):
            self.closed = True
    settings = HarnessSettings(model="offline-fixture")
    try:
        first = get_runtime(project, settings, "fixture", Runner)
        assert get_runtime(project, settings, "fixture", Runner) is first
        first._resumed.add("same-sdk-session")
        save(project, "system", "工程系统提示甲")
        second = get_runtime(project, settings, "fixture", Runner)
        assert second is not first and first.closed
        assert second.restart_reason == "configuration_changed"
        assert captured[-1] == "工程系统提示甲"
        assert second.sdk_directory == first.sdk_directory
        assert second._resumed == set()  # Next activation resumes the real SDK session.
        assert second._prompt_config == prompt_snapshot(project)
    finally:
        close_project_runtime(project)


def test_cli_mcp_share_override_read_and_restore(project, tmp_path, capsys):
    entry = tmp_path / "unified.nproj"
    params = {"prompt_id": "system", "text": "CLI自定工作流", "expected_revision": 0, "reason": "CLI设置"}
    assert tool_cli.main(["tool", "call", "save_prompt_override", json.dumps(params), "--project", str(entry), "--json"]) == 0
    cli = json.loads(capsys.readouterr().out)
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "load_prompt_settings", "arguments": {}}}, default_project=str(entry))
    assert not reply["result"]["isError"]
    persisted = json.loads(reply["result"]["content"][0]["text"])
    assert cli["status"] == "accepted" and {key: cli[key] for key in persisted} == persisted
    assert effective_prompt(project, "system") == "CLI自定工作流"


def test_scene_guidance_and_policy_use_project_effective_text(project):
    from test_tool_fingerprint_refs import prepare

    screenplay, segment, _unit, _event = prepare(project)
    save(project, "scene_table", "场表自定工作流 {literal}")
    save(project, "scene_policy", "根据用户自定义标准判断。\n保留可用语境。")
    save(project, "reference_policy", "参考资料按用户约定使用。")
    task = project.prepare_script_writing_task({"kind": "scene_table", "ids": [segment], "screenplay_id": screenplay})
    assert task["instruction"] == "场表自定工作流 {literal}"
    context = project.read_scene_table_context(segment)
    assert context["scene_table_policy"]["rules"] == ["根据用户自定义标准判断。", "保留可用语境。"]
    assert context["reference_policy"] == {"guidance": "参考资料按用户约定使用。"}


def test_empty_override_is_distinct_from_restore(project):
    save(project, "system", "")
    assert effective_prompt(project, "system") == ""
    assert next(row for row in project.load_prompt_settings()["prompts"] if row["id"] == "system")["overridden"]
    save(project, "scene_table_shortcut", "")
    assert effective_prompt(project, "scene_table_shortcut") == ""
    save(project, "system", None)
    assert effective_prompt(project, "system")


def test_task_snapshot_freezes_tool_guidance_and_is_project_scoped(project, tmp_path):
    from test_tool_fingerprint_refs import prepare

    from narrative.prompt_settings import bind_prompt_snapshot

    _screenplay, segment, _unit, _event = prepare(project)
    save(project, "scene_policy", "当前任务固定标准")
    save(project, "reference_policy", "当前任务固定参考规则")
    captured = prompt_snapshot(project)
    other = Operations.create_graph_project(tmp_path / "independent.nproj")
    with bind_prompt_snapshot(project, captured):
        save(project, "scene_policy", "下一任务新标准")
        save(project, "reference_policy", "下一任务新参考规则")
        assert next(row for row in project.load_prompt_settings()["prompts"] if row["id"] == "scene_policy")["effective"] == "下一任务新标准"
        context = project.read_scene_table_context(segment)
        assert context["scene_table_policy"]["rules"] == ["当前任务固定标准"]
        assert context["reference_policy"]["guidance"] == "当前任务固定参考规则"
        assert effective_prompt(other, "scene_policy") != "当前任务固定标准"
    context = project.read_scene_table_context(segment)
    assert context["scene_table_policy"]["rules"] == ["下一任务新标准"]
    assert context["reference_policy"]["guidance"] == "下一任务新参考规则"


def test_cli_preserves_explicit_root_parent(project, tmp_path, capsys):
    from test_screenplay_timeline_crud import prepare

    ids, script = prepare(project)
    revision = project.read_screenplay_timeline(script)["revision"]
    assert tool_cli.main(["screenplay-timeline-move", "--screenplay-id", script,
        "--node-id", ids["门口与屋内"], "--parent-id", "", "--expected-timeline-revision", str(revision),
        "--reason", "明确顶层", "--project", str(tmp_path / "unified.nproj"), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not any(ids["门口与屋内"] in row.get("events", []) for row in result["nodes"])


def test_captured_runtime_configuration_wins_over_later_settings_change(project):
    class Runner:
        def __init__(self, _settings, _key, _directory, **kwargs):
            self.system_prompt = kwargs["system_prompt"]
        def close(self):
            pass
    settings = HarnessSettings(model="offline-race")
    save(project, "system", "捕获时的系统提示")
    captured = prompt_snapshot(project)
    save(project, "system", "捕获后的新系统提示")
    try:
        runtime = get_runtime(project, settings, "fixture", Runner, prompt_config=captured)
        assert runtime._prompt_config == captured
        assert runtime.runner.system_prompt == "捕获时的系统提示"
        assert get_runtime(project, settings, "fixture", Runner, prompt_config=captured) is runtime
        next_task = get_runtime(project, settings, "fixture", Runner)
        assert next_task is not runtime and next_task.runner.system_prompt == "捕获后的新系统提示"
    finally:
        close_project_runtime(project)
