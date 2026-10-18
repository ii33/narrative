from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_workbench_demo_review_and_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=20).run()
    assert not app.exception
    next(b for b in app.button if b.label == "载入自编演示").click().run()
    assert not app.exception
    assert app.tabs[0].label == "人物与事件地图"
    assert any(tab.label == "候选审核" for tab in app.tabs)
    assert any("沈砚的说法" in str(m.value) for m in app.markdown)
    next(b for b in app.button if b.label == "接受并保存正式资料").click().run()
    assert not app.exception
    reopened = AppTest.from_file(str(ui_path), default_timeout=20).run()
    assert not reopened.exception
    assert any("已丢失" in e.label for e in reopened.expander)
    assert any("claim" in str(j.value) for j in reopened.json)


def test_project_error_visible_outside_collapsed_sidebar(tmp_path, monkeypatch):
    from narrative.operations import OperationError, Operations

    monkeypatch.chdir(tmp_path)

    def fail(*args, **kwargs):
        raise OperationError("测试项目打开失败")

    monkeypatch.setattr(Operations, "open_project", fail)
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=20).run()
    assert not app.exception
    assert "测试项目打开失败" in app.main.error[0].value
    assert any(b.label == "重试加载项目" for b in app.main.button)


def test_timeline_ui_uses_operations_for_act_and_evidenced_time_candidates(
    tmp_path, monkeypatch
):
    from narrative.operations import Operations

    directory = tmp_path / "projects"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(directory))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "ui-temporal")
    operations = Operations.open_project(directory, "ui-temporal")
    source_id = operations.import_text(
        "时间修订 UI",
        "甲先到门口。后来甲再看信。甲穿着外套。",
    )
    first_evidence = operations.locate(source_id, "甲先到门口。")[0]
    later_evidence = operations.locate(source_id, "后来甲再看信。")[0]
    outfit_evidence = operations.locate(source_id, "甲穿着外套。")[0]

    seed = operations.propose_objects(
        [
            {"kind": "entity", "id": "person-a", "name": "甲", "entity_type": "person"},
            {"kind": "entity", "id": "item-letter", "name": "信", "entity_type": "item"},
            {"kind": "entity", "id": "item-coat", "name": "外套", "entity_type": "item"},
            {
                "kind": "entity",
                "id": "location-door",
                "name": "门口",
                "entity_type": "location",
            },
            {
                "kind": "state",
                "id": "state-outfit",
                "subject_id": "person-a",
                "property_id": "outfit",
                "value_entity_id": "item-coat",
                "evidence": [outfit_evidence.model_dump(mode="json")],
            },
        ]
    )
    operations.accept_candidate(seed, operations.review_candidate(seed).version)

    first = operations.propose_event(
        title="甲到门口",
        action="act",
        actor_id="person-a",
        target_id="location-door",
        evidence=first_evidence,
    )
    operations.accept_candidate(first, operations.review_candidate(first).version)
    later = operations.propose_event(
        title="甲看信",
        action="act",
        actor_id="person-a",
        target_id="item-letter",
        evidence=later_evidence,
    )
    operations.accept_candidate(later, operations.review_candidate(later).version)
    first_event_id = operations.events()[0]["id"]
    later_event_id = operations.events()[1]["id"]

    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=25).run()
    assert not app.exception
    action_widget = next(widget for widget in app.selectbox if widget.label == "动作")
    assert "叙事动作" in action_widget.options
    assert any(button.label == "建立先后修订候选" for button in app.button)
    assert any(button.label == "建立状态起止修订候选" for button in app.button)

    next(widget for widget in app.selectbox if widget.label == "查看事件与前后状态").set_value(
        later_event_id
    ).run()
    next(widget for widget in app.selectbox if widget.label == "该事件发生在此事件之后").set_value(
        first_event_id
    ).run()
    next(widget for widget in app.text_area if widget.label == "支持先后关系的原文片段").set_value(
        "后来甲再看信。"
    ).run()
    next(button for button in app.button if button.label == "建立先后修订候选").click().run()
    assert not app.exception
    assert len(operations.events()) == 2
    assert len(operations.candidates(status="pending")) == 1

    next(widget for widget in app.selectbox if widget.label == "新终点（保持原有设置）").set_value(
        later_event_id
    ).run()
    next(widget for widget in app.text_area if widget.label == "支持状态起止的原文片段").set_value(
        "甲穿着外套。"
    ).run()
    next(button for button in app.button if button.label == "建立状态起止修订候选").click().run()
    assert not app.exception
    assert len(operations.candidates(status="pending")) == 2
    state = next(row for row in operations.records("state") if row["id"] == "state-outfit")
    assert state["validity"]["end_event_id"] is None

    next(widget for widget in app.selectbox if widget.label == "动作").set_value("叙事动作").run()
    next(widget for widget in app.text_input if widget.label == "事件标题").set_value(
        "甲折起信纸"
    ).run()
    next(widget for widget in app.selectbox if widget.label == "行动人物").set_value(
        "person-a"
    ).run()
    next(widget for widget in app.text_area if widget.label == "事件证据原句").set_value(
        "后来甲再看信。"
    ).run()
    next(widget for widget in app.selectbox if widget.label == "事件证据位置").set_value(0).run()
    next(button for button in app.button if button.label == "提交事件候选").click().run()
    assert not app.exception
    assert len(operations.events()) == 2
    assert len(operations.candidates(status="pending")) == 3
