import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from narrative.operations import OperationError, Operations


class ModelServer(ThreadingHTTPServer):
    daemon_threads = True


@pytest.fixture
def model_server():
    requests = []
    control = {"mode": "success"}
    received = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "body": body})
            received.set()
            if control["mode"] == "slow":
                release.wait(20)
            if control["mode"] == "error":
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                content = (
                    b'{"error":{"message":"invalid test-key-secret","type":"authentication_error"}}'
                )
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            result = {
                "entities": [{"name": "甲", "entity_type": "person"}],
                "assertions": [
                    {
                        "subject_ref": "E0",
                        "predicate": "门状态",
                        "value": {"kind": "scalar", "data": "锁着"},
                        "scope": "claim",
                        "holder_ref": "E0",
                        "evidence_ref": "Q0",
                    }
                ],
            }
            result = control.get("result", result)
            raw = (
                ""
                if control["mode"] == "empty"
                else "not JSON"
                if control["mode"] == "malformed"
                else json.dumps(result, ensure_ascii=False)
            )
            reason = "length" if control["mode"] == "unfinished" else "stop"

            def chunk(delta, finish=None, usage=None):
                return {
                    "id": "chat-test",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    "usage": usage,
                }

            frames = [
                chunk({"role": "assistant", "content": raw}),
                chunk(
                    {}, reason, {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180}
                ),
            ]
            wire = (
                "".join(
                    "data: " + json.dumps(frame, ensure_ascii=False) + "\n\n" for frame in frames
                )
                + "data: [DONE]\n\n"
            )
            content = wire.encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    server = ModelServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/plan/v3", requests, control, received
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(3)


def prepare(tmp_path, base_url, timeout=15):
    app = Operations(tmp_path / "book.sqlite3")
    app.save_harness_settings(
        {"base_url": base_url, "model": "Doubao-Seed-2.1-pro", "timeout_seconds": timeout}
    )
    source_id = app.import_text("章一", "甲说门锁着。")
    return app, app.create_run(source_id)


def wait_finished(app, run_id, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = next(r for r in app.runs() if r["id"] == run_id)
        if row["status"] not in {"queued", "running"}:
            # Wait for resource cleanup too; next extraction must be immediately usable.
            from narrative import jobs

            job = jobs._jobs.get(app.project_info().path + ":" + run_id)
            if job:
                job.thread.join(5)
            return row
        time.sleep(0.05)
    pytest.fail(f"提取未结束：{app.runs()}")


def test_real_sdk_runtime_to_candidate_review(tmp_path, model_server):
    url, requests, _, _ = model_server
    app, run_id = prepare(tmp_path, url)
    revision = app.project_info().revision
    app.start_extraction(run_id, api_key="test-key-secret")
    # Duplicate dispatch in the same active task does not start another model call.
    app.start_extraction(run_id, api_key="test-key-secret")
    run = wait_finished(app, run_id)
    assert run["status"] == "complete", run
    assert len(requests) == 1
    request = requests[0]
    assert request["path"] == "/api/plan/v3/chat/completions"
    assert request["body"]["model"] == "Doubao-Seed-2.1-pro"
    assert request["body"]["reasoning_effort"] == "low"
    assert not request["body"].get("tools")
    assert not request["body"].get("session_log")
    assert "甲说门锁着。" in json.dumps(request["body"], ensure_ascii=False)
    assert app.project_info().revision == revision and not app.assertions()
    candidate = app.candidates(status="pending")[0]
    review = app.review_candidate(candidate["id"])
    assert not [d for d in review.diagnostics if d.severity == "error"]
    app.accept_candidate(candidate["id"], review.version)
    assert app.assertions()[0].scope == "claim"
    assert app.assertions()[0].evidence[0].quote == "甲说门锁着。"
    assert app.chunks(run_id)[0]["metadata"]
    assert "test-key-secret" not in json.dumps(app.runs())
    with pytest.raises(OperationError, match="新建任务"):
        app.start_extraction(run_id, api_key="test-key-secret")
    assert len(requests) == 1


@pytest.mark.parametrize("mode", ["malformed", "unfinished", "error", "empty"])
def test_bad_model_results_do_not_become_candidates(tmp_path, model_server, mode):
    url, requests, control, _ = model_server
    control["mode"] = mode
    app, run_id = prepare(tmp_path, url)
    app.start_extraction(run_id, api_key="test-key-secret")
    run = wait_finished(app, run_id)
    assert run["status"] == "failed", run
    assert len(requests) == 1
    assert not app.candidates() and not app.assertions()
    assert app.chunks(run_id)[0]["error"]
    if mode not in {"error", "empty"}:
        assert app.chunks(run_id)[0]["raw"]
    usage = app.token_usage(run_id)
    if mode != "error":
        assert usage["total_tokens"] == 180 and usage["incomplete_chunks"] == 0
    else:
        assert usage["total_tokens"] is None and usage["incomplete_chunks"] == 1
    assert "test-key-secret" not in json.dumps(app.runs())


def test_cancel_real_sdk_request_and_keep_formal_state(tmp_path, model_server):
    url, _, control, received = model_server
    control["mode"] = "slow"
    app, run_id = prepare(tmp_path, url)
    app.start_extraction(run_id, api_key="test-key-secret")
    assert received.wait(15)
    with pytest.raises(OperationError, match="执行中"):
        app.ingest_result(app.chunks(run_id)[0]["id"], "{}")
    app.cancel_extraction(run_id)
    assert wait_finished(app, run_id)["status"] == "cancelled"
    assert not app.candidates() and not app.assertions()


def test_failed_model_result_can_be_repaired_without_new_request(tmp_path, model_server):
    url, requests, control, _ = model_server
    control["mode"] = "malformed"
    app, run_id = prepare(tmp_path, url)
    app.start_extraction(run_id, api_key="test-key-secret")
    assert wait_finished(app, run_id)["status"] == "failed"
    chunk = app.chunks(run_id)[0]
    corrected = {
        "entities": [{"ref": "a", "name": "甲"}],
        "assertions": [
            {
                "subject_ref": "a",
                "predicate": "门状态",
                "value": "锁着",
                "scope": "claim",
                "holder_ref": "a",
                "quote": "甲说门锁着。",
                "quote_start": 0,
            }
        ],
    }
    change_id = app.ingest_result(chunk["id"], json.dumps(corrected, ensure_ascii=False))
    repaired = next(r for r in app.runs() if r["id"] == run_id)
    assert repaired["status"] == "complete"
    assert not repaired["data"].get("error")
    assert repaired["data"]["resolved_errors"][0]["resolved_by_chunk"] == chunk["id"]
    assert len(requests) == 1 and not app.assertions()
    review = app.review_candidate(change_id)
    assert not [d for d in review.diagnostics if d.severity == "error"]


def test_runtime_timeout_does_not_retry(tmp_path, model_server):
    url, requests, control, _ = model_server
    control["mode"] = "slow"
    app, run_id = prepare(tmp_path, url, timeout=5)
    app.start_extraction(run_id, api_key="test-key-secret")
    row = wait_finished(app, run_id)
    assert row["status"] == "failed" and "超时" in row["data"]["error"]
    assert len(requests) == 1 and not app.candidates()


def test_missing_key_and_secret_settings_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("narrative.harness._windows_key", lambda: ("", "none"))
    app, run_id = prepare(tmp_path, "https://example.com/v1")
    with pytest.raises(OperationError, match="密钥"):
        app.start_extraction(run_id)
    assert app.runs()[0]["status"] == "awaiting_results"
    with pytest.raises(OperationError):
        app.save_harness_settings({"api_key": "must-not-save"})


def test_persistent_windows_key_without_process_restart(monkeypatch):
    from narrative.harness import HarnessSettings, credential_status, resolve_key

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    values = {"key": "user-key"}
    monkeypatch.setattr(
        "narrative.harness._windows_key", lambda: (values["key"], "user_environment")
    )
    settings = HarnessSettings()
    assert resolve_key(settings, None) == "user-key"
    assert credential_status() == {"available": True, "source": "user_environment"}
    values["key"] = "updated-user-key"
    assert resolve_key(settings, None) == "updated-user-key"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-key")
    assert resolve_key(settings, None) == "process-key"
    assert resolve_key(settings, "session-key") == "session-key"


def test_empty_manual_import_does_not_fail_model_task(tmp_path):
    app, run_id = prepare(tmp_path, "http://127.0.0.1:1/v1")
    with pytest.raises(OperationError, match="启动模型提取"):
        app.ingest_result(app.chunks(run_id)[0]["id"], " ")
    assert app.runs()[0]["status"] == "awaiting_results"


def test_interrupted_run_recovery_does_not_dispatch(tmp_path):
    app, run_id = prepare(tmp_path, "http://127.0.0.1:1/v1")
    # Simulate durable state left by a terminated process; no live OS lease.
    import sqlite3

    with sqlite3.connect(app.project_info().path) as db:
        db.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
        db.execute("UPDATE chunks SET status='running' WHERE run_id=?", (run_id,))
    reopened = Operations(app.project_info().path)
    assert reopened.runs()[0]["status"] == "interrupted"
    assert reopened.chunks(run_id)[0]["status"] == "interrupted"
    assert not reopened.candidates()


def test_active_lease_allows_project_reopen_and_read(tmp_path, monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    from narrative.jobs import ProjectLease

    app, run_id = prepare(tmp_path, "http://127.0.0.1:1/v1")
    lease = ProjectLease(Path(app.project_info().path + ".dsh.lock"))
    try:
        reopened = Operations(app.project_info().path)
        assert reopened.sources()
        assert reopened.runs()[0]["status"] == "awaiting_results"
        monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "book")
        ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
        page = AppTest.from_file(str(ui_path), default_timeout=20).run()
        assert not page.exception and not page.main.error
        assert page.tabs[0].label == "人物与事件地图"
        assert any(tab.label == "提取交接" for tab in page.tabs)
        with pytest.raises(ValueError, match="已有模型提取"):
            ProjectLease(Path(app.project_info().path + ".dsh.lock"))
    finally:
        lease.close()


def test_ui_real_sdk_result_can_be_reviewed(tmp_path, monkeypatch, model_server):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    url, requests, _, _ = model_server
    app = Operations(tmp_path / "data" / "demo.sqlite3")
    app.save_harness_settings(
        {"base_url": url, "model": "Doubao-Seed-2.1-pro", "timeout_seconds": 15}
    )
    app.import_text("章一", "甲说门锁着。")
    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    monkeypatch.chdir(tmp_path)
    page = AppTest.from_file(str(ui_path), default_timeout=20).run()
    assert not page.exception
    next(b for b in page.button if b.label == "创建分块任务").click().run()
    assert not page.exception
    page.text_input(key="harness-api-key").set_value("test-key-secret")
    next(b for b in page.button if b.label == "启动模型提取").click().run()
    run_id = app.runs()[0]["id"]
    assert wait_finished(app, run_id)["status"] == "complete"
    page.run()
    assert not page.exception
    next(b for b in page.button if b.label == "接受并保存正式资料").click().run()
    assert not page.exception
    assert app.assertions()[0].scope == "claim" and len(requests) == 1
    assert not app.candidates("pending")
    assert app.runs()[0]["status"] == "complete"
    status = [item.value for item in page.markdown if "任务状态：" in item.value]
    assert status == ["**任务状态：提取结果已解析**"]


def test_real_sdk_event_result_uses_existing_events_and_ui_review(
    tmp_path, monkeypatch, model_server
):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    url, requests, control, _ = model_server
    directory = tmp_path / "projects"
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(directory))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "sdk-events")
    app = Operations.open_project(directory, "sdk-events")
    app.save_harness_settings(
        {"base_url": url, "model": "Doubao-Seed-2.1-pro", "timeout_seconds": 15}
    )
    quote = "十分钟后，甲把铜钥匙交给乙。"
    source = app.import_text("模型事件测试", "甲看见铜钥匙。" + quote)
    seed = app.propose_event(
        title="此前观察", action="observe", evidence=app.locate(source, "甲看见铜钥匙。")[0]
    )
    seed_event_id = app.review_candidate(seed).change.operations[0].id
    app.accept_candidate(seed, 1)
    control["result"] = {
        "entities": [
            {"name": "甲", "entity_type": "person"},
            {"name": "乙", "entity_type": "person"},
            {"name": "铜钥匙", "entity_type": "item"},
        ],
        "events": [
            {
                "title": "铜钥匙交接",
                "action": "transfer",
                "actor_ref": "E0",
                "target_ref": "E1",
                "item_ref": "E2",
                "time_kind": "relative",
                "after_event_ref": seed_event_id,
                "evidence_ref": "Q1",
            }
        ],
    }
    revision = app.project_info().revision
    run_id = app.create_run(source)
    app.start_extraction(run_id, api_key="test-key-secret")
    run = wait_finished(app, run_id)
    assert run["status"] == "complete", run
    assert len(requests) == 1
    body = json.dumps(requests[0]["body"], ensure_ascii=False)
    assert "existing_events" in body and seed_event_id in body and "此前观察" in body
    assert "after_event_ref" in body and "时间" in body
    metadata = json.loads(app.chunks(run_id)[0]["metadata"])
    assert metadata["prompt_version"] == "evidence-extraction/11"
    assert app.project_info().revision == revision
    assert len(app.events()) == 1 and not app.entities()
    assert len(app.candidates(status="pending")) == 1

    ui_path = Path(__file__).resolve().parents[1] / "src" / "narrative" / "ui.py"
    page = AppTest.from_file(str(ui_path), default_timeout=20).run()
    assert not page.exception
    next(b for b in page.button if b.label == "接受并保存正式资料").click().run()
    assert not page.exception
    assert len(app.events()) == 2 and len(requests) == 1
    assert app.project_info().revision == revision + 1
    event = next(row for row in app.events() if row["title"] == "铜钥匙交接")
    ids = {entity["name"]: entity["id"] for entity in app.entities()}
    state = app.event_state(event["id"])
    assert state["before"]["holders"][ids["铜钥匙"]] is None
    assert state["after"]["holders"][ids["铜钥匙"]] == ids["乙"]
    assert event["evidence"][0]["quote"] == quote
    assert page.get("graphviz_chart")
    assert any("铜钥匙交接" in frame.value.to_string() for frame in page.dataframe)
