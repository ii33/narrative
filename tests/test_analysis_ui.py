import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from narrative.operations import Operations


def test_person_event_map_and_search_are_read_only(tmp_path, monkeypatch):
    directory = tmp_path / "projects"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(directory))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "analysis-ui")
    monkeypatch.setattr(
        "narrative.operations.credential_status",
        lambda *_args, **_kwargs: {"available": False, "source": "none"},
    )

    def no_model_call(*args, **kwargs):
        pytest.fail("分析工作台只读浏览，不应调用模型")

    monkeypatch.setattr("narrative.jobs.start", no_model_call)
    operations = Operations.open_project(directory, "analysis-ui")
    source_id = operations.import_text("寻信", "甲性格勇敢。甲开始寻找乙的信件。")
    evidence_trait = operations.locate(source_id, "甲性格勇敢。")[0]
    evidence_event = operations.locate(source_id, "甲开始寻找乙的信件。")[0]
    candidate_id = operations.propose_objects(
        [
            {"kind": "entity", "id": "person-a", "name": "甲", "entity_type": "person"},
            {"kind": "entity", "id": "person-b", "name": "乙", "entity_type": "person"},
            {
                "kind": "assertion", "id": "trait-a", "subject_id": "person-a",
                "predicate": "性格特点", "value": "勇敢", "scope": "world",
                "properties": {"analysis_kind": "trait"},
                "evidence": [evidence_trait.model_dump(mode="json")],
            },
            {
                "kind": "relation", "id": "friendship", "subject_id": "person-a",
                "object_id": "person-b", "predicate": "朋友", "certainty": "certain",
                "evidence": [evidence_trait.model_dump(mode="json")],
            },
            {
                "kind": "event", "id": "search-letter", "title": "寻找信件", "action": "act",
                "actor_id": "person-a", "target_id": "person-b",
                "participants": [
                    {"entity_id": "person-a", "role": "行动者"},
                    {"entity_id": "person-b", "role": "信件主人"},
                ],
                "narrative_type": "主线", "evidence": [evidence_event.model_dump(mode="json")],
            },
            {
                "kind": "storyline", "id": "main-line", "title": "寻信主线",
                "line_type": "main", "event_ids": ["search-letter"], "description": "寻找信件",
                "evidence": [evidence_event.model_dump(mode="json")],
            },
        ]
    )
    operations.accept_candidate(candidate_id, operations.review_candidate(candidate_id).version)
    revision = operations.project_info().revision

    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=25).run()
    assert not app.exception
    assert app.tabs[0].label == "分析工作区"
    assert app.tabs[0].label in [tab.label for tab in app.tabs]
    assert any("勇敢" in str(frame.value) for frame in app.dataframe)
    assert any("乙" in str(frame.value) for frame in app.dataframe)
    assert any("寻找信件" in str(frame.value) for frame in app.dataframe)
    assert any("行动者" in str(frame.value) for frame in app.dataframe)
    assert any(widget.label == "选择故事线" for widget in app.selectbox)

    query = next(widget for widget in app.text_input if widget.label.startswith("关键词"))
    query.set_value("勇敢").run()
    next(button for button in app.button if button.label == "检索").click().run()
    assert not app.exception
    assert any("性格特点" in str(frame.value) for frame in app.dataframe)
    next(widget for widget in app.selectbox if widget.label == "分类对象类型").select(
        "人物／地点／物品"
    ).run()
    next(widget for widget in app.selectbox if widget.label == "分类对象").select("甲").run()
    next(widget for widget in app.text_input if widget.label == "分类名称").set_value("英雄").run()
    next(button for button in app.button if button.label == "提交分类候选").click().run()
    assert not app.exception
    assert operations.project_info().revision == revision
    assert len(operations.candidates(status="pending")) == 1
    assert operations.records("classification") == []


def test_v5_analysis_views_render_readable_names_without_mutation(monkeypatch):
    project_dir = Path(__file__).resolve().parents[1] / "data"
    project_path = project_dir / "kongyiji-agent-v5.sqlite3"
    if not project_path.exists():
        pytest.skip("v5 read-only demonstration project is not present")
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "kongyiji-agent-v5")
    operations = Operations.open_project(project_dir, "kongyiji-agent-v5")
    before = (operations.project_info().revision, operations.candidates())

    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30).run()
    assert not app.exception

    tables = [str(frame.value) for frame in app.dataframe]
    table_text = "\n".join(tables)
    assert "孔乙己" in table_text
    assert "掌柜" in table_text
    assert "受掌柜安排" in table_text
    assert "孔乙己到咸亨酒店喝酒遭众人嘲笑并争辩窃书" in table_text
    assert "人物特点" in table_text
    storyline_options = next(widget for widget in app.selectbox if widget.label == "选择故事线").options
    assert any("主线：孔乙己在咸亨酒店" in option for option in storyline_options)
    person_options = next(widget for widget in app.selectbox if widget.label == "选择人物").options
    assert any("孔乙己" in option for option in person_options)
    event_options = next(widget for widget in app.selectbox if widget.label == "选择事件").options
    assert any("孔乙己到咸亨酒店" in option for option in event_options)
    assert all(
        list(getattr(frame.value, "columns", [])) != ["事件", "故事时间", "披露位置", "状态", "ID"]
        for frame in app.dataframe
    )
    assert any(widget.label == "选择人物" for widget in app.selectbox)
    assert any(widget.label == "选择事件" for widget in app.selectbox)
    assert any(widget.label == "打开分类对象" for widget in app.selectbox)
    classification_options = next(widget for widget in app.selectbox if widget.label == "打开分类对象").options
    assert any("孔乙己" in option for option in classification_options)
    assert any("技术详情" in widget.label for widget in app.expander)

    after = (operations.project_info().revision, operations.candidates())
    assert after == before


def test_failed_isolated_extraction_task_has_readable_selector_and_status(monkeypatch):
    project_dir = Path(__file__).resolve().parents[1] / "data"
    project_path = project_dir / "kongyiji-label-smoke.sqlite3"
    if not project_path.exists():
        pytest.skip("isolated extraction smoke project is not present")
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "kongyiji-label-smoke")
    operations = Operations.open_project(project_dir, "kongyiji-label-smoke")
    before = (operations.project_info().revision, operations.runs(), operations.candidates())
    run_id = operations.runs()[0]["id"]
    handoff_calls = []

    def start_handoff(self, extraction_run_id, *, task=None, budget=None, api_key=None):
        handoff_calls.append((extraction_run_id, task))
        return "handoff-agent-run"

    monkeypatch.setattr(Operations, "start_narrative_from_extraction", start_handoff)

    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30).run()
    assert not app.exception
    run_selector = next(widget for widget in app.selectbox if widget.label == "提取任务")
    assert any("孔乙己·第4段节选" in option and "失败" in option for option in run_selector.options)
    assert run_id not in run_selector.options
    assert any("任务状态：提取失败" in str(item.value) for item in app.markdown)
    assert any("模型未完整结束" in str(item.value) for item in app.error)
    assert any("evidence-extraction/11" in str(item.value) for item in app.caption)
    assert any("历史提取任务 · 创建于" in str(item.value) for item in app.caption)
    next(button for button in app.button if button.label == "查看此块模型返回正文").click().run()
    assert any("没有模型返回正文" in str(item.value) for item in app.code)
    next(button for button in app.button if button.label == "交给 agent 继续处理").click().run()
    assert handoff_calls and handoff_calls[0][0] == run_id
    assert "重新检索原文" in handoff_calls[0][1]
    assert (operations.project_info().revision, operations.runs(), operations.candidates()) == before


def test_agent_research_threshold_budget_and_phase_display(tmp_path, monkeypatch):
    directory = tmp_path / "projects"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(directory))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "agent-budget-ui")
    monkeypatch.setattr(
        "narrative.operations.credential_status",
        lambda *_args, **_kwargs: {"available": False, "source": "none"},
    )
    operations = Operations.open_project(directory, "agent-budget-ui")
    source_id = operations.import_text("验收文本", "甲走进屋内，询问信件。")
    evidence = operations.locate(source_id, "甲走进屋内")[0]
    candidate_id = operations.propose_objects([{
        "kind": "entity", "id": "person-a", "name": "甲", "entity_type": "person",
        "evidence": [evidence.model_dump(mode="json")],
    }])
    captured = {}

    def start_analysis(
        self, source_ids, *, budget=None, api_key=None, task=None, feedback=None,
        seed_candidate_id=None, feedback_origin="user_input",
    ):
        call = {
            "source_ids": source_ids, "budget": budget, "task": task,
            "feedback": feedback, "seed_candidate_id": seed_candidate_id,
            "feedback_origin": feedback_origin,
        }
        captured.setdefault("calls", []).append(call)
        return f"test-agent-run-{len(captured['calls'])}"

    def analysis_status(self, run_id):
        assert run_id in {"test-agent-run-1", "test-agent-run-2", "test-agent-run-3"}
        child_run = run_id == "test-agent-run-2"
        return {
            "id": run_id,
            "status": "running",
            "source_ids": [source_id],
            "budget": captured["calls"][-1]["budget"],
            "task": captured["calls"][-1]["task"],
            "feedback": captured["calls"][-1]["feedback"],
            "feedback_origin": captured["calls"][-1]["feedback_origin"],
            "lineage": {"parent_candidate_id": candidate_id} if child_run else None,
            "provider": "test",
            "model": "test-model",
            "prompt_version": "narrative-agent/5",
            "phase": "precheck",
            "phase_rounds_used": {"research": 3, "draft": 1, "precheck": 1},
            "phase_remaining": None,
            "precheck_diagnostics": [
                {"severity": "error", "code": "evidence_missing", "message": "证据需修正"}
            ],
            "rounds": 4,
            "tool_count": 7,
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "candidate_ids": [candidate_id] if not child_run else [],
            "steps": [],
            "workspace": {},
            "evidence_handles": {},
        }

    control_calls = []

    def analysis_trace(self, run_id):
        return {
            "run_id": run_id, "status": "running", "phase": "precheck",
            "request_state": "in_flight", "provider": "test", "model": "test-model",
            "prompt_version": "narrative-agent/5", "source_labels": ["验收文本"],
            "rounds": 4, "tool_count": 7, "usage": {"total_tokens": 150},
            "task": captured["calls"][-1]["task"],
            "control": {"paused": False, "pending_count": 0, "instructions": []},
            "workspace": [{"id": "person-a", "kind": "entity", "label": "甲"}],
            "precheck_diagnostics": [{"severity": "error", "code": "evidence_missing", "message": "证据需修正"}],
            "candidate_ids": [], "steps": [{
                "phase": "draft", "finish_reason": "completed", "plan": "先补充甲的证据。",
                "request_state": "completed",
                "raw_available": True,
                "tools": [{"name": "search_source", "args": {"query": "甲"},
                    "result": {"summary": "找到 1 处原文"}}],
                "staged": [{"label": "甲 · 人物"}], "removed": [], "diagnostics": [],
            }],
            "notice": "仅展示已记录的模型响应与工具动作；不展示或推断模型内部思考。",
        }

    def pause_analysis(self, run_id):
        control_calls.append(("pause", run_id))
        return {"paused": True}

    def send_instruction(self, run_id, instruction):
        control_calls.append(("instruction", run_id, instruction))
        return {"seq": 1, "text": instruction}

    def analysis_response(self, run_id, round_number):
        control_calls.append(("response", run_id, round_number))
        return {"phase": "draft", "finish_reason": "completed", "prompt_version": "narrative-agent/5",
                "raw": '{"action":"stage","objects":[{"name":"甲"}]}'}

    monkeypatch.setattr(Operations, "start_narrative_analysis", start_analysis)
    monkeypatch.setattr(Operations, "narrative_analysis_status", analysis_status)
    monkeypatch.setattr(Operations, "narrative_analysis_trace", analysis_trace)
    monkeypatch.setattr(Operations, "pause_narrative_analysis", pause_analysis)
    monkeypatch.setattr(Operations, "send_narrative_instruction", send_instruction)
    monkeypatch.setattr(Operations, "narrative_analysis_response", analysis_response)
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=25).run()
    next(widget for widget in app.multiselect if widget.label == "本次分析原文范围").select(source_id).run()
    next(widget for widget in app.text_area if widget.label == "分析任务目标（可编辑）").set_value(
        "梳理人物关系与主线。"
    ).run()
    assert not app.exception
    assert "启动叙事分析" in [button.label for button in app.button], [button.label for button in app.button]
    next(button for button in app.button if button.label == "启动叙事分析").click().run()

    assert not app.exception
    assert captured["calls"][0]["source_ids"] == [source_id]
    assert captured["calls"][0]["task"] == "梳理人物关系与主线。"
    assert captured["calls"][0]["feedback_origin"] == "user_input"
    assert captured["calls"][0]["budget"] == {
        "research_rounds": 3,
        "max_rounds": None,
        "max_tokens_per_round": "inherit",
        "max_total_tokens": None,
        "wall_seconds": None,
        "request_seconds": 180,
    }
    assert "max_tools" not in captured["calls"][0]["budget"]
    assert any("当前阶段：预检" in str(item.value) for item in app.caption)
    assert any("持续至完成" in str(item.value) for item in app.caption)
    assert any("产生候选：1 个" in str(item.value) for item in app.caption)
    budget_view = next(json.loads(item.value) for item in app.json if "research_rounds" in item.value)
    assert budget_view["max_rounds"] == "不设上限"
    assert budget_view["max_total_tokens"] == "不设上限"
    assert budget_view["wall_seconds"] == "不设上限"
    assert any("evidence_missing" in str(frame.value) for frame in app.dataframe)
    assert any("模型正在处理当前请求" in str(item.value) for item in app.info)
    assert any("先补充甲的证据" in str(item.value) for item in app.markdown)
    assert any("检索原文" in str(item.value) for item in app.markdown)
    assert any("新增／更新：甲 · 人物" in str(item.value) for item in app.markdown)
    assert any(widget.label == "独立预审建议（可编辑）" for widget in app.text_area)
    next(button for button in app.button if button.label == "请求暂停").click().run()
    assert control_calls[-1] == ("pause", "test-agent-run-1")
    next(widget for widget in app.text_area if widget.label == "补充给 agent 的指令").set_value(
        "请补充关系证据。"
    ).run()
    next(button for button in app.button if button.label == "发送补充指令").click().run()
    assert control_calls[-1] == ("instruction", "test-agent-run-1", "请补充关系证据。")
    next(button for button in app.button if button.label == "查看本轮模型返回正文").click().run()
    assert control_calls[-1] == ("response", "test-agent-run-1", 1)
    assert any('"action":"stage"' in item.value for item in app.code)
    next(button for button in app.button if button.label == "交 agent 补查修订").click().run()
    assert not app.exception
    assert captured["calls"][1]["task"] == "梳理人物关系与主线。"
    assert captured["calls"][1]["seed_candidate_id"] == candidate_id
    assert captured["calls"][1]["feedback_origin"] == "review_agent_guided"
    assert captured["calls"][1]["feedback"]
    assert any("这是基于「" in item.value for item in app.info)
    assert any("独立预审建议" in item.label for item in app.expander)
    assert operations.candidates(status="pending")[0]["id"] == candidate_id
    assert operations.entities() == []

    output_mode_widget = next(widget for widget in app.selectbox if widget.label == "Agent 每轮输出设置")
    output_mode_widget.select("service-default").run()
    next(button for button in app.button if button.label == "启动叙事分析").click().run()
    assert captured["calls"][-1]["budget"]["max_tokens_per_round"] == "service-default"
    output_mode_widget = next(widget for widget in app.selectbox if widget.label == "Agent 每轮输出设置")
    output_mode_widget.select("override").run()
    next(widget for widget in app.number_input if widget.label == "本任务每轮最大输出 Token").set_value(256000).run()
    next(button for button in app.button if button.label == "启动叙事分析").click().run()
    assert captured["calls"][-1]["budget"]["max_tokens_per_round"] == 256000


def test_builtin_model_picker_keeps_custom_value_when_switching_provider(tmp_path, monkeypatch):
    directory = tmp_path / "projects"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(directory))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "model-picker-ui")
    monkeypatch.setattr(
        "narrative.operations.credential_status",
        lambda *_args, **_kwargs: {"available": False, "source": "none"},
    )
    operations = Operations.open_project(directory, "model-picker-ui")
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=25).run()
    provider = next(widget for widget in app.selectbox if widget.label == "模型供应商")
    catalog = next(widget for widget in app.selectbox if widget.label == "内置模型建议（可选）")
    assert any("Doubao-Seed-2.1-pro" in option for option in catalog.options)

    provider.select("openai").run()
    catalog = next(widget for widget in app.selectbox if widget.label == "内置模型建议（可选）")
    assert any("gpt-5.4" in option for option in catalog.options)
    catalog.select("gpt-5.4").run()
    model_input = next(widget for widget in app.text_input if widget.label == "精确模型 ID")
    assert model_input.value == "gpt-5.4"
    model_input.set_value("my-private-deployment").run()
    next(widget for widget in app.number_input if widget.label == "每块最大输出 Token").set_value(256000).run()
    next(button for button in app.button if button.label == "保存服务设置").click().run()
    assert operations.harness_settings()["provider"] == "openai"
    assert operations.harness_settings()["model"] == "my-private-deployment"
    assert operations.harness_settings()["max_tokens"] == 256000

    provider = next(widget for widget in app.selectbox if widget.label == "模型供应商")
    provider.select("qwen").run()
    provider = next(widget for widget in app.selectbox if widget.label == "模型供应商")
    provider.select("openai").run()
    model_input = next(widget for widget in app.text_input if widget.label == "精确模型 ID")
    assert model_input.value == "my-private-deployment"
    assert operations.harness_settings()["provider"] == "openai"
    assert operations.harness_settings()["model"] == "my-private-deployment"

    token_mode = next(widget for widget in app.selectbox if widget.label == "服务输出上限")
    token_mode.select("service-default").run()
    next(button for button in app.button if button.label == "保存服务设置").click().run()
    assert operations.harness_settings()["max_tokens"] is None
    provider = next(widget for widget in app.selectbox if widget.label == "模型供应商")
    provider.select("qwen").run()
    provider = next(widget for widget in app.selectbox if widget.label == "模型供应商")
    provider.select("openai").run()
    token_mode = next(widget for widget in app.selectbox if widget.label == "服务输出上限")
    assert token_mode.value == "service-default"
    token_mode.select("explicit").run()
    token_input = next(widget for widget in app.number_input if widget.label == "每块最大输出 Token")
    assert token_input.value == 256000
    assert operations.harness_settings("qwen")["max_tokens"] == 8192


def test_real_output_override_ui_uses_frozen_run_settings_read_only(monkeypatch):
    project_dir = Path(__file__).resolve().parents[1] / "data"
    project_path = project_dir / "wolf-first-output-cap.sqlite3"
    if not project_path.exists():
        pytest.skip("isolated output-limit validation project is not present")
    run_id = "71c45c4c6a9947ff877aca1d01d4ff2e"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "wolf-first-output-cap")
    operations = Operations.open_project(project_dir, "wolf-first-output-cap")
    before = (operations.project_info().revision, operations.candidates())
    assert operations.harness_settings()["max_tokens"] == 8192
    status = operations.narrative_analysis_status(run_id)
    assert status["budget"]["max_tokens_per_round"] == 131072

    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30).run()
    run_input = next(widget for widget in app.text_input if widget.label == "任务 ID")
    run_input.set_value(run_id).run()
    assert not app.exception
    assert any(
        "每轮输出设置：任务指定 · 131072 token · max_tokens" in str(item.value)
        for item in app.caption
    )
    assert any("候选：1" in str(item.value) for item in app.caption)
    assert (operations.project_info().revision, operations.candidates()) == before


def test_historical_integer_output_limit_remains_frozen_in_ui(monkeypatch):
    project_dir = Path(__file__).resolve().parents[1] / "data"
    project_path = project_dir / "wolf-agent-reviewed-fix.sqlite3"
    if not project_path.exists():
        pytest.skip("isolated historical agent project is not present")
    run_id = "671ee78635fd469a9d91ab49f545b8f8"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "wolf-agent-reviewed-fix")
    operations = Operations.open_project(project_dir, "wolf-agent-reviewed-fix")
    before = (operations.project_info().revision, operations.candidates())
    status = operations.narrative_analysis_status(run_id)
    assert status["budget"]["max_tokens_per_round"] == 8192
    assert status["config"]["max_tokens"] == 8192

    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30).run()
    next(widget for widget in app.text_input if widget.label == "任务 ID").set_value(run_id).run()
    assert not app.exception
    assert any(
        "历史每轮输出上限：8192 token · max_tokens" in str(item.value)
        for item in app.caption
    )
    assert (operations.project_info().revision, operations.candidates()) == before

