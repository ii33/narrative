"""QA hooks forward calls unchanged and time genuine stream completion boundaries."""
import importlib
import time
from pathlib import Path

import pytest


def test_observation_hooks_forward_without_request_mutation(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    module = importlib.import_module("qa_live_observer")
    from narrative import assistant, harness, model_gateway

    class GatewayStub:
        def __init__(self):
            self._usage_records = []

    seen = []
    step = {"tool": "submit_extraction", "ok": True, "args": {"result": {"nodes": []}},
            "result": {"status": "accepted", "node_count": 2}, "duration_ms": 4.2}
    def original(*args, **kwargs):
        seen.append((args, kwargs))
        return step
    monkeypatch.setattr(harness, "ModelGateway", GatewayStub)
    monkeypatch.setattr(assistant, "_call", original)
    observer = module.LiveObserver()
    observer.started(None, {}, {}, tmp_path, time.monotonic())
    with module.observe_real_calls(observer):
        gateway = harness.ModelGateway()
        record = {"purpose": "generation", "usage": {"total_tokens": 7}}
        gateway._usage_records.append(record)
        stream = model_gateway.UsageObserver()
        stream.feed(b'data: {"choices":[],"usage":{"total_tokens":7}}\n\ndata: [DONE]\n\n')
        assert assistant._call("project", "tool", {"original": True}) is step
        snapshot = observer.snapshot()
        assert snapshot["provider_requests"][0]["completed"]
        assert snapshot["provider_requests"][0]["duration_ms"] >= 0
        assert snapshot["first_accepted_submission"]["node_count"] == 2
        assert gateway._usage_records[0] is record
    assert assistant._call is original and harness.ModelGateway is GatewayStub
    assert seen == [(("project", "tool", {"original": True}), {})]


def test_large_source_seed_preserves_exact_text_without_host_slices(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    module = importlib.import_module("accept_two_chapters_fine")
    from narrative.operations import Operations

    if not module.SOURCE_PATH.is_file():
        pytest.skip("User-provided acceptance source is not a repository fixture")
    ops = Operations.create_graph_project(tmp_path / "large.nproj")
    fixture = module.seed(ops)
    assert len(ops.source(fixture["source_id"])["text"]) == 408795
    assert fixture["target_chars"] == 48071
    assert not ops.source_slices(fixture["source_id"])
    assert not ops.graph_snapshot()["nodes"]
