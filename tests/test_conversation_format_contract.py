"""Only complete, diagnosed malformed tool JSON can be corrected in-task."""
from __future__ import annotations

from copy import deepcopy

import pytest

from narrative.conversation_session import IncompleteConversation


def metadata():
    return {"finish_reason": "error", "sdk_error_diagnostics": [{
        "code": "MALFORMED_RESPONSE", "message": "DeepSeek Messages stream: tool input is invalid JSON",
    }], "model_requests": [{"purpose": "generation", "complete": True,
        "finish_reason": "tool_calls", "tool_calls": [{"name": "preview_extraction", "id": "bad-call",
            "arguments": '{"quotes": ["原句" as_placeholder]}' }]}]}


def test_complete_malformed_json_feedback_preserves_original_arguments(tmp_path):
    data = metadata()
    original = deepcopy(data)
    error = IncompleteConversation(data, tmp_path / "metadata.json")
    assert error.recoverable and error.code == "tool_input_invalid_json"
    assert error.failed_tool_calls[0]["arguments"] == data["model_requests"][0]["tool_calls"][0]["arguments"]
    assert error.failed_tool_calls[0]["position"] > 0
    assert "as_placeholder" in error.feedback and "不要重放" in error.feedback
    assert data == original


@pytest.mark.parametrize("condition", ["capacity", "network", "compaction", "incomplete_stream", "provider_length",
                                       "other_sdk_error", "valid_arguments", "missing_upstream"])
def test_other_failures_cannot_be_automatically_continued(tmp_path, condition):
    data = metadata()
    if condition == "capacity":
        data["finish_reason"] = "length"
    elif condition == "network":
        data["service_error"] = "connection reset"
    elif condition == "compaction":
        data["compaction_incomplete"] = True
    elif condition == "incomplete_stream":
        data["model_requests"][0]["complete"] = False
    elif condition == "provider_length":
        data["model_requests"][0]["finish_reason"] = "length"
    elif condition == "other_sdk_error":
        data["sdk_error_diagnostics"].append({"code": "NETWORK_ERROR"})
    elif condition == "valid_arguments":
        data["model_requests"][0]["tool_calls"][0]["arguments"] = '{"quotes": ["原句"]}'
    else:
        data["model_requests"] = []
    assert not IncompleteConversation(data, tmp_path / "metadata.json").recoverable


@pytest.mark.parametrize("detail", [
    {"sdk_error_diagnostics": [{"code": "RATE_LIMIT", "message": "provider diagnostic"}]},
    {"service_error": "HTTP 429：Requests are too frequent. private request reference"},
])
def test_provider_rate_limit_is_visible_without_automatic_retry(tmp_path, detail):
    data = {"finish_reason": "error", **detail}
    error = IncompleteConversation(data, tmp_path / "metadata.json")
    assert error.code == "rate_limited" and not error.recoverable
    assert "限流（HTTP 429）" in str(error) and "未自动重试" in str(error)
    assert "private request reference" not in str(error)
    assert error.metadata is data


@pytest.mark.parametrize("detail,expected", [
    ({"service_error": "HTTP 500：InternalServiceError private reference"}, "HTTP 500"),
    ({"service_error": "HTTP 503: unavailable"}, "HTTP 503"),
    ({"sdk_error_diagnostics": [{"code": "SERVER"}]}, "模型服务内部错误"),
])
def test_provider_server_failure_is_visible_and_never_replayed(tmp_path, detail, expected):
    data = {"finish_reason": "error", **detail}
    error = IncompleteConversation(data, tmp_path / "metadata.json")
    assert error.code == "provider_server_error" and not error.recoverable
    assert expected in str(error) and "未自动重试" in str(error)
    assert "private reference" not in str(error) and error.metadata is data
