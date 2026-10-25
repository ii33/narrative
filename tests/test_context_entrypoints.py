"""Context policy entry points preserve settings and fail before network access."""
from __future__ import annotations

import json
import time

import pytest

from narrative import cli, graph_agent
from narrative.agent_runtime import _compact, _json
from narrative.graph_operations import GraphOperations
from narrative.harness import HarnessReply, HarnessSettings

POLICY = {
    "context_window": 2_000_000,
    "output_reserve": 140_000,
    "compaction_reserve": 70_000,
    "compaction_max_tokens": 40_000,
}


@pytest.mark.parametrize("graph", [True, False])
def test_profiles_roundtrip_context_fields_and_preserve_explicit_old_caps(tmp_path, graph):
    path = tmp_path / "profiles.sqlite3"
    ops = GraphOperations(path, create=True)
    saved = ops.save_harness_settings({"provider": "ark", "max_tokens": 16384, **POLICY})
    ops.save_harness_settings({"provider": "openai", "model": "account-model"})
    assert ops.harness_settings("ark") == saved
    fresh = ops.harness_settings("qwen")
    assert fresh == HarnessSettings(provider="qwen", model="__unconfigured__").model_dump() | {"model": ""}
    assert all(field in fresh for field in POLICY)
    reopened = GraphOperations(path)
    assert reopened.harness_settings("ark") == saved
    assert saved["max_tokens"] == 16384
    draft = reopened.harness_settings("opencode-go")
    assert draft["model"] == ""
    assert all(field in draft for field in POLICY)
    with pytest.raises(ValueError, match="精确模型"):
        reopened.save_harness_settings(draft)


def test_graph_schema_lists_only_node_object_kinds(tmp_path):
    ops = GraphOperations(tmp_path / "schema.sqlite3", create=True)
    schema = ops.graph_schema()
    assert schema["object_kinds"] == ["node", "context", "timeline", "source_order"]
    assert set(schema["fields"]) == {"node", "context", "timeline", "source_order"}
    assert len(_json(schema)) < 32000
    assert _compact(schema) is schema


@pytest.mark.parametrize("command", ["graph-analyze"])
def test_cli_forwards_context_flags_through_operations(monkeypatch, capsys, command):
    class FakeOperations:
        def __init__(self):
            self.saved = HarnessSettings().model_dump()
            self.started = None

        def harness_settings(self):
            return dict(self.saved)

        def save_harness_settings(self, settings):
            self.saved = HarnessSettings.model_validate(settings).model_dump()

        def start_graph_analysis(self, source_ids, **kwargs):
            self.started = (source_ids, kwargs)
            return "run"

        def graph_analysis_status(self, run_id):
            return {"id": run_id, "status": "complete"}

        def create_run(self, source_id):
            self.source_id = source_id
            return "run"

        def start_extraction(self, run_id):
            self.started = run_id

        def runs(self):
            return [{"id": "run", "status": "complete"}]

    fake = FakeOperations()
    monkeypatch.setattr(cli, "open_graph_project", lambda path: fake)
    flags = [item for field, value in POLICY.items() for item in ("--" + field.replace("_", "-"), str(value))]
    monkeypatch.setattr("sys.argv", ["narrative", "--db", "unused.sqlite3", command, "source", *flags])
    cli.main()
    assert {field: fake.saved[field] for field in POLICY} == POLICY
    assert fake.started is not None
    assert "complete" in capsys.readouterr().out


def test_invalid_task_output_override_fails_before_runner_key_or_run_creation(tmp_path, monkeypatch):
    ops = GraphOperations(tmp_path / "invalid.sqlite3", create=True)
    sid = ops.import_source("短文", "甲来了。")
    ops.save_harness_settings({
        "provider": "ark", "model": "test-model", "max_tokens": 200,
        "context_window": 1000, "output_reserve": 200,
        "compaction_reserve": 100, "compaction_max_tokens": 50,
    })

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid context policy must fail before SDK/key lookup")

    monkeypatch.setattr(graph_agent, "HarnessRunner", forbidden)
    monkeypatch.setattr(graph_agent, "resolve_key", forbidden)
    with pytest.raises(ValueError, match="上下文窗口"):
        ops.start_graph_analysis([sid], budget={"max_tokens_per_round": 950})
    assert not ops.run_dir().exists()


def test_graph_agent_persists_context_compaction_and_every_actual_request(tmp_path, monkeypatch):
    ops = GraphOperations(tmp_path / "metadata.sqlite3", create=True)
    sid = ops.import_source("短文", "甲来了。")
    calls = []
    model_requests = [
        {"purpose": "compaction", "max_tokens": 32768, "usage": {"total_tokens": 7}, "complete": True, "raw": "摘要", "finish_reason": "stop"},
        {"purpose": "generation", "max_tokens": 131072, "usage": {"total_tokens": 11}, "complete": True, "raw": "partial", "finish_reason": "length"},
    ]
    events = [{"type": "compaction/end", "seq": 1, "data": {"status": "complete"}}]

    class FakeRunner:
        def __init__(self, settings, api_key, directory):
            pass

        def agent_turn(self, instruction, session_id):
            calls.append(instruction)
            return HarnessReply("partial", {
                "finish_reason": "length", "usage": [{"total_tokens": 7}, {"total_tokens": 11}],
                "gateway_upstream_attempt_count": 2,
                "context_policy": {"context_window": 1_000_000},
                "compaction_events": events, "model_requests": model_requests,
                "compaction_request_count": 1,
                "compaction_incomplete": True, "compaction_failure": "summary output length",
                "blocked_generation_count": 1,
            })

        def close(self):
            pass

        def redact(self, value):
            return value

    monkeypatch.setattr(graph_agent, "HarnessRunner", FakeRunner)
    monkeypatch.setattr(graph_agent, "resolve_key", lambda *args: "local-only")
    run_id = ops.start_graph_analysis([sid], budget={"max_rounds": 3})
    for _ in range(100):
        state = ops.graph_analysis_status(run_id)
        if state["status"] not in {"queued", "running"}:
            break
        time.sleep(0.025)
    assert state["status"] == "incomplete"
    assert len(calls) == 1  # Truncation remains terminal, even after compaction.
    assert state["usage"]["total_tokens"] == 18
    assert state["request_count"] == 2
    assert state["compaction_request_count"] == 1
    assert state["steps"][0]["model_requests"] == model_requests
    assert state["steps"][0]["compaction_events"] == events
    assert state["steps"][0]["compaction_incomplete"] is True
    assert state["steps"][0]["compaction_failure"] == "summary output length"
    assert state["steps"][0]["blocked_generation_count"] == 1
    assert state["candidate_ids"] == []
    trace = ops.graph_analysis_trace(run_id)
    assert trace["request_count"] == 2
    assert trace["steps"][0]["model_requests"] == model_requests
    assert trace["steps"][0]["compaction_failure"] == "summary output length"
    response = graph_agent.response(ops, run_id, 1)
    assert response["compaction_incomplete"] is True
    assert response["compaction_failure"] == "summary output length"
    assert response["blocked_generation_count"] == 1
    assert json.loads(calls[0].split("本轮有效context_policy：", 1)[1].split("。", 1)[0])["max_tokens"] == 131072
