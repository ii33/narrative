"""Only complete, malformed tool arguments can be repaired inside one task."""
import threading
from pathlib import Path

import pytest

from narrative import assistant
from narrative.conversation_session import IncompleteConversation


@pytest.fixture(autouse=True)
def prompt_project(tmp_path, monkeypatch):
    from narrative.operations import Operations
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    Operations.create_graph_project(tmp_path / "test.nproj")


def malformed(**overrides):
    data = {"finish_reason": "error", "sdk_error_diagnostics": [{"code": "MALFORMED_RESPONSE",
            "message": "DeepSeek Messages stream: tool input is invalid JSON"}],
            "model_requests": [{"purpose": "generation", "complete": True, "finish_reason": "tool_calls",
                                "tool_calls": [{"name": "read_source", "id": "bad", "arguments": '{"value": invalid}'}]}]}
    data.update(overrides)
    return IncompleteConversation(data, Path("isolated-turn/metadata.json"))


def test_multiple_format_repairs_keep_one_task_and_send_new_diagnostic_inputs(tmp_path):
    calls, feedback = [], []

    def ask(instruction):
        calls.append(instruction)
        if len(calls) <= 5:
            raise malformed()
        return "完成"

    result = assistant.run_turn(ask, "读取原文", project=tmp_path / "test.nproj", extraction_granularity="standard",
                                on_format_error=feedback.append)
    assert len(calls) == 6 and len(feedback) == 5
    assert "用户说：读取原文" in calls[0]
    assert all("invalid" in instruction and "自行修正" in instruction for instruction in calls[1:])
    assert all(instruction != calls[0] for instruction in calls[1:])
    assert result["answer"] == "完成" and len(result["format_repairs"]) == 5


@pytest.mark.parametrize("changes", [
    {"model_requests": [{"purpose": "generation", "complete": True, "finish_reason": "length"}]},
    {"model_requests": [{"purpose": "generation", "complete": False, "finish_reason": "tool_calls"}]},
    {"service_error": "provider unavailable"}, {"compaction_incomplete": True},
    {"finish_reason": "timeout"}, {"sdk_error_diagnostics": [{"code": "NETWORK_ERROR", "message": "socket closed"}]},
])
def test_capacity_network_timeout_and_unknown_failures_are_not_retried(tmp_path, changes):
    calls = []

    def ask(instruction):
        calls.append(instruction)
        raise malformed(**changes)

    with pytest.raises(IncompleteConversation):
        assistant.run_turn(ask, "读取", project=tmp_path / "test.nproj", extraction_granularity="standard")
    assert len(calls) == 1


def test_task_has_no_timer_or_elapsed_time_cutoff(tmp_path, monkeypatch):
    def forbidden_timer(*_args, **_kwargs):
        raise AssertionError("A conversation task must not install a total-time timer")

    monkeypatch.setattr(threading, "Timer", forbidden_timer)
    result = assistant.run_turn(lambda _: "完成", "读取", project=tmp_path / "test.nproj", extraction_granularity="standard")
    assert result["answer"] == "完成"


def test_manual_transport_closure_keeps_original_exception(tmp_path):
    error = RuntimeError("DeepSeek Harness runtime stdout closed")
    calls = []

    def ask(instruction):
        calls.append(instruction)
        raise error

    with pytest.raises(RuntimeError) as failure:
        assistant.run_turn(ask, "提取", project=tmp_path / "test.nproj", extraction_granularity="standard")
    assert failure.value is error
    assert len(calls) == 1
