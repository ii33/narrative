"""Model event candidates and saved-result recovery through the Operations/UI boundary."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from narrative.operations import OperationError, Operations

UI_PATH = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"


@pytest.fixture
def project(tmp_path, monkeypatch):
    directory = tmp_path / "projects"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(directory))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "model-events")
    monkeypatch.setattr(
        "narrative.operations.credential_status", lambda: {"available": False, "source": "none"}
    )

    def no_model_call(*args, **kwargs):
        pytest.fail("导入、修正和审核已保存的事件结果不应调用模型")

    monkeypatch.setattr("narrative.jobs.start", no_model_call)
    return Operations.open_project(directory, "model-events")


def workbench():
    page = AppTest.from_file(str(UI_PATH), default_timeout=20).run()
    assert not page.exception
    return page


def by_label(elements, label):
    return next(element for element in elements if element.label == label)


def entities():
    return [
        {"ref": "a", "name": "甲", "entity_type": "person"},
        {"ref": "b", "name": "乙", "entity_type": "person"},
        {"ref": "c", "name": "丙", "entity_type": "person"},
        {"ref": "key", "name": "铜钥匙", "entity_type": "item"},
    ]


def test_model_events_stay_pending_until_ui_review_then_drive_timeline_and_state(project):
    first_quote = "10分钟，甲把铜钥匙交给乙。"
    second_quote = "随后乙把铜钥匙交给丙。"
    source_id = project.import_text("先披露结果的插叙", second_quote + "\n" + first_quote)
    run_id = project.create_run(source_id)
    chunk_id = project.chunks(run_id)[0]["id"]
    payload = {
        "entities": entities(),
        "events": [
            {
                "ref": "second",
                "title": "乙交给丙",
                "action": "transfer",
                "actor_ref": "b",
                "target_ref": "c",
                "item_ref": "key",
                "time_kind": "relative",
                "after_event_ref": "first",
                "quote": second_quote,
            },
            {
                "ref": "first",
                "title": "甲交给乙",
                "action": "transfer",
                "actor_ref": "a",
                "target_ref": "b",
                "item_ref": "key",
                "time_kind": "point",
                "time_start": 10,
                "quote": first_quote,
            },
        ],
    }
    revision = project.project_info().revision
    candidate_id = project.ingest_result(chunk_id, json.dumps(payload, ensure_ascii=False))
    assert project.candidates(status="pending")[0]["id"] == candidate_id
    assert not project.events() and not project.entities()
    assert project.timeline()["rows"] == []
    assert project.project_info().revision == revision

    page = workbench()
    by_label(page.button, "接受并保存正式资料").click().run()
    assert not page.exception
    assert not project.candidates(status="pending")
    assert project.project_info().revision == revision + 1
    assert [row["title"] for row in project.timeline()["rows"]] == ["甲交给乙", "乙交给丙"]
    assert [row["title"] for row in project.timeline(order="disclosure")["rows"]] == [
        "乙交给丙",
        "甲交给乙",
    ]
    names = {entity["name"]: entity["id"] for entity in project.entities()}
    last = project.timeline()["rows"][-1]
    state = project.event_state(last["id"])
    assert state["before"]["holders"][names["铜钥匙"]] == names["乙"]
    assert state["after"]["holders"][names["铜钥匙"]] == names["丙"]
    assert state["applied"]
    assert page.get("vega_lite_chart")
    assert any("甲交给乙" in frame.value.to_string() for frame in page.dataframe)
    reopened = workbench()
    assert any("乙交给丙" in frame.value.to_string() for frame in reopened.dataframe)
    assert Operations(project.project_info().path).event_state(last["id"]) == state


def test_mixed_event_and_assertion_quote_repair_keeps_independent_positions_and_raw(project):
    quote = "甲把铜钥匙交给乙。"
    text = quote + "\r\n铜钥匙闪了一下。🙂\r\n" + quote
    source_id = project.import_text("重复原句", text)
    run_id = project.create_run(source_id)
    chunk_id = project.chunks(run_id)[0]["id"]
    payload = {
        "entities": entities()[:2] + entities()[3:],
        "assertions": [
            {"subject_ref": "key", "predicate": "交接记录", "value": "交给乙", "quote": quote}
        ],
        "events": [
            {
                "ref": "handoff",
                "title": "铜钥匙交接",
                "action": "transfer",
                "actor_ref": "a",
                "target_ref": "b",
                "item_ref": "key",
                "time_kind": "unknown",
                "quote": "甲把铜钥匙交给乙!",
            }
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False)
    model_metadata = {"model": "saved-event-model", "usage": {"total_tokens": 123}}
    with pytest.raises(OperationError):
        project.ingest_result(chunk_id, raw, metadata=model_metadata)
    assert not project.candidates() and not project.events() and not project.assertions()

    page = workbench()
    save = by_label(page.button, "保存修正并导入候选")
    assert save.disabled
    by_label(page.text_area, "事件引文 · 第 1 条").set_value(quote).run()
    assert not page.exception
    assertion_position = by_label(page.selectbox, "证据位置 · 第 1 条")
    event_position = by_label(page.selectbox, "事件证据位置 · 第 1 条")
    assert len(assertion_position.options) == len(event_position.options) == 3
    assertion_position.select(0)
    event_position.select(text.rindex(quote)).run()
    assert not page.exception
    assert not by_label(page.button, "保存修正并导入候选").disabled
    by_label(page.button, "保存修正并导入候选").click().run()
    assert not page.exception
    assert len(project.candidates(status="pending")) == 1
    assert not project.events() and not project.assertions()
    candidate = project.review_candidate(project.candidates(status="pending")[0]["id"])
    assert not [d for d in candidate.diagnostics if d.severity == "error"]
    evidence = {
        operation.kind: operation.evidence[0]
        for operation in candidate.change.operations
        if operation.kind in {"event", "assertion"}
    }
    assert evidence["assertion"].start == 0
    assert evidence["event"].start == text.rindex(quote)
    assert evidence["event"].quote == quote
    metadata = json.loads(project.chunks(run_id)[0]["metadata"])
    assert metadata["model_response"] == {"raw": raw, "metadata": model_metadata}
    correction = metadata["manual_corrections"][0]
    assert correction["previous_raw"] == raw
    assert correction["selected_quote_starts"] == {"0": 0}
    assert correction["selected_event_quote_starts"] == {"0": text.rindex(quote)}

    by_label(page.button, "接受并保存正式资料").click().run()
    assert not page.exception
    assert len(project.events()) == len(project.assertions()) == 1
    event = project.events()[0]
    ids = {entity["name"]: entity["id"] for entity in project.entities()}
    assert project.event_state(event["id"])["after"]["holders"][ids["铜钥匙"]] == ids["乙"]
    reopened = workbench()
    assert not any(expander.label == "修正证据后重新导入" for expander in reopened.expander)
    assert json.loads(project.chunks(run_id)[0]["metadata"])["model_response"]["raw"] == raw


def test_unknown_and_relative_model_events_render_graph_without_invented_time(project):
    examples = Path(__file__).resolve().parents[1] / "examples"
    text = (examples / "events_story.txt").read_text(encoding="utf-8")
    raw = (examples / "harness_events_result.json").read_text(encoding="utf-8")
    source_id = project.import_text("未定交接与相对移动", text)
    run_id = project.create_run(source_id)
    candidate = project.ingest_result(project.chunks(run_id)[0]["id"], raw)
    assert candidate is not None
    assert not project.events()
    page = workbench()
    by_label(page.button, "接受并保存正式资料").click().run()
    assert not page.exception

    rows = project.timeline()["rows"]
    assert [row["time_kind"] for row in rows] == ["unknown", "relative"]
    assert all(row["time_start"] is None and row["time_end"] is None for row in rows)
    first, second = rows
    assert first["id"] in second["predecessors"]
    graphs = page.get("graphviz_chart")
    assert len(graphs) == 1
    dot = graphs[0].proto.spec
    assert "林舟将钥匙交给沈砚" in dot and "沈砚随后进入仓库" in dot
    assert f"{json.dumps(first['id'])} -> {json.dumps(second['id'])};" in dot
    assert not page.get("vega_lite_chart")
    assert any("此图没有时间刻度" in caption.value for caption in page.caption)

    ids = {entity["name"]: entity["id"] for entity in project.entities()}
    state = project.event_state(second["id"])
    assert state["after"]["locations"][ids["沈砚"]] == ids["仓库"]
    assert state["after"]["holders"][ids["铜钥匙"]] == ids["沈砚"]
    assert state["applied"]
