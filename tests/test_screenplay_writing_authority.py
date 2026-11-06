"""Current local plot remains authoritative while conflicting references stay readable."""
import importlib
from pathlib import Path

from narrative.operations import Operations
from narrative.prompt_defaults import DEFAULT_PROMPTS


def test_deleted_local_event_is_reference_despite_old_outcome_and_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    fixture_module = importlib.import_module("accept_screenplay_authority")
    ops = Operations.create_graph_project(tmp_path / "authority.nproj")
    fixture = fixture_module.seed(ops)
    context = ops.read_script_unit_context(fixture["unit_id"])
    scope = context["creation_scope"]
    assert scope["effective_event_ids"] == [fixture["ids"]["交接预算表"]]
    assert context["generation_allowed"] and not scope["invalid_event_ids"]
    assert "播放录音宣布离职" in {node["name"] for node in ops.graph_snapshot()["nodes"]}
    assert fixture["ids"]["播放录音宣布离职"] not in {node["id"] for node in ops.read_screenplay_timeline(fixture["screenplay_id"])["nodes"]}
    assert "辞职录音" in ops.source(fixture["source_id"])["text"]
    assert "辞职录音" in context["plan_input"]["plan"]["outcome"]
    assert "辞职录音" in context["unit"]["blocks"][0]["payload"]["text"]
    assert {"original", "source_adaptation", "previous_chat", "old_drafts"} <= set(scope["reference_only"])
    assert scope["plot_authority"] == ["specified_screenplay_timeline_and_bound_range", "consistent_current_scene_plan"]
    assert context["project_style"] == ops.load_effective_creative_style(fixture["screenplay_id"])
    assert context["project_style"] == context["reference_context"]["project_style"]
    assert fixture["project_style"]["text"] in context["project_style"]["text"]
    assert context["creative_agreement"]["sources"]["tone"] == "script"
    assert context["reference_context"]["creation_scope"] == scope
    assert "本剧本当前独立事件线" in context["reference_policy"]["guidance"]


def test_writing_defaults_distinguish_plot_scope_from_artistic_expression():
    versions = {"system": "5", "writing": "4", "batch_writing": "4",
                "unit_writing_shortcut": "3", "batch_writing_shortcut": "3"}
    for key in ("system", "scene_table", "writing", "scene_policy", "batch_writing", "scene_table_shortcut", "unit_writing_shortcut", "batch_writing_shortcut", "reference_policy"):
        assert DEFAULT_PROMPTS[key]["version"] == versions.get(key, "2")
        assert "事件线" in DEFAULT_PROMPTS[key]["text"]
    writing = DEFAULT_PROMPTS["writing"]["text"]
    assert "大胆重构对白、动作与场景细节" in writing
    assert "不能扩展为额外独立事件" in writing
    assert "场表任一字段" in writing and "当前删改取舍" in writing
    assert "本会话历史只供参考" in writing
    assert "逐句搬进动作块" in writing
