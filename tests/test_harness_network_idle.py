"""SDK task waits indefinitely; only network inactivity retains a timeout."""
import json

import pytest

from narrative.harness import HarnessRunner, HarnessSettings, runtime_patch


def test_sdk_rpc_has_no_total_timeout_but_retains_stream_idle_limit(tmp_path, monkeypatch):
    import deepseek_harness

    captured = []

    class LocalHarness:
        def __init__(self, **config):
            captured.append(config)

        def close(self, **_kwargs):
            pass

    monkeypatch.setattr(deepseek_harness, "DeepSeekHarness", LocalHarness)
    settings = HarnessSettings(timeout_seconds=5)
    runner = HarnessRunner(settings, "offline-placeholder", tmp_path / "sdk")
    try:
        assert captured[0]["request_timeout_seconds"] is None
        assert settings.timeout_seconds == 5
        assert '"streamIdleTimeoutMs": 5000' in json.dumps(runtime_patch(settings))
        assert not hasattr(runner, "_timed_out")
    finally:
        runner.close()


def test_resume_after_manual_close_cannot_restart_the_runtime():
    runner = HarnessRunner.__new__(HarnessRunner)
    runner._closed = True

    class ForbiddenHarness:
        def start(self):
            raise AssertionError("A cancelled runtime must never restart")

    runner._harness = ForbiddenHarness()
    with pytest.raises(ValueError, match="任务已经停止"):
        runner.resume("chat-isolated")
