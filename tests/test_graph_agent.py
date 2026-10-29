"""The graph agent must use real Operations contracts and a persistent SDK turn loop."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from narrative import graph_agent
from narrative.graph_operations import GraphOperations
from narrative.harness import HarnessReply, _sdk_error_diagnostics


def _answer(**kwargs) -> str:
    return json.dumps({"plan": "核对原文和图合同", "actions": [], "stage": [], "remove_stage_ids": [], "done": False, "coverage": {}, **kwargs}, ensure_ascii=False)


def _property_records() -> list[dict]:
    """One v5 property batch: a definition, an initial state, and an event change."""
    return [
        {"kind": "property_definition", "id": "state", "value_type": "string", "cardinality": "one", "persistence": "until_changed", "owner_node_types": ["person"]},
        {"kind": "property_value", "id": "e1-p1-state", "node_id": "p1", "property_id": "state", "value": "安然", "event_id": "e1"},
    ]


def test_sdk_error_summary_redacts_secrets_and_omits_other_event_content():
    events = [
        {"type": "assistant/message", "data": {"message": {"content": [{"text": "private model text"}]}}},
        {"type": "turn/end", "data": {"reason": {"kind": "error", "error": {"code": "transport", "message": "Bearer gateway-token; api-secret unavailable", "stack": "private stack"}}}},
    ]
    details = _sdk_error_diagnostics(events, ("api-secret", "gateway-token"))
    assert details == [{"event_type": "turn/end", "kind": "error", "code": "transport", "message": "Bearer [REDACTED]; [REDACTED] unavailable"}]


def test_graph_agent_observes_tool_precheck_then_proposes_pending(tmp_path: Path, monkeypatch):
    db = tmp_path / "graph.nproj"
    ops = GraphOperations(db, create=True)
    source_id = ops.import_source("短篇", "屠夫遇见狼。")
    base_revision = ops.graph_project_info()["revision"]
    base = ops.graph_propose([
        {"kind": "node", "id": "p1", "node_type": "person", "name": "屠夫"},
        {"kind": "node", "id": "e1", "node_type": "event", "name": "屠夫遇狼"},
        {"kind": "node", "id": "e2", "node_type": "event", "name": "狼离开"},
    ], base_revision)
    ops.graph_accept(base, 1)
    records = _property_records()
    responses = [
        _answer(actions=[
            {"tool": "graph_schema", "args": {}},
            {"tool": "source_excerpt", "args": {"source_id": source_id, "start": 0, "end": 7}},
            {"tool": "locate", "args": {"source_id": source_id, "quote": "屠夫遇见狼。"}},
        ]),
        _answer(stage=[
            {"kind": "predicate", "id": "contains_event", "label": "包含事件", "semantic": "containment", "subject_types": ["event"], "object_types": ["event"], "value_type": "node"},
            {"kind": "assertion", "id": "a1", "subject_id": "e1", "predicate_id": "contains_event", "object_id": "e2"},
        ]),
        _answer(actions=[{"tool": "graph_precheck", "args": {}}]),
        _answer(stage=[
            {"kind": "evidence", "id": "ev1", "evidence_ref": "h1"},
            {"kind": "evidence_link", "id": "l1", "assertion_id": "a1", "evidence_id": "ev1"},
        ]),
        _answer(actions=[{"tool": "property_precheck", "args": {"records": records}}]),
        _answer(actions=[{"tool": "property_propose", "args": {"records": records}}]),
        _answer(actions=[{"tool": "graph_precheck", "args": {}}]),
        _answer(actions=[{"tool": "graph_propose", "args": {}}]),
    ]
    calls = []

    class FakeRunner:
        def __init__(self, settings, api_key, directory):
            self.settings = settings

        def agent_turn(self, instruction, session_id):
            calls.append((instruction, session_id))
            return HarnessReply(responses[len(calls) - 1], {"finish_reason": "completed", "usage": [{"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}]})

        def close(self):
            pass

        def redact(self, message):
            return message

        def partial_raw(self):
            return []

        def usage_metadata(self):
            return {}

    monkeypatch.setattr(graph_agent, "HarnessRunner", FakeRunner)
    monkeypatch.setattr(graph_agent, "resolve_key", lambda settings, supplied: "local-test-key")
    run_id = ops.start_graph_analysis([source_id], task="记录遭遇事件与人物状态")
    for _ in range(100):
        state = ops.graph_analysis_status(run_id)
        if state["status"] not in {"queued", "running"}:
            break
        time.sleep(0.05)
    assert state["status"] == "complete", state.get("error")
    assert len(calls) == 8
    assert len({session_id for _, session_id in calls}) == 1
    assert "h1" in calls[1][0]  # The second model turn observes the first turn's locate result.
    assert any(d["code"] == "EVIDENCE_REQUIRED" for d in state["steps"][2]["diagnostics"])
    assert state["steps"][6]["diagnostics"] == []
    assert state["steps"][4]["tools"][0]["tool"] == "property_precheck"
    assert state["steps"][4]["tools"][0]["result"]["passed"] is True
    assert state["steps"][5]["tools"][0]["result"]["status"] == "pending"
    assert len(state["candidate_ids"]) == 1
    candidate = ops.graph_candidate(state["candidate_ids"][0])
    assert candidate["status"] == "pending"
    assert candidate["expected_revision"] == ops.graph_project_info()["revision"]
    assert {obj["id"] for obj in candidate["objects"]} == {"contains_event", "a1", "ev1", "l1"}
    assert ops.graph_snapshot().get("assertions", []) == []
    pending_properties = ops.property_candidates("pending")
    assert len(pending_properties) == 1
    assert pending_properties[0]["records"] == records
    assert ops.property_candidates("accepted") == []


def test_graph_agent_seeds_pending_candidate_without_accepting_parent(tmp_path: Path, monkeypatch):
    ops = GraphOperations(tmp_path / "seed.nproj", create=True)
    source_id = ops.import_source("短篇", "甲从门前经过。")
    revision = ops.graph_project_info()["revision"]
    parent_id = ops.graph_propose([{"kind": "node", "id": "p1", "node_type": "person", "name": "甲"}], revision, source="model")
    responses = [
        _answer(actions=[{"tool": "source_excerpt", "args": {"source_id": source_id, "start": 0, "end": 8}}]),
        _answer(stage=[{"kind": "node", "id": "p1", "node_type": "person", "name": "甲", "identity_note": "此段中的甲"}]),
        _answer(actions=[{"tool": "graph_precheck", "args": {}}]),
        _answer(actions=[{"tool": "graph_propose", "args": {}}]),
    ]
    calls = []

    class FakeRunner:
        def __init__(self, settings, api_key, directory):
            pass

        def agent_turn(self, instruction, session_id):
            calls.append(instruction)
            return HarnessReply(responses[len(calls) - 1], {"finish_reason": "completed", "usage": []})

        def close(self):
            pass

        def redact(self, message):
            return message

        def partial_raw(self):
            return []

        def usage_metadata(self):
            return {}

    monkeypatch.setattr(graph_agent, "HarnessRunner", FakeRunner)
    monkeypatch.setattr(graph_agent, "resolve_key", lambda settings, supplied: "local-test-key")
    run_id = ops.start_graph_analysis([source_id], task="核对这段人物身份", feedback="明确本段范围", seed_candidate_id=parent_id)
    for _ in range(100):
        state = ops.graph_analysis_status(run_id)
        if state["status"] not in {"queued", "running"}:
            break
        time.sleep(0.05)
    assert state["status"] == "complete", state.get("error")
    assert state["lineage"]["parent_candidate_id"] == parent_id
    assert ops.graph_candidate(parent_id)["status"] == "pending"
    child = ops.graph_candidate(state["candidate_ids"][0])
    assert child["parent_candidate_id"] == parent_id
    assert child["objects"][0]["identity_note"] == "此段中的甲"
    assert ops.graph_project_info()["revision"] == revision


def test_graph_agent_provider_failure_stops_without_retry(tmp_path: Path, monkeypatch):
    ops = GraphOperations(tmp_path / "failed.nproj", create=True)
    source_id = ops.import_source("短篇", "甲来了。")
    calls = []

    class FailingRunner:
        def __init__(self, settings, api_key, directory):
            pass

        def agent_turn(self, instruction, session_id):
            calls.append(session_id)
            raise ValueError("provider unavailable")

        def partial_raw(self):
            return ["partial provider text"]

        def usage_metadata(self):
            return {"usage": [], "usage_incomplete": True}

        def redact(self, message):
            return message

        def close(self):
            pass

    monkeypatch.setattr(graph_agent, "HarnessRunner", FailingRunner)
    monkeypatch.setattr(graph_agent, "resolve_key", lambda settings, supplied: "local-test-key")
    run_id = ops.start_graph_analysis([source_id])
    for _ in range(100):
        state = ops.graph_analysis_status(run_id)
        if state["status"] not in {"queued", "running"}:
            break
        time.sleep(0.05)
    assert state["status"] == "incomplete"
    assert len(calls) == 1
    assert state["steps"][0]["raw"] == ["partial provider text"]
    assert state["candidate_ids"] == []


def test_graph_agent_pause_and_instruction_apply_at_next_request_boundary(tmp_path: Path, monkeypatch):
    ops = GraphOperations(tmp_path / "control.nproj", create=True)
    source_id = ops.import_source("短篇", "甲来了。")
    entered = threading.Event()
    release = threading.Event()
    calls = []
    responses = [
        _answer(actions=[{"tool": "graph_schema", "args": {}}]),
        _answer(stage=[{"kind": "node", "id": "p1", "node_type": "person", "name": "甲"}]),
        _answer(actions=[{"tool": "graph_precheck", "args": {}}]),
        _answer(actions=[{"tool": "graph_propose", "args": {}}]),
    ]

    class FakeRunner:
        def __init__(self, settings, api_key, directory):
            pass

        def agent_turn(self, instruction, session_id):
            calls.append(instruction)
            if len(calls) == 1:
                entered.set()
                assert release.wait(3)
            return HarnessReply(responses[len(calls) - 1], {"finish_reason": "completed", "usage": []})

        def close(self):
            pass

        def redact(self, message):
            return message

        def partial_raw(self):
            return []

        def usage_metadata(self):
            return {}

    monkeypatch.setattr(graph_agent, "HarnessRunner", FakeRunner)
    monkeypatch.setattr(graph_agent, "resolve_key", lambda settings, supplied: "local-test-key")
    run_id = ops.start_graph_analysis([source_id])
    assert entered.wait(3)
    journal = ops.run_dir() / run_id / "journal.json"
    before = journal.read_bytes()
    worker_key = (ops.graph_project_info()["path"], run_id)
    worker = graph_agent._jobs.pop(worker_key)
    try:
        observed = GraphOperations(ops.graph_project_info()["path"]).graph_analysis_status(run_id)
        assert observed["observer_state"] == "unverified_external"
        assert observed["worker_local"] is False
        assert journal.read_bytes() == before
    finally:
        graph_agent._jobs[worker_key] = worker
    ops.pause_graph_analysis(run_id)
    instruction_id = ops.instruct_graph_analysis(run_id, "只分析此段的人物")
    release.set()
    for _ in range(100):
        state = ops.graph_analysis_status(run_id)
        if state["request_state"] == "paused_waiting":
            break
        time.sleep(0.05)
    assert state["request_state"] == "paused_waiting"
    assert len(calls) == 1
    assert ops.graph_analysis_control(run_id)["pending_count"] == 1
    ops.resume_graph_analysis(run_id)
    for _ in range(100):
        state = ops.graph_analysis_status(run_id)
        if state["status"] not in {"queued", "running"}:
            break
        time.sleep(0.05)
    assert state["status"] == "complete", state.get("error")
    assert "只分析此段的人物" in calls[1]
    assert instruction_id in state["steps"][1]["instruction_ids"]
    assert ops.graph_analysis_control(run_id)["pending_count"] == 0
    assert ops.graph_candidate(state["candidate_ids"][0])["status"] == "pending"


def test_graph_agent_real_sdk_session_dispatches_graph_tools(tmp_path: Path):
    ops = GraphOperations(tmp_path / "sdk.nproj", create=True)
    source_id = ops.import_source("短篇", "甲遇见狼。")
    base_revision = ops.graph_project_info()["revision"]
    base = ops.graph_propose([
        {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
        {"kind": "node", "id": "e1", "node_type": "event", "name": "甲遇见狼"},
        {"kind": "node", "id": "e2", "node_type": "event", "name": "狼离开"},
    ], base_revision)
    ops.graph_accept(base, 1)
    records = _property_records()
    responses = [
        _answer(actions=[{"tool": "graph_schema", "args": {}}, {"tool": "locate", "args": {"source_id": source_id, "quote": "甲遇见狼。"}}]),
        _answer(stage=[
            {"kind": "predicate", "id": "contains_event", "label": "包含事件", "semantic": "containment", "subject_types": ["event"], "object_types": ["event"], "value_type": "node"},
            {"kind": "assertion", "id": "a1", "subject_id": "e1", "predicate_id": "contains_event", "object_id": "e2"},
            {"kind": "evidence", "id": "ev1", "evidence_ref": "h1"},
            {"kind": "evidence_link", "id": "l1", "assertion_id": "a1", "evidence_id": "ev1"},
        ]),
        _answer(actions=[{"tool": "graph_precheck", "args": {}}]),
        _answer(actions=[{"tool": "property_precheck", "args": {"records": records}}]),
        _answer(actions=[{"tool": "property_propose", "args": {"records": records}}]),
        _answer(actions=[{"tool": "graph_propose", "args": {}}]),
    ]
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            raw = responses[len(requests) - 1]
            chunks = [
                {"id": "graph-test", "object": "chat.completion.chunk", "created": 1, "model": body["model"], "choices": [{"index": 0, "delta": {"role": "assistant", "content": raw}, "finish_reason": None}], "usage": None},
                {"id": "graph-test", "object": "chat.completion.chunk", "created": 1, "model": body["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
            ]
            wire = ("".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1"
        ops.save_harness_settings({"provider": "custom", "base_url": url, "model": "local-scripted", "reasoning_effort": None, "timeout_seconds": 15})
        run_id = ops.start_graph_analysis([source_id], api_key="test-secret", budget={"request_seconds": 15})
        for _ in range(600):
            state = ops.graph_analysis_status(run_id)
            if state["status"] not in {"queued", "running"}:
                break
            time.sleep(0.05)
        assert state["status"] == "complete", state.get("error")
        assert len(requests) == state["rounds"] == 6
        assert "h1" in json.dumps(requests[1], ensure_ascii=False)
        assert {step["finish_reason"] for step in state["steps"]} == {"completed"}
        assert state["usage"]["total_tokens"] == 90
        assert "test-secret" not in (ops.run_dir() / run_id / "journal.json").read_text(encoding="utf-8")
        assert ops.graph_candidate(state["candidate_ids"][0])["status"] == "pending"
        assert ops.graph_snapshot()["assertions"] == []
        pending_properties = ops.property_candidates("pending")
        assert [row["status"] for row in pending_properties] == ["pending"]
        assert ops.property_candidates("accepted") == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_property_tool_flags_staged_graph_dependency_without_writes(tmp_path: Path):
    db = tmp_path / "staged-property.nproj"
    ops = GraphOperations(db, create=True)
    source_id = ops.import_source("短篇", "屠夫遇见狼。")
    revision = ops.graph_project_info()["revision"]
    records = [
        {"kind": "property_definition", "id": "state", "value_type": "node", "cardinality": "one", "persistence": "until_changed"},
        {"kind": "property_value", "id": "p1-state", "node_id": "p1", "property_id": "state", "value": "p2", "event_id": "e1"},
        {"kind": "property_value", "id": "p1-observe", "node_id": "p1", "property_id": "state", "value": "p2", "event_id": "e1"},
    ]
    state = {"workspace": {
        "p1": {"kind": "node", "id": "p1", "node_type": "person", "name": "屠夫"},
        "p2": {"kind": "node", "id": "p2", "node_type": "person", "name": "狼"},
        "e1": {"kind": "node", "id": "e1", "node_type": "event", "name": "遇狼"},
        "contains": {"kind": "predicate", "id": "contains", "label": "含", "semantic": "containment", "value_type": "node"},
    }}
    for tool in ("property_precheck", "property_propose"):
        result = graph_agent._tool(ops, [source_id], tool, {"records": records}, state)
        assert result["status"] == "pending_graph_review"
        assert result["tool"] == tool
        assert result["passed"] is False
        assert [item["code"] for item in result["diagnostics"]] == ["PENDING_GRAPH_REVIEW"]
        assert set(result["staged_refs"]) == {"p1", "p2", "e1"}
        assert "graph_propose" in result["next_action"]

    # No property candidate, record, or revision bump is written for a staged graph.
    assert ops.property_candidates("pending") == []
    with sqlite3.connect(db) as raw:
        assert raw.execute("SELECT COUNT(*) FROM property_candidates").fetchone()[0] == 0
        assert raw.execute("SELECT COUNT(*) FROM property_records").fetchone()[0] == 0
        assert raw.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0] == str(revision)


def test_property_tool_preserves_accepted_graph_operations(tmp_path: Path):
    db = tmp_path / "accepted-property.nproj"
    ops = GraphOperations(db, create=True)
    source_id = ops.import_source("短篇", "屠夫遇见狼。")
    revision = ops.graph_project_info()["revision"]
    base = ops.graph_propose([
        {"kind": "node", "id": "p1", "node_type": "person", "name": "屠夫"},
        {"kind": "node", "id": "e1", "node_type": "event", "name": "遇狼"},
    ], revision)
    ops.graph_accept(base, 1)
    accepted_revision = ops.graph_project_info()["revision"]
    records = [
        {"kind": "property_definition", "id": "state", "value_type": "string", "cardinality": "one", "persistence": "until_changed", "owner_node_types": ["person"]},
        {"kind": "property_value", "id": "e1-p1-state", "node_id": "p1", "property_id": "state", "value": "安然", "event_id": "e1"},
    ]
    # A private graph draft re-stages an accepted node and adds a new one, but the
    # records depend only on accepted nodes, so normal property operations must run.
    state = {"workspace": {
        "p1": {"kind": "node", "id": "p1", "node_type": "person", "name": "屠夫草稿"},
        "e2": {"kind": "node", "id": "e2", "node_type": "event", "name": "狼离开"},
    }}
    precheck = graph_agent._tool(ops, [source_id], "property_precheck", {"records": records}, state)
    assert precheck["passed"] is True
    assert precheck["diagnostics"] == []
    proposed = graph_agent._tool(ops, [source_id], "property_propose", {"records": records}, state)
    assert proposed["status"] == "pending"
    assert ops.property_candidate(proposed["candidate_id"])["status"] == "pending"
    assert ops.graph_project_info()["revision"] == accepted_revision


def test_property_propose_accepts_advertised_source_argument_without_changing_provenance(
    tmp_path: Path,
):
    """The schema advertises a source argument; the bridge must not reject it.

    A live graph-agent/11 run passed the accepted source id as ``source`` and the
    generic "参数无效" error ended the run before it could retry. Provenance must
    stay system-controlled, but a reasonable argument must not fail the call.
    """
    db = tmp_path / "source-argument-property.nproj"
    ops = GraphOperations(db, create=True)
    source_id = ops.import_source("短篇", "屠夫遇见狼。")
    revision = ops.graph_project_info()["revision"]
    base = ops.graph_propose(
        [
            {"kind": "node", "id": "p1", "node_type": "person", "name": "屠夫"},
            {"kind": "node", "id": "e1", "node_type": "event", "name": "遇狼"},
        ],
        revision,
    )
    ops.graph_accept(base, 1)
    records = [
        {
            "kind": "property_definition",
            "id": "state",
            "value_type": "string",
            "cardinality": "one",
            "persistence": "until_changed",
            "owner_node_types": ["person"],
        },
        {
            "kind": "property_value",
            "id": "p1-state",
            "node_id": "p1",
            "property_id": "state",
            "value": "警觉",
            "event_id": "e1",
        },
    ]
    result = graph_agent._tool(
        ops, [source_id], "property_propose", {"records": records, "source": source_id}, {}
    )
    assert result["status"] == "pending"
    assert result["source"] == "model"
    assert ops.property_candidate(result["candidate_id"])["source"] == "model"
