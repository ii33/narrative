"""Exercise saved-result recovery through the real workbench and Operations boundary."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from narrative.operations import OperationError, Operations

UI_PATH = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
MODEL_METADATA = {"model": "saved-test-model", "usage": {"total_tokens": 456}}


def result(quote):
    return json.dumps(
        {
            "entities": [{"ref": "a", "name": "甲"}],
            "assertions": [
                {
                    "subject_ref": "a",
                    "predicate": "动作",
                    "value": "敲门",
                    "quote": quote,
                }
            ],
        },
        ensure_ascii=False,
    )


def prepare(tmp_path, monkeypatch, raw):
    directory = tmp_path / "data"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(directory))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "demo")
    monkeypatch.setattr(
        "narrative.operations.credential_status", lambda: {"available": False, "source": "none"}
    )

    def no_model_call(*args, **kwargs):
        pytest.fail("修正保存结果不应调用模型")

    monkeypatch.setattr("narrative.jobs.start", no_model_call)
    operations = Operations.open_project(directory, "demo")
    text = "甲敲门。\r\n甲说门锁着。🙂\r\n甲敲门。"
    source_id = operations.import_text("待修正章节", text)
    run_id = operations.create_run(source_id)
    chunk_id = operations.chunks(run_id)[0]["id"]
    with pytest.raises(OperationError):
        operations.ingest_result(chunk_id, raw, metadata=MODEL_METADATA)
    return operations, run_id, chunk_id, text


def open_workbench():
    app = AppTest.from_file(str(UI_PATH), default_timeout=20).run()
    assert not app.exception
    return app


def save_button(app):
    return next(button for button in app.button if button.label == "保存修正并导入候选")


def assert_recovered(operations, run_id, chunk_id, raw, quote, expected_start):
    assert len(operations.candidates(status="pending")) == 1
    assert not operations.entities() and not operations.assertions()
    change_id = operations.candidates(status="pending")[0]["id"]
    candidate = operations.review_candidate(change_id)
    assert not [d for d in candidate.diagnostics if d.severity == "error"]
    evidence = next(op for op in candidate.change.operations if op.kind == "assertion").evidence[0]
    assert evidence.quote == quote and evidence.start == expected_start
    chunk = next(c for c in operations.chunks(run_id) if c["id"] == chunk_id)
    assert chunk["status"] == "processed" and chunk["error"] is None
    metadata = json.loads(chunk["metadata"])
    assert metadata["model_response"] == {"raw": raw, "metadata": MODEL_METADATA}
    assert len(metadata["manual_corrections"]) == 1
    assert metadata["manual_corrections"][0]["previous_raw"] == raw


def test_recovery_panel_selects_second_repeated_quote_and_keeps_pending(tmp_path, monkeypatch):
    raw = result("甲敲门。")
    operations, run_id, chunk_id, text = prepare(tmp_path, monkeypatch, raw)
    revision = operations.project_info().revision
    app = open_workbench()
    assert any(expander.label == "修正证据后重新导入" for expander in app.expander)
    assert save_button(app).disabled
    position = next(item for item in app.selectbox if item.label == "证据位置 · 第 1 条")
    assert len(position.options) == 3  # Placeholder plus both exact occurrences.

    second_start = text.rindex("甲敲门。")
    position.select(second_start).run()
    assert not app.exception and not save_button(app).disabled
    save_button(app).click().run()
    assert not app.exception
    assert any("已生成候选" in notice.value for notice in app.success)
    assert_recovered(operations, run_id, chunk_id, raw, "甲敲门。", second_start)
    assert operations.project_info().revision == revision
    metadata = json.loads(operations.chunks(run_id)[0]["metadata"])
    assert metadata["manual_corrections"][0]["selected_quote_starts"] == {"0": second_start}

    reopened = open_workbench()
    assert not any(expander.label == "修正证据后重新导入" for expander in reopened.expander)
    assert not any(button.label == "保存修正并导入候选" for button in reopened.button)
    assert len(operations.candidates(status="pending")) == 1


def test_recovery_panel_edits_missing_quote_then_imports_exact_evidence(tmp_path, monkeypatch):
    raw = result("甲说门锁着!")
    operations, run_id, chunk_id, text = prepare(tmp_path, monkeypatch, raw)
    app = open_workbench()
    assert save_button(app).disabled
    quote = next(item for item in app.text_area if item.label == "引文 · 第 1 条")
    quote.set_value("甲说门锁着。").run()
    assert not app.exception
    position = next(item for item in app.selectbox if item.label == "证据位置 · 第 1 条")
    start = text.index("甲说门锁着。")
    position.select(start).run()
    assert not app.exception and not save_button(app).disabled
    save_button(app).click().run()
    assert not app.exception
    assert_recovered(operations, run_id, chunk_id, raw, "甲说门锁着。", start)
    metadata = json.loads(operations.chunks(run_id)[0]["metadata"])
    edit = json.loads(metadata["manual_corrections"][0]["edited_raw"])
    assert edit["assertions"][0]["quote"] == "甲说门锁着。"


def test_recovery_panel_repairs_malformed_json_without_model_request(tmp_path, monkeypatch):
    raw = "not valid JSON"
    operations, run_id, chunk_id, text = prepare(tmp_path, monkeypatch, raw)
    app = open_workbench()
    assert any("结果格式尚未通过校验" in notice.value for notice in app.info)
    assert not any(button.label == "保存修正并导入候选" for button in app.button)
    editor = next(item for item in app.text_area if item.label == "待修正的提取结果 JSON")
    editor.set_value(result("甲说门锁着。")).run()
    assert not app.exception and not save_button(app).disabled
    save_button(app).click().run()
    assert not app.exception
    assert_recovered(operations, run_id, chunk_id, raw, "甲说门锁着。", text.index("甲说门锁着。"))
