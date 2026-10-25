"""Focused tests for the v5 graph validation runner, budget reserves, and activity.

No real model calls: loopback fake servers only. Streamlit is not imported.
"""

from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from narrative import graph_agent
from narrative.graph_operations import GraphOperations
from narrative.harness import HarnessReply, HarnessRunner, HarnessSettings
from narrative.model_gateway import ModelGateway

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_v5_graph_validation.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_v5_graph_validation", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# -- manifest precheck -------------------------------------------------------


def test_manifest_requires_three_contiguous_slices_and_sha256(tmp_path: Path):
    runner = _load_runner()
    text = "第一章\n" + "甲" * 10 + "\n第二章\n" + "乙" * 10 + "\n第三章\n" + "丙" * 10
    source = tmp_path / "source.txt"
    source.write_bytes(text.encode("utf-8"))
    first = text.index("第二章")
    second = text.index("第三章")
    manifest = {
        "chapters": [
            {"title": "一", "start": 0, "end": first, "sha256": _sha256(text[0:first])},
            {"title": "二", "start": first, "end": second, "sha256": _sha256(text[first:second])},
            {"title": "三", "start": second, "end": len(text), "sha256": _sha256(text[second:])},
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    result = runner.verify_source_manifest(source, manifest_path)
    assert result["slice_count"] == 3
    assert len(result["input_hash"]) == 64
    assert result["source_sha256"] == _sha256(text)
    assert [row["title"] for row in result["chapters"]] == ["一", "二", "三"]

    broken = json.loads(manifest_path.read_text(encoding="utf-8"))
    broken["chapters"][1]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 不匹配"):
        runner.verify_source_manifest(source, manifest_path)

    manifest_path.write_text(
        json.dumps({"chapters": broken["chapters"][:2]}, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="3 个连续章节"):
        runner.verify_source_manifest(source, manifest_path)

    gap = {
        "chapters": [
            {"title": "一", "start": 0, "end": first, "sha256": _sha256(text[0:first])},
            {"title": "二", "start": first + 1, "end": second, "sha256": _sha256(text[first + 1:second])},
            {"title": "三", "start": second, "end": len(text), "sha256": _sha256(text[second:])},
        ]
    }
    manifest_path.write_text(json.dumps(gap, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="不连续"):
        runner.verify_source_manifest(source, manifest_path)


def test_output_dir_is_created_exclusively(tmp_path: Path):
    runner = _load_runner()
    target = tmp_path / "out"
    created = runner.create_output_dir(target)
    assert created == target.resolve() and created.is_dir()
    (created / "existing.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="禁止覆盖"):
        runner.create_output_dir(target)
    assert (created / "existing.txt").read_text(encoding="utf-8") == "keep"


# -- activity reporting ------------------------------------------------------


def test_progress_keeps_upstream_activity_separate_from_heartbeat():
    runner = _load_runner()
    heartbeat = "2026-10-19T00:00:00+00:00"
    base = {
        "run_id": "a" * 32,
        "runner_pid": 1234,
        "status": "running",
        "phase": "draft",
        "rounds": 3,
        "request_state": "in_flight",
        "usage": {"total_tokens": 42},
        "candidate_count": 2,
        "budget": {"max_rounds": 32},
        "started_utc": heartbeat,
        "heartbeat_utc": heartbeat,
    }
    fresh = runner.build_progress(upstream_activity=None, **base)
    assert fresh["heartbeat_utc"] == heartbeat
    # The upstream timestamp must never be inferred from the local heartbeat.
    assert fresh["last_upstream_response_utc"] is None
    assert fresh["upstream_response_bytes"] is None

    upstream = {
        "last_response_utc": "2026-10-19T00:00:05+00:00",
        "last_response_age_seconds": 0.5,
        "response_bytes": 99,
        "response_events": 3,
        "source": "model_gateway_upstream",
    }
    observed = runner.build_progress(upstream_activity=upstream, **base)
    assert observed["last_upstream_response_utc"] == upstream["last_response_utc"]
    assert observed["last_upstream_response_utc"] != observed["heartbeat_utc"]
    assert observed["upstream_response_bytes"] == 99
    assert observed["upstream_activity_source"] == "model_gateway_upstream"


def test_harness_runner_exposes_model_gateway_activity_without_sdk():
    class FakeGateway:
        def upstream_activity_snapshot(self):
            return {"last_response_utc": "2026-10-19T00:00:05+00:00", "source": "model_gateway_upstream"}

    runner = HarnessRunner.__new__(HarnessRunner)
    runner._gateway = FakeGateway()
    assert runner.upstream_activity_snapshot()["source"] == "model_gateway_upstream"


def test_model_gateway_records_upstream_response_bytes():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            wire = (
                b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
                b'"usage":{"total_tokens":3}}\n\n'
                b'data: [DONE]\n\n'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gateway = None
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1"
        settings = HarnessSettings(
            provider="custom", base_url=url, model="local-scripted",
            reasoning_effort=None, timeout_seconds=15,
        )
        gateway = ModelGateway(settings, "local-key")
        fresh = gateway.upstream_activity_snapshot()
        assert fresh["last_response_utc"] is None
        assert fresh["response_bytes"] == 0

        parsed = urllib.parse.urlsplit(gateway.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
        body = json.dumps(
            {"model": "local-scripted", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
        ).encode()
        connection.request(
            "POST",
            parsed.path.rstrip("/") + "/chat/completions",
            body,
            {"Content-Type": "application/json", "Authorization": f"Bearer {gateway.token}"},
        )
        response = connection.getresponse()
        response.read()
        connection.close()

        observed = gateway.upstream_activity_snapshot()
        assert observed["last_response_utc"] is not None
        assert observed["response_bytes"] > 0
        assert observed["response_events"] >= 1
        assert observed["source"] == "model_gateway_upstream"
    finally:
        if gateway is not None:
            gateway.close()
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_graph_agent_upstream_activity_reads_live_gateway(tmp_path: Path, monkeypatch):
    ops = GraphOperations(tmp_path / "activity.sqlite3", create=True)
    sid = ops.import_source("短篇", "甲来了。")
    snapshot = {
        "last_response_utc": "2026-10-19T00:00:00+00:00",
        "last_response_age_seconds": 0.1,
        "response_bytes": 42,
        "response_events": 2,
        "source": "model_gateway_upstream",
    }

    class FakeRunner:
        def __init__(self, settings, api_key, directory):
            pass

        def upstream_activity_snapshot(self):
            return dict(snapshot)

        def agent_turn(self, instruction, session_id):
            return HarnessReply("不是JSON", {"finish_reason": "completed", "usage": []})

        def close(self):
            pass

        def redact(self, message):
            return message

        def partial_raw(self):
            return []

        def usage_metadata(self):
            return {}

    monkeypatch.setattr(graph_agent, "HarnessRunner", FakeRunner)
    monkeypatch.setattr(graph_agent, "resolve_key", lambda *args: "local-test-key")
    run_id = ops.start_graph_analysis([sid], budget={"max_rounds": 1})
    for _ in range(100):
        state = ops.graph_analysis_status(run_id)
        if state["status"] not in {"queued", "running"}:
            break
        time.sleep(0.02)
    assert graph_agent.upstream_activity(ops, run_id) == snapshot


# -- budget reserve ----------------------------------------------------------


def test_request_reserve_counts_cjk_and_explicit_output_cap():
    reserve = graph_agent._request_reserve("甲" * 10, 1000)
    assert reserve["estimated_prompt_tokens"] >= 10
    assert reserve["reserved_output_tokens"] == 1000
    assert reserve["reserved_total_tokens"] == reserve["estimated_prompt_tokens"] + 1000
    assert reserve["hard_token_guarantee"] is False
    assert graph_agent._request_reserve("", None)["reserved_output_tokens"] == 0
    assert graph_agent._estimate_prompt_tokens("") == 0


def test_graph_agent_stops_before_request_when_reserve_exceeds_budget(tmp_path: Path, monkeypatch):
    ops = GraphOperations(tmp_path / "budget.sqlite3", create=True)
    sid = ops.import_source("短篇", "甲遇见狼。")
    calls = []

    class FakeRunner:
        def __init__(self, settings, api_key, directory):
            pass

        def agent_turn(self, instruction, session_id):
            calls.append(instruction)
            return HarnessReply("{}", {"finish_reason": "completed", "usage": []})

        def close(self):
            pass

        def redact(self, message):
            return message

        def partial_raw(self):
            return []

        def usage_metadata(self):
            return {}

    monkeypatch.setattr(graph_agent, "HarnessRunner", FakeRunner)
    monkeypatch.setattr(graph_agent, "resolve_key", lambda *args: "local-test-key")
    run_id = ops.start_graph_analysis([sid], budget={"max_tokens_per_round": 1000, "max_total_tokens": 100})
    for _ in range(100):
        state = ops.graph_analysis_status(run_id)
        if state["status"] not in {"queued", "running"}:
            break
        time.sleep(0.02)
    assert state["status"] == "incomplete"
    assert "预留" in state["error"]
    assert calls == []
    assert state["steps"] == []
    assert state["request_reserve"]["reserved_output_tokens"] == 1000


# -- final report ------------------------------------------------------------


def test_report_marks_review_action_none_and_excludes_secrets(tmp_path: Path):
    runner = _load_runner()
    journal = tmp_path / "journal.json"
    journal.write_text("{}", encoding="utf-8")
    verification = {
        "input_hash": "a" * 64,
        "source_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
        "chapters": [{"title": "一"}],
    }
    report = runner.build_report(
        runner_pid=999,
        run_id="d" * 32,
        status_state={
            "status": "complete",
            "request_state": "stopped",
            "prompt_version": "graph-agent/10",
            "provider": "custom",
            "model": "m",
            "config": {"provider": "custom", "model": "m", "key_env": "NARRATIVE_API_KEY"},
            "usage": {"total_tokens": 10},
            "precheck_diagnostics": [],
            "steps": [
                {
                    "round": 1,
                    "phase": "submit",
                    "finish_reason": "completed",
                    "usage": [],
                    "input_hash": "e" * 64,
                    "tools": [],
                }
            ],
        },
        verification=verification,
        profile={"provider": "custom", "model": "m", "key_env": "NARRATIVE_API_KEY"},
        budget={"max_rounds": 32},
        journal_path=journal,
        source_id="s1",
        source_hash=verification["source_sha256"],
        started_utc="2026-10-19T00:00:00+00:00",
        ended_utc="2026-10-19T00:01:00+00:00",
        candidates={
            "graph": [{"id": "c1", "status": "pending", "version": 1}],
            "property": [],
            "count": 1,
            "accepted": 0,
        },
        upstream_activity=None,
    )
    assert report["review_action"] == "none"
    assert report["prompt_version"] == "graph-agent/10"
    assert report["input_hash"] == "a" * 64
    assert report["response_journal_location"].endswith("journal.json")
    assert report["finish_reason"] == "completed"
    assert report["candidates"]["count"] == 1
    assert all(row["status"] != "accepted" for row in report["candidates"]["graph"])
    serialized = json.dumps(report, ensure_ascii=False)
    # The env-var *name* is safe metadata; no secret field or value is recorded.
    assert "NARRATIVE_API_KEY" in serialized
    assert "sk-live-secret" not in serialized
    for secret_field in ('"api_key"', '"apikey"', '"authorization"', '"bearer"', '"secret"'):
        assert secret_field not in serialized.lower()
