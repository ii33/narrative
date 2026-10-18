import json
import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from narrative.model_gateway import UsageObserver
from narrative.operations import OperationError, Operations
from narrative.usage import normalize


@pytest.mark.parametrize(
    "value,expected",
    [
        ({"inputTokens": 10, "outputTokens": 4, "totalTokens": 14}, (10, 4, 14)),
        ({"prompt_tokens": 10, "completion_tokens": 4}, (10, 4, 14)),
        ({"input_tokens": 10, "output_tokens": 4, "total_tokens": 19}, (10, 4, 19)),
        ({"inputTokens": 0, "outputTokens": 0}, (0, 0, 0)),
        ({"total_tokens": 14}, (None, None, 14)),
        ({"inputTokens": 10}, (10, None, None)),
        ({"inputTokens": True, "outputTokens": -4, "totalTokens": "14"}, (None, None, None)),
        ({"inputTokens": 1.5, "outputTokens": 4.0}, (None, None, None)),
        (None, (None, None, None)),
        ([], (None, None, None)),
        ("malformed", (None, None, None)),
        ([{"inputTokens": 10, "outputTokens": 4}] * 2, (20, 8, 28)),
        ([{"inputTokens": 10, "outputTokens": 4}, None], (10, 4, 14)),
    ],
)
def test_usage_formats_zero_unknown_and_multiple_reports(value, expected):
    result = normalize(value)
    assert (result["input_tokens"], result["output_tokens"], result["total_tokens"]) == expected
    if isinstance(value, list) and value and value[-1] is None:
        assert "total_tokens" in result["partial_fields"]


def test_cache_and_reasoning_are_subsets_not_extra_tokens():
    result = normalize(
        {
            "prompt_tokens": 100,
            "completion_tokens": 80,
            "prompt_tokens_details": {"cached_tokens": 60},
            "completion_tokens_details": {"reasoning_tokens": 50},
        }
    )
    assert result["total_tokens"] == 180
    assert result["cached_input_tokens"] == 60
    assert result["reasoning_tokens"] == 50
    result = normalize({"inputTokens": 100, "outputTokens": 80, "prompt_cache_hit_tokens": 60})
    assert result["cached_input_tokens"] == 60 and result["reasoning_tokens"] is None


@pytest.mark.parametrize("chunk_size", [1, 7, 65536])
def test_stream_usage_observer_handles_fragmented_cumulative_and_final_frames(chunk_size):
    observer = UsageObserver()
    usage = {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180}
    wire = (
        b': heartbeat\r\n\r\ndata: {"usage":{"total_tokens":101}}\r\n\r\n'
        + b'data: {"usage":\r\ndata: '
        + json.dumps(usage).encode()
        + b"}\r\n\r\n"
        + b'data: {"usage":null}\r\n\r\ndata: [DONE]\r\n\r\n'
    )
    for offset in range(0, len(wire), chunk_size):
        observer.feed(wire[offset : offset + chunk_size])
    assert observer.usage == usage and observer.complete


def test_interrupted_observed_usage_is_marked_as_partial(tmp_path):
    app = Operations(tmp_path / "partial-usage.sqlite3")
    run, _, _ = add_result(
        app, {"usage": [{"prompt_tokens": 100, "completion_tokens": 2}], "usage_incomplete": True}
    )
    summary = app.token_usage(run)
    assert summary["total_tokens"] == 102
    assert "total_tokens" in summary["partial_fields"] and summary["incomplete_chunks"] == 1


def add_result(app, metadata=None, raw=None):
    source = app.import_text(f"章{len(app.runs())}", "甲说门锁着。")
    run = app.create_run(source)
    chunk = app.chunks(run)[0]["id"]
    raw = raw if raw is not None else '{"entities": [], "assertions": []}'
    app.ingest_result(chunk, raw, metadata=metadata)
    return run, chunk, raw


def test_task_project_model_totals_and_unknown_coverage_survive_backup(tmp_path):
    path = tmp_path / "usage.sqlite3"
    app = Operations(path)
    assert app.token_usage()["total_tokens"] == 0
    assert app.token_usage()["counted_chunks"] == 0
    first, _, _ = add_result(
        app,
        {
            "provider": "opencode-go",
            "model": "go-model",
            "usage": [{"inputTokens": 10, "outputTokens": 4}] * 2,
            "model_response": {"metadata": {"usage": {"total_tokens": 99999}}},
        },
    )
    add_result(app, {"provider": "deepseek", "model": "other", "usage": {"total_tokens": 9}})
    add_result(app)  # Manual import without token usage remains unknown.
    source = app.import_text("未启动", "待提取")
    app.create_run(source)
    original_revision = app.project_info().revision
    summary = app.token_usage()
    assert (summary["input_tokens"], summary["output_tokens"], summary["total_tokens"]) == (
        20,
        8,
        37,
    )
    assert summary["counted_chunks"] == 3
    assert summary["reported_chunks"] == 2 and summary["incomplete_chunks"] == 2
    assert "total_tokens" in summary["partial_fields"]
    assert app.token_usage(first)["total_tokens"] == 28
    assert app.token_usage(first)["incomplete_chunks"] == 0
    assert len(summary["by_model"]) == 3
    assert app.project_info().revision == original_revision
    app.save_harness_settings({"provider": "openai", "model": "new-current-model"})
    assert app.token_usage() == summary  # History is not relabelled by the current model.
    app.backup(tmp_path / "backup.sqlite3")
    assert Operations(tmp_path / "backup.sqlite3").token_usage() == summary
    assert Operations(path).token_usage() == summary
    with pytest.raises(OperationError, match="不存在"):
        app.token_usage("missing-run")


def test_failed_result_repair_and_repeat_import_do_not_double_count(tmp_path):
    app = Operations(tmp_path / "repair.sqlite3")
    source = app.import_text("章", "甲说门锁着。")
    run = app.create_run(source)
    chunk = app.chunks(run)[0]["id"]
    with pytest.raises(OperationError):
        app.ingest_result(
            chunk,
            "bad JSON",
            metadata={"usage": [{"inputTokens": 100, "outputTokens": 80}]},
        )
    before = app.token_usage()
    assert before["total_tokens"] == 180
    corrected = '{"entities": [], "assertions": []}'
    app.repair_extraction_result(chunk, corrected, {})
    app.ingest_result(chunk, corrected)
    after = app.token_usage()
    assert after["total_tokens"] == 180 and after["usage_reports"] == 1
    assert after["counted_chunks"] == 1


def test_cancelled_and_interrupted_chunks_do_not_hide_known_usage(tmp_path):
    path = tmp_path / "stopped.sqlite3"
    app = Operations(path)
    _, first, _ = add_result(app, {"usage": {"prompt_tokens": 5, "completion_tokens": 3}})
    _, second, _ = add_result(app)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE chunks SET status='cancelled' WHERE id=?", (first,))
        db.execute("UPDATE chunks SET status='interrupted' WHERE id=?", (second,))
    usage = app.token_usage()
    assert usage["total_tokens"] == 8 and "total_tokens" in usage["partial_fields"]
    assert usage["reported_chunks"] == 1 and usage["incomplete_chunks"] == 1


def test_ui_shows_task_project_and_partial_usage_without_new_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "ui-usage")
    monkeypatch.setattr(
        "narrative.operations.credential_status",
        lambda *args: {"available": False, "source": "none"},
    )
    app = Operations(tmp_path / "ui-usage.sqlite3")
    first, _, _ = add_result(app, {"usage": [{"inputTokens": 100, "outputTokens": 80}]})
    second, _, _ = add_result(app)
    path = Path(__file__).resolve().parents[1] / "src/narrative/ui.py"
    ui = AppTest.from_file(str(path), default_timeout=25).run()
    assert not ui.exception
    metrics = {metric.label: metric.value for metric in ui.metric}
    assert metrics["任务总 Token"] == "未知"
    assert metrics["项目总 Token"] == "≥180"
    next(item for item in ui.selectbox if item.label == "提取任务").set_value(first).run()
    assert not ui.exception
    metrics = {metric.label: metric.value for metric in ui.metric}
    assert metrics["任务输入 Token"] == "100"
    assert metrics["任务输出 Token"] == "80"
    assert metrics["任务总 Token"] == "180"
    before = json.dumps(app.token_usage(), sort_keys=True)
    ui.run()
    assert json.dumps(app.token_usage(), sort_keys=True) == before
    assert app.token_usage(second)["total_tokens"] is None
