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
