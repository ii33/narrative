import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from narrative.operations import OperationError, Operations


def by_label(elements, label):
    return next(element for element in elements if element.label == label)


@pytest.fixture
def complete_project(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "complete")
    monkeypatch.setattr(
        "narrative.operations.credential_status", lambda: {"available": False, "source": "none"}
    )
    monkeypatch.setattr(
        "narrative.jobs.start", lambda *args, **kwargs: pytest.fail("此验收不应调用模型")
    )
    app = Operations.open_project(tmp_path, "complete")
    source = app.import_text("测试章", "甲换上蓝衣。乙左臂受伤。甲说门开着。门关着。门关着。")
    evidence = app.locate(source, "甲说门开着。")[0].model_dump()
    objects = [
        {"kind": "entity", "id": "a", "name": "甲"},
        {"kind": "entity", "id": "b", "name": "乙"},
        {"kind": "entity", "id": "coat", "name": "蓝衣", "entity_type": "item"},
        {"kind": "entity", "id": "door", "name": "门", "entity_type": "item"},
        {
            "kind": "assertion",
            "id": "claim",
            "subject_id": "door",
            "predicate": "开着",
            "value": True,
            "scope": "claim",
            "holder_id": "a",
            "evidence": [evidence],
        },
        {
            "kind": "property",
            "id": "open",
            "name": "是否打开",
            "value_type": "boolean",
            "persistence": "until_changed",
            "origin": "creative",
        },
        {
            "kind": "state",
            "id": "closed",
            "subject_id": "door",
            "property_id": "open",
            "value": False,
            "validity": {"start": 0},
            "evidence": [app.locate(source, "门关着。")[0].model_dump()],
        },
        {
            "kind": "knowledge",
            "id": "heard",
            "holder_id": "b",
            "assertion_id": "claim",
            "attitude": "informed",
            "acquisition": "told",
            "source_character_id": "a",
            "validity": {"start": 2},
            "evidence": [evidence],
        },
    ]
    candidate = app.propose_objects(objects)
    app.accept_candidate(candidate, 1)
    return app, source


def workbench():
    path = Path(__file__).resolve().parents[1] / "src/narrative/ui.py"
    ui = AppTest.from_file(str(path), default_timeout=25).run()
    assert not ui.exception
    return ui


def test_full_model_queries_render_false_and_epistemic_state_without_writes(complete_project):
    operations, _ = complete_project
    revision = operations.project_info().revision
    ui = workbench()
    assert "资料模型" in [tab.label for tab in ui.tabs]
    by_label(ui.number_input, "查询时刻（相对分钟）").set_value(3.0)
    by_label(ui.button, "查询此刻状态").click().run()
    assert not ui.exception
    rows = [row for frame in ui.dataframe for row in frame.value.to_dict("records")]
    assert any(row.get("属性") == "是否打开" and row.get("此刻状态") == "否" for row in rows)
    assert any(
        row["property_id"] == "open" and row["value"] is False
        for row in operations.state_at_time(3)["states"]
    )
    assert any(row.get("认知态度") == "听说" for row in rows)
    assert operations.project_info().revision == revision
    assert operations.assertions()[0].scope == "claim"


@pytest.mark.parametrize("action", ["人物换装", "人物受伤", "告知信息"])
def test_new_actions_are_accessible_and_reviewable(complete_project, action):
    operations, source = complete_project
    ui = workbench()
    by_label(ui.selectbox, "动作").set_value(action).run()
    assert not ui.exception
    by_label(ui.text_input, "事件标题").set_value("新动作")
    by_label(ui.selectbox, "行动人物").set_value("a")
    by_label(ui.selectbox, "时间方式").set_value("时间点").run()
    by_label(ui.number_input, "相对分钟").set_value(5.0)
    if action == "人物换装":
        by_label(ui.selectbox, "穿戴物品").set_value("coat")
        quote = "甲换上蓝衣。"
    elif action == "人物受伤":
        by_label(ui.selectbox, "受伤或接受处理的人物").set_value("b")
        quote = "乙左臂受伤。"
    else:
        by_label(ui.selectbox, "获知人物").set_value("b")
        by_label(ui.selectbox, "告知的命题").set_value("claim")
        quote = "甲说门开着。"
    by_label(ui.selectbox, "事件证据章节").set_value(source)
    by_label(ui.text_area, "事件证据原句").set_value(quote).run()
    by_label(ui.selectbox, "事件证据位置").set_value(0).run()
    by_label(ui.button, "提交事件候选").click().run()
    assert not ui.exception
    candidate = operations.candidates(status="pending")[0]
    review = operations.review_candidate(candidate["id"])
    assert not [d for d in review.diagnostics if d.severity == "error"]
    assert review.change.operations[0].kind == "event"


def test_new_record_recovery_does_not_mix_entry_indexes(complete_project):
    operations, source = complete_project
    run = operations.create_run(source)
    chunk = operations.export_run(run)["chunks"][0]["chunk_id"]
    response = {
        "states": [
            {
                "ref": "s",
                "subject_ref": "door",
                "property_id": "open",
                "value": False,
                "validity": {"start": 5},
                "quote": "门关着。",
            }
        ]
    }
    with pytest.raises(OperationError, match="多个位置"):
        operations.ingest_result(chunk, json.dumps(response))
    assert operations.chunks(run)[0]["status"] == "failed"
    ui = workbench()
    assert not ui.exception
    assert any("状态第 1 条" in expander.label for expander in ui.expander)
    assert by_label(ui.button, "保存修正并导入候选").disabled


def test_schema_export_and_formal_validation_are_read_only(complete_project):
    operations, _ = complete_project
    before = operations.project_snapshot()
    schema = operations.model_schema()
    assert schema["schema_version"] == 3
    assert set(operations.record_templates()) <= {
        definition["properties"]["kind"]["const"]
        for definition in schema["change_set"]["$defs"].values()
        if "kind" in definition.get("properties", {})
    }
    assert not [d for d in operations.validate_project() if d["severity"] == "error"]
    assert operations.project_snapshot() == before


def test_event_and_time_queries_share_the_complete_state_engine(complete_project):
    operations, source = complete_project
    candidate = operations.propose_objects(
        [
            {
                "kind": "event",
                "id": "opened",
                "title": "开门",
                "action": "observe",
                "time_kind": "point",
                "time_start": 5,
                "preconditions": [{"subject_id": "door", "property_id": "open", "value": False}],
                "effects": [{"subject_id": "door", "property_id": "open", "value": True}],
                "evidence": [operations.locate(source, "甲说门开着。")[0].model_dump()],
            }
        ]
    )
    operations.accept_candidate(candidate, 1)
    event = operations.event_state("opened")
    before = next(
        row
        for row in event["before"]["states"]
        if row["subject_id"] == "door" and row["property_id"] == "open"
    )
    after = next(
        row
        for row in event["after"]["states"]
        if row["subject_id"] == "door" and row["property_id"] == "open"
    )
    assert before["value"] is False and after["value"] is True
    assert after == next(
        row
        for row in operations.state_at_time(6)["states"]
        if row["subject_id"] == "door" and row["property_id"] == "open"
    )
    assert not [row for row in operations.timeline()["diagnostics"] if row["severity"] == "error"]
