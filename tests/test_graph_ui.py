from pathlib import Path

from streamlit.testing.v1 import AppTest

from narrative.graph_operations import GraphOperations
from narrative.graph_ui import TYPE_LABELS
from narrative.operations import Operations


def _seed_graph(path: Path) -> None:
    graph = Operations.create_graph_project(path)
    source_id = graph.import_source("第一章", "甲认识乙。")
    snapshot = graph.graph_snapshot()
    evidence = graph.locate(source_id, "甲认识乙。")[0]
    objects = [
        {"kind": "node", "id": "person-a", "node_type": "person", "name": "甲", "aliases": ["阿甲"]},
        {"kind": "node", "id": "person-b", "node_type": "person", "name": "乙", "aliases": []},
        {
            "kind": "predicate", "id": "knows", "label": "认识", "semantic": "relation",
            "subject_types": ["person"], "object_types": ["person"], "value_type": "node",
        },
        evidence,
        {
            "kind": "assertion", "id": "assertion-ab", "subject_id": "person-a",
            "predicate_id": "knows", "object_id": "person-b", "scope": "world",
            "certainty": "certain", "assessment": "supported",
        },
        {
            "kind": "evidence_link", "id": "evidence-link-ab", "assertion_id": "assertion-ab",
            "evidence_id": evidence["id"], "role": "supports",
        },
    ]
    candidate_id = graph.graph_propose(objects, snapshot["revision"])
    candidate = graph.graph_candidate(candidate_id)
    graph.graph_accept(candidate_id, candidate["version"])


def test_new_workbench_creates_isolated_project_and_imports_source(tmp_path, monkeypatch):
    project_dir = tmp_path / "projects"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30).run()
    assert not app.exception
    assert "叙事图工作台" in str(app.title[0].value)
    next(widget for widget in app.text_input if widget.label == "作品名称").set_value("测试作品")
    next(widget for widget in app.text_input if widget.label == "工程代号").set_value("ui-new-project")
    next(button for button in app.button if button.label == "创建 v4 工程").click().run()
    assert not app.exception
    assert "测试作品" in str(app.title[0].value)
    app.radio(key="graph:nav").set_value("原文证据").run()
    next(widget for widget in app.text_input if widget.label == "章节标题").set_value("第一章")
    next(widget for widget in app.text_area if widget.label == "原文正文").set_value("甲出门。")
    next(button for button in app.button if button.label == "保存原文").click().run()
    assert not app.exception
    assert next(widget for widget in app.selectbox if widget.label == "查看章节").value


def test_v4_analysis_and_node_detail_show_readable_direct_relation(tmp_path, monkeypatch):
    project_dir = tmp_path / "projects"
    project_dir.mkdir()
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    path = project_dir / "readable.sqlite3"
    _seed_graph(path)
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30)
    app.session_state["graph:path"] = str(path)
    view = app.run()
    assert not view.exception
    assert any("甲" in str(element.value) for element in view.markdown)
    assert any("直接断言边" in str(element.value) for element in view.markdown)
    view.radio(key="graph:nav").set_value("实体表").run()
    assert not view.exception
    view.selectbox(key="graph:entity-select").set_value("person-a").run()
    assert any("甲认识乙" in str(element.value) for element in view.markdown)
    assert any(button.label == "打开：人物 · 乙" for button in view.button), [button.label for button in view.button]
    next(button for button in view.button if button.label == "打开：人物 · 乙").click().run()
    assert not view.exception
    assert view.selectbox(key="graph:entity-select").value == "person-b"


def test_candidate_review_shows_readable_object_and_accepts_through_operations(tmp_path, monkeypatch):
    project_dir = tmp_path / "projects"
    project_dir.mkdir()
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    path = project_dir / "candidate.sqlite3"
    _seed_graph(path)
    graph = Operations.open_graph_project(path)
    revision = graph.graph_project_info()["revision"]
    candidate_id = graph.graph_propose(
        [{"kind": "node", "id": "item-key", "node_type": "item", "name": "铜钥匙"}],
        revision,
        source="manual",
    )
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30)
    app.session_state["graph:path"] = str(path)
    view = app.run()
    view.radio(key="graph:nav").set_value("提取成果").run()
    assert not view.exception
    assert any("铜钥匙" in str(element.value) for element in view.markdown)
    assert any("待审核" in str(element.value) for element in view.caption)
    next(button for button in view.button if button.label == "接受并进入正式图").click().run()
    assert not view.exception
    assert graph.graph_candidate(candidate_id)["status"] == "accepted"


def test_pending_candidate_can_start_a_seeded_agent_revision(tmp_path, monkeypatch):
    project_dir = tmp_path / "projects"
    project_dir.mkdir()
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    path = project_dir / "revision.sqlite3"
    _seed_graph(path)
    graph = Operations.open_graph_project(path)
    candidate_id = graph.graph_propose(
        [{"kind": "node", "id": "candidate-item", "node_type": "item", "name": "铜钥匙"}],
        graph.graph_project_info()["revision"],
        source="model",
    )
    started = {}

    def fake_start(self, source_ids, **kwargs):
        started["source_ids"] = source_ids
        started.update(kwargs)
        return "f" * 32

    monkeypatch.setattr(GraphOperations, "start_graph_analysis", fake_start)
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30)
    app.session_state["graph:path"] = str(path)
    view = app.run()
    view.radio(key="graph:nav").set_value("提取成果").run()
    next(button for button in view.button if button.label == "交给 Agent 修订此候选").click().run()
    assert not view.exception
    assert any("修订起点" in str(element.value) for element in view.info)
    next(widget for widget in view.text_area if widget.label == "修订意见").set_value("请拆分个体，并用原文证据补齐参与角色。")
    next(button for button in view.button if button.label == "启动图分析").click().run()
    assert not view.exception
    assert started["seed_candidate_id"] == candidate_id
    assert started["feedback"] == "请拆分个体，并用原文证据补齐参与角色。"
    assert started["source_ids"] == [graph.sources()[0]["id"]]
    assert graph.graph_candidate(candidate_id)["status"] == "pending"


def test_asset_library_shows_imported_source_and_evidence_anchor(tmp_path, monkeypatch):
    project_dir = tmp_path / "projects"
    project_dir.mkdir()
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    path = project_dir / "asset-library.sqlite3"
    _seed_graph(path)
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30)
    app.session_state["graph:path"] = str(path)
    view = app.run()
    view.radio(key="graph:nav").set_value("资产库").run()
    assert not view.exception
    assert any("已导入原文" in str(element.value) for element in view.markdown)
    assert any("甲认识乙" in str(element.value) for element in view.markdown)
    assert any("在原文与证据中打开" == button.label for button in view.button)


def test_analysis_neighborhood_uses_operations_and_shows_returned_path(tmp_path, monkeypatch):
    project_dir = tmp_path / "projects"
    project_dir.mkdir()
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    path = project_dir / "neighborhood.sqlite3"
    graph = Operations.create_graph_project(path)
    source_id = graph.import_source("第一章", "甲在码头送别乙。")
    evidence = graph.locate(source_id, "甲在码头送别乙")[0]
    objects = [
        {"kind": "node", "id": "person-a", "node_type": "person", "name": "甲"},
        {"kind": "node", "id": "place", "node_type": "location", "name": "码头"},
        {"kind": "node", "id": "event", "node_type": "event", "name": "送别"},
        {"kind": "predicate", "id": "participates", "label": "参与", "semantic": "participation", "subject_types": ["event"], "object_types": ["person"], "value_type": "node"},
        {"kind": "predicate", "id": "occurs-at", "label": "发生于", "semantic": "relation", "subject_types": ["event"], "object_types": ["location"], "value_type": "node"},
        evidence,
        {"kind": "assertion", "id": "takes-part", "subject_id": "event", "predicate_id": "participates", "object_id": "person-a", "qualifiers": {"role": "送行者"}},
        {"kind": "assertion", "id": "place-of", "subject_id": "event", "predicate_id": "occurs-at", "object_id": "place"},
        {"kind": "evidence_link", "id": "supports-part", "assertion_id": "takes-part", "evidence_id": evidence["id"]},
        {"kind": "evidence_link", "id": "supports-place", "assertion_id": "place-of", "evidence_id": evidence["id"]},
    ]
    candidate_id = graph.graph_propose(objects, graph.graph_project_info()["revision"], source="manual")
    graph.graph_accept(candidate_id, graph.graph_candidate(candidate_id)["version"])
    calls = []
    original = GraphOperations.graph_neighborhood

    def record_call(self, center_id, **kwargs):
        calls.append((center_id, kwargs))
        return original(self, center_id, **kwargs)

    monkeypatch.setattr(GraphOperations, "graph_neighborhood", record_call)
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30)
    app.session_state["graph:path"] = str(path)
    view = app.run()
    assert not view.exception
    view.selectbox(key="graph:neighborhood-center").set_value("person-a").run()
    assert not view.exception
    assert any("推导路径" in str(element.value) for element in view.markdown)
    assert any("码头" in str(element.value) and "送别" in str(element.value) for element in view.markdown)
    assert calls and calls[-1][1]["depth"] == 2
    assert calls[-1][1]["node_types"] == list(TYPE_LABELS)


def test_incomplete_run_usage_is_not_rendered_as_zero(tmp_path, monkeypatch):
    project_dir = tmp_path / "projects"
    project_dir.mkdir()
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    path = project_dir / "unknown-usage.sqlite3"
    _seed_graph(path)
    run_id = "0" * 32
    monkeypatch.setattr(
        GraphOperations,
        "graph_analysis_runs",
        lambda self: [{"run_id": run_id, "status": "incomplete", "phase": "research", "source_ids": [], "rounds": 1, "modified_at": 0}],
    )
    monkeypatch.setattr(
        GraphOperations,
        "graph_analysis_trace",
        lambda self, identity: {
            "run_id": identity,
            "status": "incomplete",
            "request_state": "stopped",
            "phase": "research",
            "rounds": 1,
            "tool_count": 0,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "incomplete": True},
            "candidate_ids": [],
            "steps": [],
            "error": "模型请求失败；用量未报告",
        },
    )
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30)
    app.session_state["graph:path"] = str(path)
    view = app.run()
    view.radio(key="graph:nav").set_value("提取").run()
    assert not view.exception
    assert any("Usage：未报告（用量记录不完整）" in str(element.value) for element in view.caption)
    assert not any("0 tokens" in str(element.value) for element in view.caption)
    assert any("当前任务：未完成" in str(element.value) for element in view.markdown)
    assert any("模型请求失败；用量未报告" in str(element.value) for element in view.error)
    assert not any("候选当前状态" in str(element.value) for element in view.info)


def test_run_displays_recorded_agent_instruction_and_consumption_state(tmp_path, monkeypatch):
    project_dir = tmp_path / "projects"
    project_dir.mkdir()
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(project_dir))
    path = project_dir / "instructions.sqlite3"
    _seed_graph(path)
    run_id = "1" * 32
    monkeypatch.setattr(
        GraphOperations,
        "graph_analysis_runs",
        lambda self: [{"run_id": run_id, "status": "running", "phase": "correction", "request_state": "in_flight", "source_ids": [], "rounds": 3, "modified_at": 0, "observer_state": "unverified_external"}],
    )
    monkeypatch.setattr(
        GraphOperations,
        "graph_analysis_trace",
        lambda self, identity: {
            "run_id": identity,
            "status": "running",
            "request_state": "in_flight",
            "observer_state": "unverified_external",
            "phase": "correction",
            "rounds": 3,
            "tool_count": 2,
            "usage": {"total_tokens": 10, "incomplete": False},
            "candidate_ids": [],
            "steps": [],
            "control": {"paused": False, "cancel_requested": False, "instructions": [{"seq": 1, "id": "internal", "text": "现在起草一批并通过整批预检。", "created_at": "2026-10-18T00:00:00Z", "consumed_at": None}]},
        },
    )
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=30)
    app.session_state["graph:path"] = str(path)
    view = app.run()
    view.radio(key="graph:nav").set_value("提取").run()
    assert not view.exception
    assert any("第 1 条 · 已排队，等待后续模型请求" in str(element.value) and "现在起草一批" in str(element.value) for element in view.info)
    assert any("外部进程" in str(element.value) for element in view.warning)

