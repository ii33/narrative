import json
import sqlite3
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest
from streamlit.testing.v1 import AppTest
from test_harness import wait_finished

from narrative.harness import HarnessRunner, HarnessSettings, configured_key, resolve_key
from narrative.model_gateway import ModelGateway
from narrative.operations import OperationError, Operations
from narrative.providers import PRESETS


@pytest.fixture
def provider_server():
    received = []
    policy = {"token_parameter": "max_tokens", "reasoning_effort": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(
                {
                    "path": self.path,
                    "body": body,
                    "authorization": self.headers.get("Authorization"),
                    "session": self.headers.get("x-opencode-session"),
                    "user_agent": self.headers.get("User-Agent"),
                }
            )
            if policy.get("redirect"):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/must-not-follow")
                self.end_headers()
                return
            if (
                policy["token_parameter"] not in body
                or ({"max_tokens", "max_completion_tokens"} - {policy["token_parameter"]})
                & body.keys()
                or body.get("reasoning_effort") != policy["reasoning_effort"]
            ):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"unsupported provider parameters"}}')
                return
            raw = json.dumps(
                {
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
                },
                ensure_ascii=False,
            )

            def frame(delta, reason=None, usage=None):
                return {
                    "id": "provider-test",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
                    "usage": usage,
                }

            frames = [
                frame({"role": "assistant", "content": raw}, usage=policy.get("early_usage")),
                frame(
                    {},
                    "stop",
                    policy.get(
                        "usage", {"prompt_tokens": 11, "completion_tokens": 12, "total_tokens": 23}
                    ),
                ),
            ]
            wire = (
                "".join("data: " + json.dumps(f, ensure_ascii=False) + "\n\n" for f in frames)
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/compatible/v1", received, policy
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("provider", list(PRESETS))
def test_each_provider_uses_real_sdk_adapted_wire_and_review(tmp_path, provider_server, provider):
    address, received, policy = provider_server
    expected = PRESETS[provider]
    policy.update(
        token_parameter=expected["token_parameter"], reasoning_effort=expected["reasoning_effort"]
    )
    app = Operations(tmp_path / (provider + ".sqlite3"))
    app.save_harness_settings(
        {"provider": provider, "base_url": address, "model": "vendor-model", "timeout_seconds": 15}
    )
    source = app.import_text("章一", "甲说门锁着。")
    run = app.create_run(source)
    app.start_extraction(run, api_key="provider-only-test-secret")
    finished = wait_finished(app, run)
    assert finished["status"] == "complete", finished
    assert len(received) == 1
    request = received[0]
    assert request["path"] == "/compatible/v1/chat/completions"
    assert request["authorization"] == "Bearer provider-only-test-secret"
    if provider == "opencode-go":
        assert request["session"] == f"extract-{run}-0"
        assert request["user_agent"] == "narrative-core/0.1.0"
    else:
        assert request["session"] is None
    assert request["body"]["model"] == "vendor-model"
    assert request["body"]["stream_options"]["include_usage"] is True
    if policy["reasoning_effort"] is None:
        assert "reasoning_effort" not in request["body"]
    candidate = app.candidates("pending")[0]
    review = app.review_candidate(candidate["id"])
    app.accept_candidate(candidate["id"], review.version)
    assert app.assertions()[0].scope == "claim"
    assert "provider-only-test-secret" not in json.dumps(app.runs()) + json.dumps(app.chunks(run))
    assert finished["data"]["settings"]["provider"] == provider
    assert json.loads(app.chunks(run)[0]["metadata"])["provider"] == provider
    usage = app.token_usage(run)
    assert (usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]) == (11, 12, 23)
    assert usage["reported_chunks"] == 1 and usage["incomplete_chunks"] == 0


def test_provider_credentials_do_not_fall_back_to_another_provider(monkeypatch):
    monkeypatch.setattr("narrative.harness._windows_key", lambda *a: ("", "none"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = HarnessSettings(provider="openai", model="test-model")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        resolve_key(settings, None)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    assert resolve_key(settings, None) == "openai-secret"
    assert resolve_key(settings, "supplied-secret") == "supplied-secret"
    assert configured_key()[0] == "legacy-secret"


@pytest.mark.parametrize("reported", [True, False])
def test_provider_stream_token_totals_use_final_report_and_preserve_unknown(
    tmp_path, provider_server, reported
):
    address, _, policy = provider_server
    final_usage = {
        "prompt_tokens": 100,
        "completion_tokens": 80,
        "total_tokens": 180,
        "prompt_tokens_details": {"cached_tokens": 60},
        "completion_tokens_details": {"reasoning_tokens": 50},
    }
    policy["early_usage"] = {"prompt_tokens": 100, "completion_tokens": 1} if reported else None
    policy["usage"] = final_usage if reported else None
    app = Operations(tmp_path / "stream-usage.sqlite3")
    app.save_harness_settings({"provider": "opencode-go", "base_url": address, "model": "m"})
    source = app.import_text("章", "甲说门锁着。")
    run = app.create_run(source)
    app.start_extraction(run, api_key="test-only-key")
    assert wait_finished(app, run)["status"] == "complete"
    summary = app.token_usage(run)
    if reported:
        assert summary["input_tokens"] == 100 and summary["output_tokens"] == 80
        assert summary["total_tokens"] == 180 and summary["usage_reports"] == 1
        assert summary["cached_input_tokens"] == 60 and summary["reasoning_tokens"] == 50
        assert summary["incomplete_chunks"] == 0
    else:
        assert summary["total_tokens"] is None and summary["reported_chunks"] == 0
        assert summary["incomplete_chunks"] == 1


def test_redirect_is_not_followed_or_retried(tmp_path, provider_server):
    address, received, policy = provider_server
    policy["redirect"] = True
    app = Operations(tmp_path / "redirect.sqlite3")
    app.save_harness_settings(
        {"provider": "openai", "base_url": address, "model": "m", "timeout_seconds": 15}
    )
    source = app.import_text("章", "甲说门锁着。")
    run = app.create_run(source)
    app.start_extraction(run, api_key="test-secret")
    result = wait_finished(app, run)
    assert result["status"] == "failed"
    assert len(received) == 1 and not app.candidates()
    assert "重定向" in result["data"]["error"]


def test_local_ollama_never_inherits_deepseek_key(monkeypatch):
    monkeypatch.setattr("narrative.harness._windows_key", lambda *a: ("", "none"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-send")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert (
        resolve_key(HarnessSettings(provider="ollama", model="local-model"), None)
        == "local-no-auth"
    )


def test_windows_lookup_uses_selected_environment_name(monkeypatch):
    seen = []
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    def registry(name):
        seen.append(name)
        return "registry-key", "user_environment"

    monkeypatch.setattr("narrative.harness._windows_key", registry)
    assert configured_key("DASHSCOPE_API_KEY") == ("registry-key", "user_environment")
    assert seen == ["DASHSCOPE_API_KEY"]


def test_legacy_settings_and_provider_profiles_survive_reopen_and_backup(tmp_path):
    path = tmp_path / "book.sqlite3"
    app = Operations(path)
    old = {
        "base_url": "https://old.example/v1",
        "model": "old-exact-model",
        "reasoning_effort": "low",
    }
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO meta(key,value) VALUES ('harness_settings',?)", (json.dumps(old),))
    assert app.harness_settings()["key_env"] == "DEEPSEEK_API_KEY"
    assert app.harness_settings()["provider"] == "custom"
    app.save_harness_settings({"provider": "openai", "model": "my-model"})
    assert app.harness_settings("custom")["base_url"] == old["base_url"]
    assert app.harness_settings("custom")["model"] == old["model"]
    app.save_harness_settings({"provider": "qwen", "model": "my-qwen"})
    assert app.harness_settings("openai")["model"] == "my-model"
    assert app.harness_settings("openai")["token_parameter"] == "max_completion_tokens"
    app.backup(tmp_path / "backup.sqlite3")
    reopened = Operations(tmp_path / "backup.sqlite3")
    assert reopened.harness_settings("openai") == app.harness_settings("openai")
    assert reopened.harness_settings() == app.harness_settings()
    with pytest.raises(OperationError):
        app.save_harness_settings(
            {"provider": "openai", "model": "my-model", "api_key": "never-save"}
        )


def test_unsaved_default_profile_is_preserved_before_switching(tmp_path):
    app = Operations(tmp_path / "defaults.sqlite3")
    before = app.harness_settings()
    app.save_harness_settings({"provider": "openai", "model": "m"})
    assert app.harness_settings("ark") == before


def test_gateway_rejects_unauthenticated_local_requests(tmp_path):
    import urllib.error
    import urllib.request

    gateway = ModelGateway(HarnessSettings(provider="openai", model="m"), "secret")
    try:
        for _ in range(12):
            request = urllib.request.Request(
                gateway.base_url + "/chat/completions", data=b"{}", method="POST"
            )
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(request, timeout=3)
            assert exc.value.code == 401
    finally:
        gateway.close()


@pytest.mark.parametrize(
    "declared,pieces",
    [(2, [b"{", b"}"]), (8, [b"{}"]), (65537, [])],
)
def test_gateway_unauthenticated_body_handling_is_bounded(declared, pieces):
    gateway = ModelGateway(HarnessSettings(provider="openai", model="m"), "secret")
    try:
        url = urlsplit(gateway.base_url)
        connection = HTTPConnection(url.hostname, url.port, timeout=3)
        try:
            connection.putrequest("POST", "/v1/chat/completions")
            connection.putheader("Content-Length", str(declared))
            connection.endheaders()
            started = time.monotonic()
            for index, piece in enumerate(pieces):
                if index:
                    time.sleep(0.02)
                connection.send(piece)
            response = connection.getresponse()
            assert response.status == 401
            assert time.monotonic() - started < 1.5
        finally:
            connection.close()
    finally:
        gateway.close()


def test_provider_ui_saves_separate_settings_and_changes_key_scope(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "providers")
    monkeypatch.setattr(
        "narrative.operations.credential_status", lambda *a: {"available": False, "source": "none"}
    )
    path = Path(__file__).resolve().parents[1] / "src/narrative/ui.py"
    ui = AppTest.from_file(str(path), default_timeout=25).run()
    assert not ui.exception
    next(x for x in ui.selectbox if x.label == "模型供应商").set_value("openai").run()
    next(x for x in ui.text_input if x.label == "精确模型 ID").set_value("account-model")
    next(x for x in ui.button if x.label == "保存服务设置").click().run()
    assert not ui.exception
    app = Operations(tmp_path / "providers.sqlite3")
    assert app.harness_settings()["provider"] == "openai"
    assert any("OPENAI_API_KEY" in x.label for x in ui.text_input)
    key = ui.text_input(key="harness-api-key")
    key.set_value("ui-key-not-saved").run()
    next(x for x in ui.selectbox if x.label == "模型供应商").set_value("ollama").run()
    assert ui.text_input(key="harness-api-key").value == ""
    assert ui.text_input(key="harness-api-key").disabled
    next(x for x in ui.text_input if x.label == "精确模型 ID").set_value("local-model")
    next(x for x in ui.button if x.label == "保存服务设置").click().run()
    assert not ui.exception
    assert ui.text_input(key="harness-api-key").value == ""
    assert app.harness_settings("openai")["model"] == "account-model"
    assert "ui-key-not-saved" not in json.dumps(app.harness_settings())
    assert not app.runs()


def test_opencode_go_real_sdk_keeps_conversation_header_stable(tmp_path, provider_server):
    address, received, _ = provider_server
    settings = HarnessSettings(provider="opencode-go", base_url=address, model="account-model")
    runner = HarnessRunner(settings, "go-test-secret", tmp_path / "first-runtime")
    try:
        for session_id in ("extract-test-0", "extract-test-0", "extract-test-1"):
            reply = runner.extract("返回 JSON 对象", {"text": "甲说门锁着。"}, session_id)
            assert reply.metadata["finish_reason"] == "completed"
            assert reply.metadata["session_id"] == session_id
    finally:
        runner.close()
    # Recreating a runtime does not change an explicitly resumed conversation's ID.
    runner = HarnessRunner(settings, "go-test-secret", tmp_path / "second-runtime")
    try:
        runner.extract("返回 JSON 对象", {"text": "甲说门锁着。"}, "extract-test-1")
    finally:
        runner.close()
    assert [request["session"] for request in received] == [
        "extract-test-0",
        "extract-test-0",
        "extract-test-1",
        "extract-test-1",
    ]
    assert all(request["user_agent"] == "narrative-core/0.1.0" for request in received)


def test_opencode_go_gateway_requires_bound_session_and_does_not_trust_incoming_header(
    provider_server,
):
    address, received, _ = provider_server
    gateway = ModelGateway(
        HarnessSettings(provider="opencode-go", base_url=address, model="m"), "go-test-secret"
    )

    def request():
        return Request(
            gateway.base_url + "/chat/completions",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {gateway.token}",
                "x-opencode-session": "stale-incoming-session",
                "User-Agent": "some-other-client",
            },
            method="POST",
        )

    try:
        with pytest.raises(HTTPError) as exc:
            urlopen(request(), timeout=3)
        assert exc.value.code == 502
        assert "缺少提取会话标识" in exc.value.read().decode()
        assert not received
        with gateway.session("actual-conversation"):
            with urlopen(request(), timeout=3) as response:
                assert response.status == 200
                response.read()
            with pytest.raises(ValueError, match="同时执行"):
                with gateway.session("overlapping-conversation"):
                    pytest.fail("must not overwrite the active session")
        with pytest.raises(HTTPError):
            urlopen(request(), timeout=3)
        assert len(received) == 1
        assert received[0]["session"] == "actual-conversation"
        assert received[0]["user_agent"] == "narrative-core/0.1.0"
    finally:
        gateway.close()


@pytest.mark.parametrize("session_id", ["", "bad\r\nx-injected: value", "有中文", "x" * 257])
def test_opencode_go_rejects_invalid_header_before_network(session_id, provider_server):
    address, received, _ = provider_server
    gateway = ModelGateway(
        HarnessSettings(provider="opencode-go", base_url=address, model="m"), "go-test-secret"
    )
    try:
        with pytest.raises(ValueError, match="会话标识"):
            with gateway.session(session_id):
                pytest.fail("invalid headers must not start a session")
        assert not received
    finally:
        gateway.close()


def test_opencode_go_profile_ui_and_key_are_separate(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "go-profile")
    monkeypatch.setattr("narrative.harness._windows_key", lambda *a: ("", "none"))
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-borrow")
    path = Path(__file__).resolve().parents[1] / "src/narrative/ui.py"
    ui = AppTest.from_file(str(path), default_timeout=25).run()
    next(x for x in ui.selectbox if x.label == "模型供应商").set_value("opencode-go").run()
    app = Operations(tmp_path / "go-profile.sqlite3")
    draft = app.harness_settings("opencode-go")
    assert draft["base_url"] == "https://opencode.ai/inference/go/openai/v1"
    assert draft["model"] == ""  # The account's exact model ID is required.
    next(x for x in ui.text_input if x.label == "精确模型 ID").set_value("account-go-model")
    next(x for x in ui.button if x.label == "保存服务设置").click().run()
    assert not ui.exception
    assert any("OPENCODE_GO_API_KEY" in x.label for x in ui.text_input)
    settings = app.harness_settings()
    assert settings["provider"] == "opencode-go"
    with pytest.raises(ValueError, match="OPENCODE_GO_API_KEY"):
        resolve_key(HarnessSettings.model_validate(settings), None)
    app.save_harness_settings({"provider": "openai", "model": "other-model"})
    assert app.harness_settings("opencode-go") == settings
    assert not app.runs()
