import json
from uuid import uuid4

import pytest

from narrative.operations import Operations


def _journal(app, run_id, state):
    project = app.project_info().path
    from pathlib import Path

    path = Path(project).parent / f"{Path(project).stem}_analysis_runs" / run_id / "journal.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def test_control_signals_persist_and_instructions_are_taken_once(tmp_path, monkeypatch):
    app = Operations(tmp_path / "control.sqlite3")
    run_id = uuid4().hex
    _journal(app, run_id, {"id": run_id, "status": "running"})
    monkeypatch.setattr(app, "narrative_analysis_status", lambda _id: {"status": "running"})
    assert app.narrative_analysis_control(run_id)["paused"] is False
    assert app.pause_narrative_analysis(run_id)["paused"] is True
    first = app.send_narrative_instruction(run_id, "补查原文再提交")
    second = app.send_narrative_instruction(run_id, "说明覆盖缺口")
    assert (first["seq"], second["seq"]) == (1, 2)
    assert app.narrative_analysis_control(run_id)["pending_count"] == 2
    taken = app.take_narrative_instructions(run_id)
    assert [item["text"] for item in taken] == ["补查原文再提交", "说明覆盖缺口"]
    assert app.take_narrative_instructions(run_id) == []
    assert app.resume_narrative_analysis(run_id)["paused"] is False
    assert app.narrative_analysis_control(run_id)["pending_count"] == 0
    assert app.cancel_narrative_analysis(run_id) is None
    assert app.narrative_analysis_control(run_id)["cancel_requested"] is True
    monkeypatch.setattr(app, "narrative_analysis_status", lambda _id: {"status": "running", "worker_local": False})
    monkeypatch.setattr("narrative.narrative_agent.cancel", lambda *_args: pytest.fail("跨进程取消不得调用本地执行器"))
    assert app.cancel_narrative_analysis(run_id) is None
    monkeypatch.setattr(app, "narrative_analysis_status", lambda _id: {"status": "complete"})
    with pytest.raises(ValueError, match="已结束"):
        app.pause_narrative_analysis(run_id)


def test_external_process_observation_is_read_only(tmp_path):
    app = Operations(tmp_path / "external.sqlite3")
    run_id = uuid4().hex
    state = {"id": run_id, "status": "running", "phase": "research", "source_ids": [], "steps": []}
    _journal(app, run_id, state)
    from pathlib import Path

    project = Path(app.project_info().path)
    path = project.parent / f"{project.stem}_analysis_runs" / run_id / "journal.json"
    before = path.read_bytes()
    trace = app.narrative_analysis_trace(run_id)
    assert trace["status"] == "running"
    assert trace["observer_state"] == "unverified_external"
    assert trace["worker_local"] is False
    assert path.read_bytes() == before


def test_trace_uses_recorded_provider_model_and_summarizes_tools(tmp_path):
    app = Operations(tmp_path / "trace.sqlite3")
    original_profile = app.harness_settings()
    assert original_profile["max_tokens"] == 131072
    app.save_harness_settings(original_profile)
    assert Operations(tmp_path / "trace.sqlite3").harness_settings()["max_tokens"] == 131072
    assert app.save_harness_settings({**original_profile, "max_tokens": 256000})["max_tokens"] == 256000
    assert app.harness_settings()["max_tokens"] == 256000
    source_id = app.import_text("短章", "甲把书送给乙。")
    run_id = uuid4().hex
    _journal(app, run_id, {
        "id": run_id, "status": "complete", "phase": "submit", "provider": "ark", "model": "recorded-model",
        "prompt_version": "narrative-agent/test", "source_ids": [source_id], "rounds": 1, "tool_count": 2,
        "budget": {"max_tokens_per_round": "service-default"},
        "config": {"max_tokens": None, "token_parameter": "max_tokens"},
        "usage": {"total_tokens": 123}, "workspace": {
            "p1": {"kind": "entity", "id": "p1", "name": "甲"},
            "p2": {"kind": "entity", "id": "p2", "name": "乙"},
            "r1": {"kind": "relation", "id": "r1", "subject_id": "p1", "predicate": "赠予", "object_id": "p2"},
        },
        "precheck_diagnostics": [{"object_id": "r1", "severity": "warning", "message": "测试"}], "candidate_ids": [], "steps": [{
            "round": 1, "phase": "research", "plan": "先看原文", "finish_reason": "completed",
            "raw": "{\"plan\":\"先看原文\"}", "usage": [{"total_tokens": 123}],
            "action_diagnostics": [{"severity": "error", "code": "action_format", "message": "coverage 字段须为字符串"}],
            "stage_ids": ["p1"], "tools": [
                {"name": "source_excerpt", "args": {"source_id": source_id, "start": 0, "end": 100},
                 "observation": {"result": {"text": "甲把书送给乙。" * 100}}},
                {"name": "locate", "args": {"source_id": source_id, "quote": "甲"},
                 "observation": {"result": [{"handle": "h1", "quote": "甲"}]}},
                {"name": "extraction_observation", "args": {"run_id": "old"},
                 "observation": {"result": {
                     "raw": "{\"secret\":\"do not show\"}", "raw_characters": 24,
                     "raw_sha256": "hash", "entities": [{"entity_type": "animal"}],
                     "diagnostics": [{"path": "$.entities[0].entity_type", "code": "literal_error", "message": "非法类型"}],
                 }}},
            ],
        }],
    })
    trace = app.narrative_analysis_trace(run_id)
    assert trace["provider"] == "ark" and trace["model"] == "recorded-model"
    assert trace["output_limit_mode"] == "service-default"
    assert trace["output_token_value"] is None and trace["output_parameter_sent"] is False
    assert trace["current_service"]["max_tokens"] == 256000
    assert trace["source_labels"] == ["短章"]
    assert trace["steps"][0]["plan"] == "先看原文"
    assert trace["steps"][0]["diagnostics"][0]["code"] == "action_format"
    assert trace["steps"][0]["tools"][0]["args"]["source_id"] == "短章"
    assert trace["steps"][0]["tools"][0]["result"]["summary"].startswith("原文摘录")
    assert len(trace["steps"][0]["tools"][0]["result"]["preview"]) < 200
    assert trace["steps"][0]["tools"][1]["result"]["matches"][0]["handle"] == "h1"
    assert trace["steps"][0]["tools"][2]["result"]["entity_types"] == ["animal"]
    assert "do not show" not in str(trace)
    assert trace["steps"][0]["staged"][0]["label"] == "甲"
    assert trace["precheck_diagnostics"][0]["object_label"] == "甲 → 赠予 → 乙"
    assert "raw" not in trace["steps"][0]
    assert trace["capabilities"]["native_tools"] is False
    assert any(tool["name"] == "locate" for tool in trace["capabilities"]["tools"])
    response = app.narrative_analysis_response(run_id, 1)
    assert response["raw"] == '{"plan":"先看原文"}'
    assert response["model"] == "recorded-model"
    assert "config" not in response
    with pytest.raises(ValueError, match="轮次不存在"):
        app.narrative_analysis_response(run_id, 2)
    assert app.save_harness_settings({**app.harness_settings(), "max_tokens": None})["max_tokens"] is None
    assert Operations(tmp_path / "trace.sqlite3").harness_settings()["max_tokens"] is None


def test_extraction_handoff_keeps_invalid_raw_and_diagnostics(tmp_path, monkeypatch):
    app = Operations(tmp_path / "handoff.sqlite3")
    source_id = app.import_text("寓言", "狼在草地上。")
    old_run_id = app.create_run(source_id, size=100, overlap=0)
    old_chunk = app.chunks(old_run_id)[0]
    raw = '{"entities":[{"name":"狼","entity_type":"animal"}]}'
    with app._store.connect(write=True) as db:
        db.execute("UPDATE runs SET status='failed' WHERE id=?", (old_run_id,))
        db.execute(
            "UPDATE chunks SET status='failed', raw=?, error=? WHERE id=?",
            (raw, "模型字段错误", old_chunk["id"]),
        )
    handoff = app.narrative_extraction_handoff(old_run_id)
    assert handoff["source_id"] == source_id
    assert handoff["source_hash"] == app.source(source_id)["hash"]
    assert handoff["chunks"][0]["raw"] == raw
    assert handoff["chunks"][0]["diagnostics"]
    retrieved = app.extraction_raw_response(old_run_id, old_chunk["id"])
    assert retrieved["raw"] == raw
    assert retrieved["diagnostics"] == handoff["chunks"][0]["diagnostics"]
    with pytest.raises(ValueError, match="不属于"):
        app.extraction_raw_response(old_run_id, "some-other-chunk")
    captured = {}

    def fake_start(_operations, source_ids, **kwargs):
        captured.update({"source_ids": source_ids, **kwargs})
        return "new-agent-run"

    monkeypatch.setattr("narrative.narrative_agent.start", fake_start)
    assert app.start_narrative_from_extraction(old_run_id) == "new-agent-run"
    assert captured["extraction_handoff"] == handoff
    assert captured["source_ids"] == [source_id]


def test_trace_uses_recorded_feedback_and_parent_labels_for_removed_seed(tmp_path):
    app = Operations(tmp_path / "feedback.sqlite3")
    source_id = app.import_text("短章", "甲和乙。")
    evidence = app.locate(source_id, "甲和乙")[0].model_dump(mode="json")
    revision = app.project_info().revision
    parent_id = app.propose_analysis_objects([
        {"kind": "entity", "id": "old", "name": "旧组合", "entity_type": "person", "evidence": [evidence]},
        {"kind": "entity", "id": "revised", "name": "原名", "entity_type": "person", "evidence": [evidence]},
    ], revision)
    child_id = app.propose_analysis_objects([
        {"kind": "entity", "id": "revised", "name": "新名", "entity_type": "person", "evidence": [evidence]},
    ], revision)
    run_id = uuid4().hex
    _journal(app, run_id, {
        "id": run_id, "status": "complete", "source_ids": [source_id],
        "task": "修订", "feedback": "拆开旧组合", "feedback_origin": "review_agent_guided",
        "lineage": {"parent_candidate_id": parent_id, "feedback_hash": "recorded-hash"},
        "workspace": {}, "candidate_ids": [child_id], "steps": [{
            "round": 1, "phase": "draft", "finish_reason": "completed",
            "removed_stage_ids": ["old"], "stage_ids": ["revised"],
        }],
    })
    trace = app.narrative_analysis_trace(run_id)
    assert trace["feedback"] == "拆开旧组合"
    assert trace["feedback_origin"] == "review_agent_guided"
    assert trace["feedback_hash"] == "recorded-hash"
    assert trace["steps"][0]["removed"][0]["label"] == "旧组合"
    assert trace["steps"][0]["staged"][0]["label"] == "新名"
