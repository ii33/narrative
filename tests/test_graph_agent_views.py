"""The agent's structural query tool keeps the Operations review boundary."""

import pytest

from narrative import graph_agent
from narrative.operations import Operations


def test_graph_view_agent_cannot_read_another_runs_pending_candidate(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "agent-view.sqlite3")
    revision = ops.graph_project_info()["revision"]
    own = ops.graph_propose(
        [{"kind": "node", "id": "p1", "node_type": "person", "name": "林岚"}],
        revision,
        source="model",
    )
    unrelated = ops.graph_propose(
        [{"kind": "node", "id": "p2", "node_type": "person", "name": "许舟"}],
        revision,
        source="model",
    )
    state = {"candidate_ids": [own]}
    with pytest.raises(ValueError, match="不在本次分析范围"):
        graph_agent._tool(ops, [], "graph_view", {"candidate_id": unrelated}, state)
    reviewed = graph_agent._tool(ops, [], "graph_view", {"view": "graph"}, state)
    assert reviewed["nodes"] == []
    result = graph_agent._tool(
        ops, [], "graph_view", {"candidate_id": own, "focus_id": "p1", "node_types": []}, state,
    )
    assert [node["id"] for node in result["nodes"]] == ["p1"]
    assert result["focus"]["matches_filter"] is False
    assert ops.graph_candidate(own)["status"] == "pending"
    assert ops.graph_project_info()["revision"] == revision


def test_graph_view_agent_can_inspect_seed_but_cannot_supply_untyped_fields(tmp_path):
    ops = Operations.create_graph_project(tmp_path / "seed-view.sqlite3")
    revision = ops.graph_project_info()["revision"]
    seed = ops.graph_propose(
        [{"kind": "node", "id": "event", "node_type": "event", "name": "城门相遇"}],
        revision,
        source="model",
    )
    state = {"candidate_ids": [], "lineage": {"parent_candidate_id": seed}}
    result = graph_agent._tool(ops, [], "graph_view", {"candidate_id": seed, "view": "forest"}, state)
    assert result["view"] == "forest"
    assert any(node["id"] == "event" for node in result["nodes"])
    with pytest.raises(ValueError):
        graph_agent._tool(ops, [], "graph_view", {"candidate_id": seed, "sql": "SELECT *"}, state)
    with pytest.raises(ValueError):
        graph_agent._tool(ops, [], "graph_view", {"depth": True}, state)
