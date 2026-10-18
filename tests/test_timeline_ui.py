from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from narrative.operations import Operations


@pytest.fixture
def timeline_project(tmp_path, monkeypatch):
    directory = tmp_path / "projects"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(directory))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "timeline")
    monkeypatch.setattr(
        "narrative.operations.credential_status", lambda: {"available": False, "source": "none"}
    )

    def no_model_call(*args, **kwargs):
        pytest.fail("事件时间轴操作不应调用模型")

    monkeypatch.setattr("narrative.jobs.start", no_model_call)
    operations = Operations.open_project(directory, "timeline")
    source_id = operations.import_text("钥匙交接", "甲把铜钥匙交给乙。随后乙收好铜钥匙。")
    for name, kind in (("甲", "person"), ("乙", "person"), ("铜钥匙", "item")):
        change_id = operations.propose_entity(name, kind, [])
        review = operations.review_candidate(change_id)
        operations.accept_candidate(change_id, review.version)
    entities = {entity["name"]: entity["id"] for entity in operations.entities()}
    return operations, source_id, entities


def _workbench():
    path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    return AppTest.from_file(str(path), default_timeout=20).run()


def _by_label(elements, label):
    return next(element for element in elements if element.label == label)


def test_event_candidate_review_timeline_and_restart(timeline_project):
    operations, _, entities = timeline_project
    revision = operations.project_info().revision
    app = _workbench()
    assert not app.exception
    assert "事件与时间轴" in [tab.label for tab in app.tabs]
    _by_label(app.text_input, "事件标题").set_value("铜钥匙交接")
    _by_label(app.selectbox, "动作").select("物品交接").run()
    assert not app.exception
    _by_label(app.selectbox, "交出者").select(entities["甲"])
    _by_label(app.selectbox, "接收者").select(entities["乙"])
    _by_label(app.selectbox, "物品").select(entities["铜钥匙"])
    _by_label(app.selectbox, "时间方式").select("时间点").run()
    assert not app.exception
    _by_label(app.number_input, "相对分钟").set_value(10.0)
    _by_label(app.text_area, "事件证据原句").set_value("甲把铜钥匙交给乙。").run()
    assert not app.exception
    _by_label(app.selectbox, "事件证据位置").select(0).run()
    assert not app.exception
    _by_label(app.button, "提交事件候选").click().run()
    assert not app.exception
    assert not operations.events()
    assert operations.project_info().revision == revision
    pending = operations.candidates(status="pending")
    assert len(pending) == 1
    proposed = operations.review_candidate(pending[0]["id"]).change.operations[0]
    assert proposed.kind == "event" and proposed.time_start == 10.0

    _by_label(app.button, "接受并保存正式资料").click().run()
    assert not app.exception
    events = operations.events()
    assert len(events) == 1 and events[0]["title"] == "铜钥匙交接"
    state = operations.event_state(events[0]["id"])
    assert state["before"]["holders"][entities["铜钥匙"]] is None
    assert state["after"]["holders"][entities["铜钥匙"]] == entities["乙"]
    assert state["applied"]
    assert any("铜钥匙交接" in frame.value.to_string() for frame in app.dataframe)
    assert app.get("vega_lite_chart")
    holder_frames = [frame.value for frame in app.dataframe if "事件后" in frame.value.columns]
    assert any(
        {"对象": "铜钥匙", "事件前": "未知", "事件后": "乙"} in frame.to_dict("records")
        for frame in holder_frames
    )

    _by_label(app.radio, "排列方式").set_value("原文披露顺序").run()
    assert not app.exception
    _by_label(app.radio, "排列方式").set_value("故事发生顺序").run()
    assert not app.exception
    reopened = _workbench()
    assert not reopened.exception
    assert any("铜钥匙交接" in frame.value.to_string() for frame in reopened.dataframe)
    assert operations.project_info().revision == revision + 1
