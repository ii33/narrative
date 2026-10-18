from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from narrative.operations import Operations


@pytest.fixture
def timed_project(tmp_path, monkeypatch):
    directory = tmp_path / "projects"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(directory))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "time-query")
    monkeypatch.setattr(
        "narrative.operations.credential_status", lambda: {"available": False, "source": "none"}
    )

    def no_model_call(*args, **kwargs):
        pytest.fail("故事时刻查询与时间轴排列不应调用模型")

    monkeypatch.setattr("narrative.jobs.start", no_model_call)
    operations = Operations.open_project(directory, "time-query")
    source_id = operations.import_text(
        "倒叙交接", "后来，甲进入大厅。\n此前，甲把铜钥匙交给乙。\n乙随后把铜钥匙还给甲。"
    )

    def accept(change_id):
        review = operations.review_candidate(change_id)
        operations.accept_candidate(change_id, review.version)

    for name, kind in (
        ("甲", "person"),
        ("乙", "person"),
        ("铜钥匙", "item"),
        ("大厅", "location"),
    ):
        accept(operations.propose_entity(name, kind, []))
    refs = {entity["name"]: entity["id"] for entity in operations.entities()}
    for minute, title, quote, fields in (
        (
            30,
            "甲进入大厅",
            "后来，甲进入大厅。",
            {"action": "move", "actor_id": refs["甲"], "location_id": refs["大厅"]},
        ),
        (
            10,
            "铜钥匙交给乙",
            "此前，甲把铜钥匙交给乙。",
            {
                "action": "transfer",
                "actor_id": refs["甲"],
                "target_id": refs["乙"],
                "item_id": refs["铜钥匙"],
            },
        ),
        (
            20,
            "铜钥匙交还甲",
            "乙随后把铜钥匙还给甲。",
            {
                "action": "transfer",
                "actor_id": refs["乙"],
                "target_id": refs["甲"],
                "item_id": refs["铜钥匙"],
            },
        ),
    ):
        accept(
            operations.propose_event(
                title=title,
                time_kind="point",
                time_start=minute,
                evidence=operations.locate(source_id, quote)[0],
                **fields,
            )
        )
    return operations


def _workbench():
    path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    app = AppTest.from_file(str(path), default_timeout=20).run()
    assert not app.exception
    return app


def _by_label(elements, label):
    return next(element for element in elements if element.label == label)


def _query(app, minute, include_same_time=True):
    _by_label(app.number_input, "查询时刻（相对分钟）").set_value(float(minute))
    _by_label(app.radio, "同刻事件边界").set_value(
        "包含同刻结束的事件" if include_same_time else "不包含同刻结束的事件"
    )
    _by_label(app.button, "查询此刻状态").click().run()
    assert not app.exception
    frames = [
        frame.value
        for frame in app.dataframe
        if set(frame.value.columns) == {"对象", "此刻状态", "依据与未知原因"}
    ]
    assert len(frames) == 2
    assert any("物品持有者（时刻查询）" in str(markdown.value) for markdown in app.markdown)
    assert any("人物所在地（时刻查询）" in str(markdown.value) for markdown in app.markdown)
    return {row["对象"]: row for frame in frames for row in frame.to_dict("records")}


def _evidence_text(app):
    panel = _by_label(app.expander, "时刻查询证据")
    text = "\n".join(str(element.value) for element in panel.markdown)
    assert any("字符" in str(caption.value) for caption in panel.caption)
    return text


def test_query_time_boundaries_unknowns_evidence_and_no_write(timed_project):
    operations = timed_project
    revision = operations.project_info().revision
    app = _workbench()
    for minute, include_same_time, holder, location, evidence_quote in (
        (5, True, "未知", "未知", None),
        (15, True, "乙", "未知", "此前，甲把铜钥匙交给乙。"),
        (20, False, "乙", "未知", "此前，甲把铜钥匙交给乙。"),
        (20, True, "甲", "未知", "乙随后把铜钥匙还给甲。"),
        (35, True, "甲", "大厅", "后来，甲进入大厅。"),
    ):
        rows = _query(app, minute, include_same_time)
        assert rows["铜钥匙"]["此刻状态"] == holder
        assert rows["甲"]["此刻状态"] == location
        assert rows["乙"]["此刻状态"] == "未知"
        assert all(row["依据与未知原因"] for row in rows.values())
        if evidence_quote:
            assert evidence_quote in _evidence_text(app)
        assert operations.project_info().revision == revision


def test_query_independent_of_display_order_and_safe_after_reopen(timed_project):
    operations = timed_project
    revision = operations.project_info().revision
    app = _workbench()
    expected = _query(app, 35)
    for order in ("原文披露顺序", "故事发生顺序"):
        _by_label(app.radio, "排列方式").set_value(order).run()
        assert not app.exception
        assert _query(app, 35) == expected
    reopened = _workbench()
    assert _query(reopened, 35) == expected
    assert operations.project_info().revision == revision
