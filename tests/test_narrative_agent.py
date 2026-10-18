import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

from narrative.harness import HarnessSettings
from narrative.narrative_agent import Budget, _materialize, _tool
from narrative.operations import Operations


def _server(responses, first_release=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if first_release is not None and len(requests) == 1:
                first_release.wait(15)
            raw, reason = responses[min(len(requests) - 1, len(responses) - 1)]

            def frame(delta, finish=None, usage=None):
                return {"id": "analysis-test", "object": "chat.completion.chunk", "created": 1,
                        "model": body["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], "usage": usage}

            chunks = [frame({"role": "assistant", "content": raw}),
                      frame({}, reason, {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})]
            wire = ("".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks)
                    + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}/v1", requests


def _wait(app, run_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = app.narrative_analysis_status(run_id)
        if state["status"] not in {"queued", "running"}:
            return state
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


def _app(tmp_path, url):
    app = Operations(tmp_path / "agent.sqlite3")
    app.save_harness_settings({"base_url": url, "model": "Doubao-Seed-2.1-pro", "timeout_seconds": 15})
    source_id = app.import_text("短篇", "甲在渡口送乙离开。乙回头挥手。")
    return app, source_id


@pytest.mark.parametrize(
    ("profile_limit", "task_limit", "token_parameter", "expected_key", "expected_value"),
    [
        (131072, "inherit", "max_tokens", "max_tokens", 131072),
        (None, "inherit", "max_tokens", None, None),
        (8192, "service-default", "max_tokens", None, None),
        (8192, 256000, "max_tokens", "max_tokens", 256000),
        (8192, 131072, "max_completion_tokens", "max_completion_tokens", 131072),
    ],
)
def test_agent_output_limit_modes_reach_real_sdk_request_body(
    tmp_path, profile_limit, task_limit, token_parameter, expected_key, expected_value
):
    server, thread, url, requests = _server([("{}", "length")])
    try:
        app, source_id = _app(tmp_path, url)
        app.save_harness_settings({
            "base_url": url, "model": "Doubao-Seed-2.1-pro", "timeout_seconds": 15,
            "max_tokens": profile_limit, "token_parameter": token_parameter,
        })
        run_id = app.start_narrative_analysis(
            [source_id], budget={"max_tokens_per_round": task_limit}, api_key="test-secret"
        )
        state = _wait(app, run_id)
        assert state["status"] == "incomplete" and state["rounds"] == len(requests) == 1
        assert state["budget"]["max_tokens_per_round"] == task_limit
        assert state["config"]["max_tokens"] == expected_value
        body = requests[0]
        for key in ("max_tokens", "max_completion_tokens"):
            if key == expected_key:
                assert body[key] == expected_value
            else:
                assert key not in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_output_limit_contract_accepts_positive_values_without_local_ceiling():
    for value in (8192, 131072, 256000):
        assert HarnessSettings(max_tokens=value).max_tokens == value
        assert Budget.parse({"max_tokens_per_round": value}).max_tokens_per_round == value
    assert HarnessSettings(max_tokens=None).max_tokens is None
    assert Budget.parse(None).max_tokens_per_round == "inherit"
    assert Budget.parse({"max_tokens_per_round": "service-default"}).max_tokens_per_round == "service-default"
    assert Budget.parse({"max_tokens_per_round": 8192}).asdict()["max_tokens_per_round"] == 8192
    for invalid in (False, 0, -1, 3.2, "256000", None):
        with pytest.raises(ValueError):
            Budget.parse({"max_tokens_per_round": invalid})
    for invalid in (False, 0, -1, 3.2, "256000"):
        with pytest.raises(ValueError):
            HarnessSettings.model_validate({"max_tokens": invalid})


def test_sdk_agent_reads_tool_observation_then_proposes_review_only(tmp_path):
    first = json.dumps({"plan": "先读原文和定位证据", "actions": [{"tool": "source_excerpt", "args": {"source_id": "SOURCE", "start": 0, "end": 100}}, {"tool": "locate", "args": {"source_id": "SOURCE", "quote": "甲"}}], "stage": [], "done": False}, ensure_ascii=False)
    second = json.dumps({"plan": "据原文提案", "actions": [], "stage": [{"kind": "entity", "id": "person-a", "name": "甲", "entity_type": "person", "evidence_refs": ["h1"]}], "done": True}, ensure_ascii=False)
    submit = json.dumps({"plan": "证据核对完成", "actions": [], "stage": [], "done": True}, ensure_ascii=False)
    responses = [(first, "stop"), (second, "stop"), (submit, "stop")]
    server, thread, url, requests = _server(responses)
    try:
        app, source_id = _app(tmp_path, url)
        responses[0] = (first.replace("SOURCE", source_id), "stop")
        responses[1] = (second, "stop")
        run_id = app.start_narrative_analysis([source_id], budget={"research_rounds": 1}, api_key="test-secret")
        state = _wait(app, run_id)
        assert state["status"] == "complete", state
        assert len(requests) == 3
        assert all(not request.get("tools") for request in requests)
        assert "甲在渡口送乙离开" in json.dumps(requests[1], ensure_ascii=False)
        assert state["rounds"] == 3 and state["tool_count"] == 2
        assert state["usage"]["total_tokens"] == 450
        assert state["steps"][0]["tools"][0]["name"] == "source_excerpt"
        assert state["steps"][0]["raw"] and state["steps"][2]["phase"] == "submit"
        assert len(state["candidate_ids"]) == 1
        assert app.candidates("pending")[0]["id"] == state["candidate_ids"][0]
        assert not app.entities()
        assert "test-secret" not in json.dumps(state)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_truncated_agent_turn_stops_without_second_request(tmp_path):
    response = json.dumps({"actions": [{"tool": "analysis_context", "args": {}}], "objects": [], "done": False})
    server, thread, url, requests = _server([(response, "length")])
    try:
        app, source_id = _app(tmp_path, url)
        run_id = app.start_narrative_analysis([source_id], api_key="test-secret")
        state = _wait(app, run_id)
        assert state["status"] == "incomplete"
        assert len(requests) == 1 and state["tool_count"] == 0
        assert state["steps"][0]["raw"] == response
        assert state["usage"]["total_tokens"] == 150
        assert not state["candidate_ids"] and not app.candidates()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_precheck_feedback_allows_one_revised_batch(tmp_path):
    invalid = {"plan": "先提出人物", "actions": [], "objects": [{"kind": "entity", "id": "stable-a", "name": "甲", "entity_type": "invalid"}], "done": True}
    valid = {"plan": "按预检改正类型", "actions": [], "objects": [{"kind": "entity", "id": "stable-a", "name": "甲", "entity_type": "person"}], "done": True}
    ready = {"plan": "读完短篇", "actions": [], "done": True}
    submit = {"plan": "自查完毕", "actions": [], "done": True}
    server, thread, url, requests = _server([(json.dumps(item, ensure_ascii=False), "stop") for item in (ready, invalid, valid, submit)])
    try:
        app, source_id = _app(tmp_path, url)
        run_id = app.start_narrative_analysis([source_id], api_key="test-secret")
        state = _wait(app, run_id)
        assert state["status"] == "complete", state
        assert len(requests) == 4
        assert len(state["candidate_ids"]) == 1
        assert state["steps"][1]["stage_diagnostics"][0]["severity"] == "error"
        assert "对象结构错误" in json.dumps(requests[2], ensure_ascii=False)
        assert len(app.candidates("pending")) == 1 and not app.entities()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_staged_workspace_expands_locate_handle_once_at_final_submit(tmp_path):
    responses = [
        (json.dumps({"plan": "定位证据", "actions": [{"tool": "locate", "args": {"source_id": "SOURCE", "quote": "甲"}}], "stage": [], "done": False}, ensure_ascii=False), "stop"),
        (json.dumps({"plan": "阶段保存人物", "actions": [], "stage": [{"kind": "entity", "id": "stable-a", "name": "甲", "entity_type": "person", "evidence_refs": ["h1"]}], "done": False}, ensure_ascii=False), "stop"),
        (json.dumps({"plan": "完成", "actions": [], "stage": [], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"plan": "核对完成", "actions": [], "stage": [], "done": True}, ensure_ascii=False), "stop"),
    ]
    server, thread, url, requests = _server(responses)
    try:
        app, source_id = _app(tmp_path, url)
        responses[0] = (responses[0][0].replace("SOURCE", source_id), "stop")
        run_id = app.start_narrative_analysis([source_id], budget={"research_rounds": 1}, api_key="test-secret")
        state = _wait(app, run_id)
        assert state["status"] == "complete", state
        assert len(requests) == 4 and state["rounds"] == 4
        assert state["steps"][1]["stage_ids"] == ["stable-a"]
        assert state["steps"][1].get("candidate_id") is None
        assert state["proposed_objects"][0]["evidence"] == [state["evidence_handles"]["h1"]]
        assert state["proposed_objects"][0]["evidence"][0]["quote"] == "甲"
        assert len(state["candidate_ids"]) == 1 and not app.entities()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_stage_rejects_unknown_handle_and_handwritten_evidence():
    with pytest.raises(ValueError, match="未知证据 handle"):
        _materialize({"p1": {"kind": "entity", "id": "p1", "name": "甲", "evidence_refs": ["h999"]}}, {})
    with pytest.raises(ValueError, match="不接收手填 evidence"):
        _materialize({"p1": {"kind": "entity", "id": "p1", "name": "甲", "evidence": [{"quote": "甲"}]}}, {})


def test_many_tools_in_one_research_response_are_not_a_task_limit(tmp_path):
    actions = [{"tool": "source_excerpt", "args": {"source_id": "SOURCE", "start": 0, "end": 12}} for _ in range(21)]
    actions.append({"tool": "locate_many", "args": {"source_id": "SOURCE", "quotes": ["甲", "乙"]}})
    responses = [
        (json.dumps({"actions": actions, "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"stage": [{"kind": "entity", "id": "p1", "name": "甲", "entity_type": "person", "evidence_refs": ["h1"]}], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"done": True}), "stop"),
    ]
    server, thread, url, requests = _server(responses)
    try:
        app, source_id = _app(tmp_path, url)
        responses[0] = (responses[0][0].replace("SOURCE", source_id), "stop")
        state = _wait(app, app.start_narrative_analysis([source_id], api_key="test-secret"))
        assert state["status"] == "complete", state
        assert len(requests) == 3 and state["tool_count"] == 22
        assert state["phase_rounds_used"] == {"research": 1, "draft": 1, "correction": 0, "submit": 1}
        assert {"h1", "h2"} <= state["evidence_handles"].keys()
        assert state["budget"]["max_rounds"] is None and state["budget"]["wall_seconds"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_submit_retraction_invalidates_precheck_and_rechecks(tmp_path):
    responses = [
        (json.dumps({"done": True}), "stop"),
        (json.dumps({"stage": [{"kind": "entity", "id": "p1", "name": "甲", "entity_type": "person"}, {"kind": "entity", "id": "p2", "name": "乙", "entity_type": "person"}], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"remove_stage_ids": ["p2"], "done": True}), "stop"),
        (json.dumps({"done": True}), "stop"),
        (json.dumps({"done": True}), "stop"),
    ]
    server, thread, url, requests = _server(responses)
    try:
        app, source_id = _app(tmp_path, url)
        state = _wait(app, app.start_narrative_analysis([source_id], api_key="test-secret"))
        assert state["status"] == "complete", state
        assert len(requests) == 5
        assert state["steps"][2]["removed_stage_ids"] == ["p2"]
        assert state["steps"][3]["phase"] == "correction" and "diagnostics" in state["steps"][3]
        assert [item["id"] for item in state["proposed_objects"]] == ["p1"]
        assert len(state["candidate_ids"]) == 1 and not app.entities()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_alternating_tool_decisions_stop_as_no_progress(tmp_path):
    first = json.dumps({"plan": "反复甲", "actions": [{"tool": "analysis_context", "args": {}}]})
    second = json.dumps({"plan": "反复乙", "actions": [{"tool": "classification_table", "args": {}}]})
    server, thread, url, requests = _server([(first if index % 2 == 0 else second, "stop") for index in range(6)])
    try:
        app, source_id = _app(tmp_path, url)
        state = _wait(app, app.start_narrative_analysis([source_id], budget={"research_rounds": 100}, api_key="test-secret"))
        assert state["status"] == "incomplete" and "周期性重复" in state["error"]
        assert len(requests) == 6 and state["rounds"] == 6
        assert not state["candidate_ids"] and not app.candidates()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_submit_research_returns_to_correction_and_rechecks_before_candidate(tmp_path):
    responses = [
        (json.dumps({"actions": [{"tool": "locate", "args": {"source_id": "SOURCE", "quote": "甲"}}], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"stage": [{"kind": "entity", "id": "p1", "name": "甲", "entity_type": "person", "evidence_refs": ["h1"]}], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"actions": [{"tool": "locate", "args": {"source_id": "SOURCE", "quote": "乙"}}, {"tool": "locate", "args": {"source_id": "SOURCE", "quote": "甲在渡口送乙离开。"}}], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"stage": [{"kind": "entity", "id": "p1", "name": "甲", "entity_type": "person", "evidence_refs": ["h3"]}], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"done": True}), "stop"),
    ]
    server, thread, url, requests = _server(responses)
    try:
        app, source_id = _app(tmp_path, url)
        with pytest.raises(ValueError, match="缺少 quotes"):
            _tool(app, [source_id], "locate_many", {"source_id": source_id, "queries": ["甲"]})
        for index in (0, 2):
            responses[index] = (responses[index][0].replace("SOURCE", source_id), "stop")
        state = _wait(app, app.start_narrative_analysis([source_id], budget={"research_rounds": 1}, api_key="test-secret"))
        assert state["status"] == "complete", state
        assert len(requests) == 5 and state["tool_count"] == 3
        assert state["steps"][2]["phase"] == "submit" and len(state["steps"][2]["tools"]) == 2
        assert "candidate_id" not in state["steps"][2]
        assert state["steps"][3]["phase"] == "correction" and "diagnostics" in state["steps"][3]
        assert state["steps"][4]["phase"] == "submit" and len(state["candidate_ids"]) == 1
        assert state["proposed_objects"][0]["evidence"] == [state["evidence_handles"]["h3"]]
        assert not app.entities()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_seeded_feedback_run_restores_handles_and_proposes_new_candidate(tmp_path):
    coverage = {key: "本段有据或无独立支线依据" for key in ("traits", "relations", "narrative_events", "participants", "main_storyline", "branch_storyline", "classification")}
    responses = [
        (json.dumps({"actions": [{"tool": "object_schema", "args": {"kinds": ["relation", "storyline", "classification"]}}], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"stage": [{"kind": "relation", "id": "r1", "subject_id": "p1", "object_id": "p2", "predicate": "送别", "evidence_refs": ["h1"]}], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"coverage": coverage, "done": True}, ensure_ascii=False), "stop"),
    ]
    server, thread, url, requests = _server(responses)
    try:
        app, source_id = _app(tmp_path, url)
        evidence_a = app.locate(source_id, "甲在渡口送乙离开。")[0].model_dump()
        evidence_b = app.locate(source_id, "乙")[0].model_dump()
        parent = app.propose_analysis_objects([
            {"kind": "entity", "id": "p1", "name": "甲", "entity_type": "person", "evidence": [evidence_a]},
            {"kind": "entity", "id": "p2", "name": "乙", "entity_type": "person", "evidence": [evidence_b]},
        ], app.project_info().revision, run_id="parent-run")
        original = app.review_candidate(parent)
        feedback = "独立审阅代理预审：补足甲乙关系证据；原候选尚未获人工确认。"
        run_id = app.start_narrative_analysis([source_id], budget={"research_rounds": 1}, task="按原文修订关系并说明覆盖缺口", feedback=feedback, feedback_origin="review_agent_guided", seed_candidate_id=parent, api_key="test-secret")
        state = _wait(app, run_id)
        assert state["status"] == "complete", state
        assert len(requests) == 3 and "独立审阅代理预审" in json.dumps(requests[0], ensure_ascii=False)
        assert "h1" in json.dumps(requests[0], ensure_ascii=False)
        assert state["lineage"]["parent_candidate_id"] == parent
        assert state["lineage"]["parent_run_id"] == "parent-run"
        assert state["feedback_origin"] == "review_agent_guided" and state["feedback"] == feedback
        assert state["workspace"]["p1"]["evidence_refs"] == ["h1"]
        assert state["evidence_handles"]["h1"] == evidence_a
        assert "diagnostics" in state["steps"][1] and not any(item["severity"] == "error" for item in state["steps"][1]["diagnostics"])
        child = state["candidate_ids"][0]
        assert child != parent and {item["id"] for item in app.candidates("pending")} == {parent, child}
        assert app.review_candidate(parent).version == original.version
        assert {item.id for item in app.review_candidate(child).change.operations} == {"p1", "p2", "r1"}
        assert not app.entities()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_seed_research_can_stage_remove_and_precheck_other_participant(tmp_path):
    coverage = {key: "本次仅修实体与事件，其余不适用" for key in ("traits", "relations", "narrative_events", "participants", "main_storyline", "branch_storyline", "classification")}
    responses = [
        (json.dumps({"plan": "核对叙事参与者合同", "actions": [{"tool": "object_schema", "args": {"kinds": ["entity", "event"]}}], "done": False}, ensure_ascii=False), "stop"),
        (json.dumps({"plan": "证据已在种子句柄，拆分并补狼参与者", "stage": [
            {"kind": "entity", "id": "p1", "name": "屠夫", "entity_type": "person", "origin": "original", "evidence_refs": ["h1"]},
            {"kind": "entity", "id": "w1", "name": "狼", "entity_type": "other", "origin": "original", "evidence_refs": ["h1"]},
            {"kind": "event", "id": "e1", "title": "屠夫杀狼", "action": "narrative", "origin": "original", "participants": [{"entity_id": "p1", "role": "行动者"}, {"entity_id": "w1", "role": "被杀的狼"}], "evidence_refs": ["h1"]},
        ], "remove_stage_ids": ["p0", "w0"], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"plan": "核对后提待审", "coverage": coverage, "done": True}, ensure_ascii=False), "stop"),
    ]
    server, thread, url, requests = _server(responses)
    try:
        app = Operations(tmp_path / "seed-stage.sqlite3")
        app.save_harness_settings({"base_url": url, "model": "Doubao-Seed-2.1-pro", "timeout_seconds": 15})
        source_id = app.import_text("短篇", "一屠遇狼，屠以刀斃狼。")
        evidence = app.locate(source_id, "一屠遇狼，屠以刀斃狼。")[0].model_dump()
        parent = app.propose_analysis_objects([
            {"kind": "entity", "id": "p0", "name": "屠夫统称", "entity_type": "person", "evidence": [evidence]},
            {"kind": "entity", "id": "w0", "name": "狼统称", "entity_type": "other", "evidence": [evidence]},
            {"kind": "event", "id": "e1", "title": "屠夫杀狼", "action": "narrative", "participants": [{"entity_id": "p0", "role": "行动者"}], "evidence": [evidence]},
        ], app.project_info().revision, run_id="parent-run")
        run_id = app.start_narrative_analysis([source_id], budget={"research_rounds": 3}, task="拆分实体并补狼参与者", feedback="独立预审：统称不可代替个体", feedback_origin="review_agent_guided", seed_candidate_id=parent, api_key="test-secret")
        state = _wait(app, run_id)
        assert state["status"] == "complete", state
        assert len(requests) == 3
        assert state["steps"][1]["phase"] == "research"
        assert state["steps"][1]["phase_transition"]["to"] == "correction"
        assert state["steps"][1]["removed_stage_ids"] == ["p0", "w0"]
        assert not any(item["severity"] == "error" for item in state["steps"][1]["diagnostics"])
        child = app.review_candidate(state["candidate_ids"][0])
        assert {item.id for item in child.change.operations} == {"p1", "w1", "e1"}
        assert {item.entity_id for item in next(item for item in child.change.operations if item.id == "e1").participants} == {"p1", "w1"}
        assert app.review_candidate(parent).version == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_paused_request_boundary_injects_instruction_without_interrupting_inflight(tmp_path):
    release = threading.Event()
    responses = [
        (json.dumps({"plan": "先读原文", "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"plan": "按补充要求核对", "stage": [{"kind": "entity", "id": "p1", "name": "甲", "entity_type": "person"}], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"plan": "公开确认", "done": True}, ensure_ascii=False), "stop"),
    ]
    server, thread, url, requests = _server(responses, first_release=release)
    try:
        app, source_id = _app(tmp_path, url)
        run_id = app.start_narrative_analysis([source_id], api_key="test-secret")
        deadline = time.monotonic() + 15
        while (len(requests) < 1 or app.narrative_analysis_status(run_id).get("request_state") != "in_flight") and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(requests) == 1
        app.pause_narrative_analysis(run_id)
        signal = app.send_narrative_instruction(run_id, "先核对甲的称呼")
        release.set()
        while app.narrative_analysis_status(run_id).get("request_state") != "paused_waiting" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(requests) == 1
        assert app.narrative_analysis_status(run_id)["request_state"] == "paused_waiting"
        app.resume_narrative_analysis(run_id)
        state = _wait(app, run_id)
        assert state["status"] == "complete", state
        assert len(requests) == 3
        assert "先核对甲的称呼" in json.dumps(requests[1], ensure_ascii=False)
        assert state["steps"][1]["instruction_ids"] == [signal["id"]]
        assert state["steps"][0]["raw"] and state["candidate_ids"]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_failed_extraction_is_observed_then_rebuilt_as_new_candidate(tmp_path):
    responses = [
        (json.dumps({"plan": "读取旧类型错误", "actions": [{"tool": "extraction_observation", "args": {"run_id": "OLD", "section": "entities"}}]}, ensure_ascii=False), "stop"),
        (json.dumps({"plan": "查当前字段", "actions": [{"tool": "object_schema", "args": {"kinds": ["entity"]}}]}, ensure_ascii=False), "stop"),
        (json.dumps({"plan": "定位原文", "actions": [{"tool": "locate", "args": {"source_id": "SOURCE", "quote": "狼"}}], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"plan": "重新提出候选", "stage": [{"kind": "entity", "id": "wolf", "name": "狼", "entity_type": "other", "evidence_refs": ["h1"]}], "done": True}, ensure_ascii=False), "stop"),
        (json.dumps({"plan": "确认待人工审核", "done": True}, ensure_ascii=False), "stop"),
    ]
    server, thread, url, requests = _server(responses)
    try:
        app = Operations(tmp_path / "handoff.sqlite3")
        app.save_harness_settings({"base_url": url, "model": "Doubao-Seed-2.1-pro", "timeout_seconds": 15})
        source_id = app.import_text("短章", "狼在山路上。")
        old_run = app.create_run(source_id)
        old_chunk = app.chunks(old_run)[0]["id"]
        old_raw = json.dumps({"entities": [{"ref": "w1", "name": "狼", "entity_type": "animal"}]}, ensure_ascii=False)
        with pytest.raises(ValueError, match="entity_type"):
            app.ingest_result(old_chunk, old_raw)
        responses[0] = (responses[0][0].replace("OLD", old_run), "stop")
        responses[2] = (responses[2][0].replace("SOURCE", source_id), "stop")
        run_id = app.start_narrative_from_extraction(old_run, api_key="test-secret")
        state = _wait(app, run_id)
        assert state["status"] == "complete", state
        assert len(requests) == 5 and state["rounds"] == 5
        assert "animal" in json.dumps(requests[1], ensure_ascii=False)
        assert "person" in json.dumps(requests[2], ensure_ascii=False)
        assert state["steps"][0]["tools"][0]["name"] == "extraction_observation"
        assert state["steps"][0]["tools"][0]["observation"]["result"]["entities"][0]["entity_type"] == "animal"
        assert not any(item["severity"] == "error" for item in state["steps"][3]["diagnostics"])
        assert app.chunks(old_run)[0]["raw"] == old_raw and app.runs()[0]["status"] == "failed"
        assert app.candidates("pending") and not app.entities()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_cancel_during_inflight_request_saves_response_without_next_round(tmp_path):
    release = threading.Event()
    response = json.dumps({"plan": "已读原文", "done": True}, ensure_ascii=False)
    server, thread, url, requests = _server([(response, "stop")], first_release=release)
    try:
        app, source_id = _app(tmp_path, url)
        run_id = app.start_narrative_analysis([source_id], api_key="test-secret")
        deadline = time.monotonic() + 15
        while len(requests) < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(requests) == 1
        app.cancel_narrative_analysis(run_id)
        assert app.narrative_analysis_control(run_id)["cancel_requested"]
        release.set()
        state = _wait(app, run_id)
        assert state["status"] == "cancelled" and state["request_state"] == "stopped"
        assert len(requests) == 1 and state["steps"][0]["raw"] == response
        assert not app.candidates()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_completed_malformed_coverage_becomes_explicit_correction_turn(tmp_path):
    malformed = json.dumps({
        "plan": "准备暂存甲",
        "stage": [{"kind": "entity", "id": "p1", "name": "甲", "entity_type": "person"}],
        "coverage": {"old_animal_entities": ["w1"]},
        "done": True,
    }, ensure_ascii=False)
    corrected = json.dumps({
        "plan": "把覆盖说明写成文字后再暂存甲",
        "stage": [{"kind": "entity", "id": "p1", "name": "甲", "entity_type": "person"}],
        "coverage": {"old_animal_entities": "旧提取类型不合合同，本次仅提出甲"},
        "done": True,
    }, ensure_ascii=False)
    responses = [
        (json.dumps({"done": True}), "stop"),
        (malformed, "stop"),
        (corrected, "stop"),
        (json.dumps({"done": True}), "stop"),
    ]
    server, thread, url, requests = _server(responses)
    try:
        app, source_id = _app(tmp_path, url)
        state = _wait(app, app.start_narrative_analysis([source_id], api_key="test-secret"))
        assert state["status"] == "complete", state
        assert len(requests) == 4 and state["rounds"] == 4
        assert state["steps"][1]["raw"] == malformed
        assert "old_animal_entities" in state["steps"][1]["action_diagnostics"][0]["message"]
        assert not state["steps"][1].get("stage_ids")
        assert "coverage 字段值须为文字" in json.dumps(requests[2], ensure_ascii=False)
        assert state["steps"][2]["phase"] == "correction" and state["workspace"]["p1"]["name"] == "甲"
        assert app.candidates("pending") and not app.entities()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_external_status_observer_never_rewrites_running_journal(tmp_path):
    app = Operations(tmp_path / "observer.sqlite3")
    run_id = uuid4().hex
    project = Path(app.project_info().path)
    journal = project.parent / f"{project.stem}_analysis_runs" / run_id / "journal.json"
    journal.parent.mkdir(parents=True)
    original = json.dumps({"id": run_id, "status": "running", "request_state": "in_flight"})
    journal.write_text(original, encoding="utf-8")
    observed = app.narrative_analysis_status(run_id)
    assert observed["status"] == "running" and observed["observer_state"] == "unverified_external"
    assert observed["worker_local"] is False
    assert journal.read_text(encoding="utf-8") == original


def test_repeated_malformed_action_stops_without_unbounded_requests(tmp_path):
    malformed = json.dumps({"coverage": {"bad_field": ["must be text"]}, "done": True})
    server, thread, url, requests = _server([(malformed, "stop")])
    try:
        app, source_id = _app(tmp_path, url)
        state = _wait(app, app.start_narrative_analysis([source_id], api_key="test-secret"))
        assert state["status"] == "incomplete" and "连续重复同一无效动作" in state["error"]
        assert len(requests) == 3 and state["rounds"] == 3
        assert all(step["raw"] == malformed for step in state["steps"])
        assert not app.candidates()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
