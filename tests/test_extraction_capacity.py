"""Capacity failures remain auditable and recovery always creates a new run."""

import json
import time
from pathlib import Path

import pytest

from narrative import jobs
from narrative.harness import HarnessReply
from narrative.operations import OperationError, Operations

USAGE = {
    "prompt_tokens": 5957,
    "completion_tokens": 10684,
    "total_tokens": 16641,
    "completion_tokens_details": {"reasoning_tokens": 2492},
}
COMPLETE_JSON = '{"entities": [], "assertions": []}'


def failed_model_run(tmp_path, monkeypatch, raw: str, finish_reason: str):
    class Runner:
        calls = 0

        def __init__(self, settings, key, directory):
            pass

        def extract(self, instruction, payload, session_id):
            type(self).calls += 1
            return HarnessReply(
                raw,
                {
                    "finish_reason": finish_reason,
                    "usage": [USAGE],
                    "usage_source": "provider-stream",
                    "provider": "ark",
                    "model": "test-model",
                },
            )

        def usage_metadata(self):
            return {"usage": [USAGE], "usage_source": "provider-stream"}

        def redact(self, message):
            return message

        def close(self):
            pass

    monkeypatch.setattr(jobs, "HarnessRunner", Runner)
    app = Operations(tmp_path / "capacity.sqlite3")
    source_id = app.import_text("项脊轩志容量测试", "甲说门锁着。" * 131 + "甲说。")
    run_id = app.create_run(source_id)
    original_revision = app.project_info().revision
    app.start_extraction(run_id, api_key="local-test-key")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = jobs._jobs.get(app.project_info().path + ":" + run_id)
        if job is not None:
            job.thread.join(0.1)
            if not job.thread.is_alive():
                break
    else:
        pytest.fail("mock model run did not finish")
    return app, source_id, run_id, original_revision, Runner


@pytest.mark.parametrize(
    "reason,raw",
    [("max-tokens", '{"entities": [',), ("length", COMPLETE_JSON)],
)
def test_incomplete_finish_never_auto_imports_even_valid_json(
    tmp_path, monkeypatch, reason, raw
):
    app, _, run_id, revision, runner = failed_model_run(tmp_path, monkeypatch, raw, reason)
    chunk = app.chunks(run_id)[0]
    metadata = json.loads(chunk["metadata"])
    assert runner.calls == 1
    assert next(r for r in app.runs() if r["id"] == run_id)["status"] == "failed"
    assert chunk["status"] == "failed" and chunk["raw"] == raw
    assert metadata["finish_reason"] == reason and metadata["usage"] == [USAGE]
    assert reason in chunk["error"] and "未导入" in chunk["error"]
    assert app.project_info().revision == revision
    assert app.candidates() == [] and app.entities() == []
    assert app.token_usage(run_id)["output_tokens"] == 10684
    assert app.token_usage(run_id)["reasoning_tokens"] == 2492
    assert app.extraction_review(chunk["id"])["importable"] is False
    assert app.extraction_review(chunk["id"])["capacity_truncated"] is True
    assert app.extraction_run_diagnostics(run_id) == {
        "finish_reasons": [reason],
        "capacity_truncated": True,
        "manually_repaired": False,
    }
    with pytest.raises(OperationError, match="未完整结束"):
        app.ingest_result(chunk["id"], raw)
    with pytest.raises(OperationError, match="原响应不能直接导入"):
        app.repair_extraction_result(chunk["id"], raw, {}, expected_raw=raw)
    assert app.chunks(run_id)[0]["raw"] == raw


def test_recovery_creates_smaller_new_run_without_relaunching_or_mutating_old(
    tmp_path, monkeypatch
):
    app, source_id, run_id, _, runner = failed_model_run(
        tmp_path, monkeypatch, '{"entities": [', "max-tokens"
    )
    before = app.chunks(run_id)[0].copy()
    old_run = next(r for r in app.runs() if r["id"] == run_id).copy()
    with pytest.raises(OperationError, match="小于原任务"):
        app.create_recovery_run(run_id, before["end"] - before["start"])
    new_id = app.create_recovery_run(run_id, size=400, overlap=20)
    new_run = next(r for r in app.runs() if r["id"] == new_id)
    assert new_id != run_id and new_run["source_id"] == source_id
    assert new_run["status"] == "awaiting_results"
    assert all(c["end"] - c["start"] <= 400 for c in app.chunks(new_id))
    assert len(app.chunks(new_id)) > 1 and runner.calls == 1
    assert app.chunks(run_id)[0] == before
    assert next(r for r in app.runs() if r["id"] == run_id) == old_run
    assert app.token_usage(run_id)["output_tokens"] == 10684


def test_explicit_manual_repair_keeps_truncated_model_response_and_audit(
    tmp_path, monkeypatch
):
    old_raw = '{"entities": ['
    app, _, run_id, _, _ = failed_model_run(tmp_path, monkeypatch, old_raw, "max-tokens")
    chunk_id = app.chunks(run_id)[0]["id"]
    corrected = '{"entities": [{"ref": "a", "name": "甲"}], "assertions": []}'
    change_id = app.repair_extraction_result(
        chunk_id, corrected, {}, expected_raw=old_raw
    )
    assert change_id is not None
    chunk = app.chunks(run_id)[0]
    metadata = json.loads(chunk["metadata"])
    assert chunk["status"] == "processed"
    assert metadata["finish_reason"] == "max-tokens"
    assert metadata["model_response"]["raw"] == old_raw
    assert metadata["manual_corrections"][0]["edited_raw"] == corrected
    assert app.extraction_run_diagnostics(run_id)["manually_repaired"] is True
    assert app.token_usage(run_id)["output_tokens"] == 10684
    assert len(app.candidates("pending")) == 1 and app.entities() == []


def test_ui_recovery_is_explicit_and_goes_through_operations(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    app, _, run_id, _, runner = failed_model_run(
        tmp_path, monkeypatch, '{"entities": [', "max-tokens"
    )
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "capacity")
    ui = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    page = AppTest.from_file(str(ui), default_timeout=20).run()
    assert not page.exception
    assert any("输出容量截断" in item.value for item in page.warning)
    assert runner.calls == 1
    next(b for b in page.button if b.label == "创建新的较小分块任务").click().run()
    assert not page.exception
    assert len(app.runs()) == 2 and runner.calls == 1
    assert app.runs()[-1]["status"] == "awaiting_results"
    assert app.chunks(run_id)[0]["status"] == "failed"
