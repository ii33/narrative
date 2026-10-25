"""Protocol regressions for the v5 graph agent; no cloud calls."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from narrative import graph_agent
from narrative.graph_operations import GraphOperations
from narrative.harness import HarnessReply


def _reply(*, actions=None, stage=None, coverage=None) -> str:
    return json.dumps(
        {
            "plan": "公开的下一步",
            "actions": actions or [],
            "stage": stage or [],
            "remove_stage_ids": [],
            "done": False,
            "coverage": coverage or {},
        },
        ensure_ascii=False,
    )


def _action(name: str) -> str:
    return _reply(actions=[{"tool": name, "args": {}}])


def _objects() -> list[dict]:
    """A valid v5 batch of ten linked objects: two event claims over two events."""
    return [
        {"kind": "node", "id": "w1", "node_type": "other", "name": "狼"},
        {"kind": "node", "id": "e1", "node_type": "event", "name": "屠夫遇狼"},
        {"kind": "node", "id": "e2", "node_type": "event", "name": "狼离开"},
        {"kind": "predicate", "id": "before", "label": "先于", "semantic": "temporal", "temporal_relation": "before", "subject_types": ["event"], "object_types": ["event"], "value_type": "node"},
        {"kind": "predicate", "id": "contains_event", "label": "包含事件", "semantic": "containment", "subject_types": ["event"], "object_types": ["event"], "value_type": "node"},
        {"kind": "evidence", "id": "ev1", "evidence_ref": "h1"},
        {"kind": "assertion", "id": "a1", "subject_id": "e1", "predicate_id": "before", "object_id": "e2", "temporal_left": {"timeline_id": "story", "form": "event_boundary", "event_id": "e1", "boundary": "end"}, "temporal_right": {"timeline_id": "story", "form": "event_boundary", "event_id": "e2", "boundary": "start"}},
        {"kind": "assertion", "id": "a2", "subject_id": "e1", "predicate_id": "contains_event", "object_id": "e2"},
        {"kind": "evidence_link", "id": "l1", "assertion_id": "a1", "evidence_id": "ev1"},
        {"kind": "evidence_link", "id": "l2", "assertion_id": "a2", "evidence_id": "ev1"},
    ]


def _base_nodes() -> list[dict]:
    """Reviewed nodes accepted before a run so property records can reference them."""
    return [
        {"kind": "node", "id": "actor", "node_type": "person", "name": "屠夫"},
        {"kind": "node", "id": "mark", "node_type": "event", "name": "遇狼"},
    ]


def _property_records() -> list[dict]:
    """One v5 property batch: a definition, an initial state, and an event change."""
    return [
        {"kind": "property_definition", "id": "state", "value_type": "string", "cardinality": "one", "persistence": "until_changed", "owner_node_types": ["person"]},
        {"kind": "property_value", "id": "mark-state", "node_id": "actor", "property_id": "state", "value": "安然", "event_id": "mark"},
    ]


def _scripted(tmp_path: Path, monkeypatch, responses, *, before_reply=None, budget=None, base_nodes=None):
    ops = GraphOperations(tmp_path / "graph.sqlite3", create=True)
    source_id = ops.import_source("短篇", "屠夫遇见狼。")
    if base_nodes:
        revision = ops.graph_project_info()["revision"]
        ops.graph_accept(ops.graph_propose(base_nodes, revision, source="manual"), 1)
    calls = []

    class FakeRunner:
        def __init__(self, settings, api_key, directory):
            pass

        def agent_turn(self, instruction, session_id):
            index = len(calls)
            calls.append((instruction, session_id))
            if before_reply is not None:
                before_reply(index, ops)
            if index >= len(responses):
                raise AssertionError("代理未在有界脚本内终止")
            return HarnessReply(
                responses[index],
                {"finish_reason": "completed", "usage": [{"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}]},
            )

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
    run_id = ops.start_graph_analysis([source_id], task="只记录有证据的遇狼事件", budget=budget)
    for _ in range(120):
        state = ops.graph_analysis_status(run_id)
        if state["status"] not in {"queued", "running"}:
            return ops, state, calls
        time.sleep(0.025)
    pytest.fail("本地假模型任务未在合理时间内终止")


def test_explicit_precheck_and_propose_after_unbounded_stage(tmp_path: Path, monkeypatch):
    records = _property_records()
    responses = [
        _reply(),  # replaced with locate after the isolated source is created
        _reply(stage=_objects()),
        _reply(actions=[{"tool": "property_precheck", "args": {"records": records}}]),
        _reply(actions=[{"tool": "property_propose", "args": {"records": records}}]),
        _action("graph_precheck"),
        _action("graph_propose"),
    ]
    def source_for_script(index, graph_ops):
        if index == 0:
            responses[0] = _reply(actions=[{"tool": "locate", "args": {"source_id": graph_ops.sources()[0]["id"], "quote": "屠夫遇见狼。"}}])

    graph_ops, state, calls = _scripted(tmp_path, monkeypatch, responses, before_reply=source_for_script, base_nodes=_base_nodes())
    assert state["status"] == "complete", state.get("error")
    assert len(calls) == 6
    assert len({session for _, session in calls}) == 1
    assert len(state["workspace"]) == 10
    assert state["steps"][2]["tools"][0]["result"]["passed"] is True
    assert state["steps"][3]["tools"][0]["result"]["status"] == "pending"
    assert state["steps"][4]["tools"][0]["result"]["status"] == "passed"
    assert state["steps"][5]["tools"][0]["result"]["status"] == "pending"
    candidate_id = state["candidate_ids"][0]
    candidate = graph_ops.graph_candidate(candidate_id)
    assert candidate["status"] == "pending"
    assert len(candidate["objects"]) == 10
    assert graph_ops.graph_snapshot()["assertions"] == []
    pending_properties = graph_ops.property_candidates("pending")
    assert len(pending_properties) == 1
    assert pending_properties[0]["records"] == records
    assert graph_ops.property_candidates("accepted") == []


@pytest.mark.parametrize("mode", ["invalid_tool", "empty_wait"])
def test_semantic_stall_ignores_coverage_churn(tmp_path: Path, monkeypatch, mode: str):
    responses = [
        _reply(
            actions=[{"tool": "not_a_graph_tool", "args": {}}] if mode == "invalid_tool" else [],
            coverage={"claimed_progress": f"new words {index}"},
        )
        for index in range(20)
    ]
    ops, state, calls = _scripted(tmp_path, monkeypatch, responses)
    assert state["status"] == "incomplete"
    assert 1 <= len(calls) <= 8
    assert state["candidate_ids"] == []
    assert ops.graph_candidates("pending") == []
    assert "停滞" in str(state.get("error")) or "无进展" in str(state.get("error"))


def test_stage_change_invalidates_precheck_until_rechecked(tmp_path: Path, monkeypatch):
    responses = [
        _reply(),  # replaced with locate by callback
        _reply(stage=_objects()),
        _action("graph_precheck"),
        _reply(stage=[{"kind": "node", "id": "p2", "node_type": "person", "name": "旁观者"}]),
        _action("graph_propose"),
        _action("graph_precheck"),
        _action("graph_propose"),
    ]

    def initial_locate(index, ops):
        if index == 0:
            responses[0] = _reply(actions=[{"tool": "locate", "args": {"source_id": ops.sources()[0]["id"], "quote": "屠夫遇见狼。"}}])

    ops, state, _ = _scripted(tmp_path, monkeypatch, responses, before_reply=initial_locate)
    assert state["status"] == "complete", state.get("error")
    assert state["steps"][4]["tools"][0]["result"].get("error")
    assert state["steps"][5]["tools"][0]["result"]["status"] == "passed"
    assert len(state["candidate_ids"]) == 1
    assert len(ops.graph_candidate(state["candidate_ids"][0])["objects"]) == 11


def test_revision_change_invalidates_precheck_until_rechecked(tmp_path: Path, monkeypatch):
    responses = [
        _reply(),
        _reply(stage=_objects()),
        _action("graph_precheck"),
        _action("graph_propose"),
        _action("graph_precheck"),
        _action("graph_propose"),
    ]

    def mutate_project(index, ops):
        if index == 0:
            responses[0] = _reply(actions=[{"tool": "locate", "args": {"source_id": ops.sources()[0]["id"], "quote": "屠夫遇见狼。"}}])
        if index == 3:
            ops.import_source("后加原文", "另一篇独立的原文。")

    ops, state, _ = _scripted(tmp_path, monkeypatch, responses, before_reply=mutate_project)
    assert state["status"] == "complete", state.get("error")
    assert state["steps"][3]["tools"][0]["result"].get("error")
    assert state["steps"][4]["tools"][0]["result"]["status"] == "passed"
    assert len(state["candidate_ids"]) == 1
    candidate = ops.graph_candidate(state["candidate_ids"][0])
    assert candidate["expected_revision"] == ops.graph_project_info()["revision"]


@pytest.mark.parametrize(
    ("plan", "coverage", "include_coverage"),
    [
        ("先查字段合同", "已核来源", True),
        (["先查字段合同", "再核原文"], ["人物", "事件"], True),
        (["核证据"], {"人物": {"已覆盖": ["屠夫"]}}, True),
        ("未填写覆盖", None, False),
    ],
    ids=["coverage_text", "plan_and_coverage_lists", "nested_coverage", "coverage_omitted"],
)
def test_display_metadata_shapes_do_not_block_valid_tool(
    tmp_path: Path, monkeypatch, plan, coverage, include_coverage: bool
):
    first = {"plan": plan, "actions": [{"tool": "graph_schema", "args": {}}], "stage": [], "remove_stage_ids": []}
    if include_coverage:
        first["coverage"] = coverage
    raw = json.dumps(first, ensure_ascii=False)
    responses = [
        raw,
        _reply(stage=[{"kind": "node", "id": "p1", "node_type": "person", "name": "屠夫"}]),
        _action("graph_precheck"),
        _action("graph_propose"),
    ]
    ops, state, calls = _scripted(tmp_path, monkeypatch, responses)
    assert state["status"] == "complete", state.get("error")
    assert len(calls) == 4
    assert state["steps"][0]["raw"] == raw  # Preserve actual model text, not a repaired copy.
    assert isinstance(state["steps"][0]["plan"], str)
    assert isinstance(state["coverage"], dict)
    assert all(isinstance(key, str) and isinstance(value, str) for key, value in state["coverage"].items())
    tool = state["steps"][0]["tools"][0]
    assert tool["tool"] == "graph_schema"
    assert "error" not in tool["result"]
    assert len(ops.graph_candidates("pending")) == 1


def test_display_tolerance_does_not_repair_tool_args_or_business_object(tmp_path: Path, monkeypatch):
    invalid_tool = json.dumps(
        {"plan": ["查看合同"], "coverage": "检查中", "actions": [{"tool": "graph_schema", "args": {"unexpected": True}}]},
        ensure_ascii=False,
    )
    invalid_object = _reply(stage=[{"kind": "node", "id": "wolf", "node_type": "animal", "name": "狼"}])
    responses = [invalid_tool, invalid_object, _action("graph_precheck"), _action("graph_propose")]
    responses.extend(_reply(coverage={"字样": str(index)}) for index in range(8))
    ops, state, _ = _scripted(tmp_path, monkeypatch, responses)
    assert state["status"] == "incomplete"
    assert state["steps"][0]["raw"] == invalid_tool
    assert "error" in state["steps"][0]["tools"][0]["result"]
    assert state["steps"][1]["raw"] == invalid_object
    assert state["steps"][1]["stage_diagnostics"]
    assert state["steps"][2]["tools"][0]["result"]["status"] == "failed"
    assert "error" in state["steps"][3]["tools"][0]["result"]
    assert ops.graph_candidates("pending") == []


@pytest.mark.parametrize("tool", ["graph_hierarchy", "graph_timeline"])
def test_hierarchy_tool_limits_candidate_to_current_run(tool):
    class ReadOnlyOperations:
        def graph_hierarchy(self, **kwargs):
            return {"requested": kwargs, "roots": []}

        graph_timeline = graph_hierarchy

    state = {"candidate_ids": ["current"], "lineage": {"parent_candidate_id": "seed"}}
    ops = ReadOnlyOperations()
    current = graph_agent._tool(ops, ["source"], tool, {"candidate_id": "current"}, state)
    assert current["requested"] == {"candidate_id": "current"}
    seed = graph_agent._tool(ops, ["source"], tool, {"candidate_id": "seed"}, state)
    assert seed["roots"] == []
    with pytest.raises(ValueError, match="不在本次分析范围"):
        graph_agent._tool(ops, ["source"], tool, {"candidate_id": "unrelated"}, state)


def test_timeline_tool_is_read_only_and_rejects_unknown_arguments():
    capability = next(item for item in graph_agent.capabilities()["tools"] if item["name"] == "graph_timeline")
    assert capability["access"] == "read_only"
    with pytest.raises(ValueError, match="graph_timeline 参数无效"):
        graph_agent._tool(object(), [], "graph_timeline", {"write": True}, {"candidate_ids": []})

def test_incremental_event_index_bridge_preserves_reading_cut():
    class IndexOps:
        def graph_event_index(self, **kwargs):
            self.received = kwargs
            return {"events": [{"id": "night", "parent_edge_id": "old-parent"}], "truncated": False}

    ops = IndexOps()
    args = {"context_id": "canon", "disclosure_cut": {"source_id": "s", "start": 12}, "as_of_revision": 2, "focus_id": "night", "limit": 4}
    result = graph_agent._tool(ops, [], "graph_event_index", args, {})
    assert ops.received == args
    assert result["events"][0]["parent_edge_id"] == "old-parent"
    with pytest.raises(ValueError, match="参数无效"):
        graph_agent._tool(ops, [], "graph_event_index", {"candidate_id": "unscoped"}, {})


def test_incremental_prompt_contract_keeps_identity_and_review_gate():
    from narrative.agent_runtime import Budget

    prompt = graph_agent._prompt([], 0, Budget())
    assert graph_agent.PROMPT_VERSION == "graph-agent/11"
    for required in ("graph_event_index", "parent_edge_id", "supersedes_id", "source_order", "disclosure_cut", "既有事件保持原ID", "canon语境与story时间线由系统预建", "graph_precheck", "graph_propose", "property_precheck", "property_propose", "不能自行接受", "PENDING_GRAPH_REVIEW", "两阶段"):
        assert required in prompt
    assert "graph_event_index" in graph_agent.TOOL_CATALOG
    assert "property_propose" in graph_agent.TOOL_CATALOG


def test_actual_budget_is_visible_on_every_request_without_bypassing_review(tmp_path, monkeypatch):
    responses = [_reply(), _reply(stage=_objects()), _action("graph_precheck"), _action("graph_propose")]

    def locate_first(index, ops):
        if index == 0:
            responses[0] = _reply(actions=[{"tool": "locate", "args": {"source_id": ops.sources()[0]["id"], "quote": "屠夫遇见狼。"}}])

    budget = {"research_rounds": 1, "max_rounds": 4, "max_total_tokens": 100, "wall_seconds": 60, "request_seconds": 30}
    ops, state, calls = _scripted(tmp_path, monkeypatch, responses, before_reply=locate_first, budget=budget)
    assert state["status"] == "complete", state.get("error")
    assert "本次实际任务预算" in calls[0][0]
    assert '"max_rounds": 4' in calls[0][0]
    assert '"wall_seconds": 60' in calls[0][0]
    for index, (instruction, _) in enumerate(calls):
        progress = json.loads(instruction.rsplit("\n本次请求预算：", 1)[1])
        assert progress["request_number"] == index + 1
        assert progress["completed_model_requests"] == index
        assert progress["remaining_model_requests_including_this"] == 4 - index
        assert progress["used_total_tokens"] == index * 4
        assert progress["remaining_total_tokens"] == 100 - index * 4
        assert progress["usage_incomplete"] is False
        assert 0 <= progress["remaining_wall_seconds"] <= 60
        assert state["steps"][index]["request_budget"] == progress
        assert "预算不授权缩小原文范围或跳过审核" in instruction
    assert [step["tools"][0]["tool"] for step in state["steps"] if step["tools"]][-2:] == ["graph_precheck", "graph_propose"]
    assert ops.graph_candidate(state["candidate_ids"][0])["status"] == "pending"
    assert ops.graph_snapshot()["assertions"] == []


def test_budget_visibility_does_not_add_requests_or_submit_without_precheck(tmp_path, monkeypatch):
    responses = [_reply(), _reply(stage=_objects()), _action("graph_precheck"), _action("graph_propose")]

    def locate_first(index, ops):
        if index == 0:
            responses[0] = _reply(actions=[{"tool": "locate", "args": {"source_id": ops.sources()[0]["id"], "quote": "屠夫遇见狼。"}}])

    ops, state, calls = _scripted(
        tmp_path, monkeypatch, responses, before_reply=locate_first,
        budget={"max_rounds": 3},
    )
    assert state["status"] == "incomplete"
    assert "模型轮数上限" in state["error"]
    assert len(calls) == 3 and state["candidate_ids"] == []
    assert ops.graph_candidates() == []
    last = state["steps"][-1]["request_budget"]
    assert last["remaining_model_requests_including_this"] == 1
    assert last["remaining_total_tokens"] is None
    assert last["remaining_wall_seconds"] is None
