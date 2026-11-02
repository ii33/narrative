"""Current-state public tools must not expose the removed revision ledger."""
import pytest

from narrative import tool_api
from narrative.graph_views import normalize_spec


def test_removed_revision_and_retraction_protocol_is_rejected():
    assert "retract_properties" not in tool_api.TOOLS
    assert "as_of_revision" not in tool_api.TOOLS["read_project_state"].params
    with pytest.raises(ValueError):
        normalize_spec({"as_of_revision": 1})
    with pytest.raises(tool_api.ToolError, match="不认识的参数"):
        tool_api.call("read_project_state", {"as_of_revision": 1})


def test_interval_correction_and_rollback_tools_use_operations():
    calls = []
    class Ops:
        def __getattr__(self, name):
            def invoke(*args, **kwargs):
                calls.append((name, args, kwargs))
                return {"revision": 7}
            return invoke
    ops = Ops()
    result = tool_api.TOOLS["correct_property"].handler(ops, {
        "record_id": "v1", "value": "正确值", "reason": "录入错误", "quote": "原文",
    })
    assert result["status"] == "accepted"
    assert calls[-1] == ("correct_property", ("v1", "正确值", "录入错误"), {"quote": "原文"})
    tool_api.TOOLS["expire_properties"].handler(ops, {
        "record_ids": ["v1"], "source_id": "s1", "start": 8, "reason": "状态改变",
    })
    assert calls[-1] == ("expire_properties", (["v1"], "s1", 8), {"event_id": None, "reason": "状态改变"})
    tool_api.TOOLS["rollback_to"].handler(ops, {
        "source_id": "s1", "start": 8, "expected_revision": 6, "reason": "回退",
    })
    assert calls[-1] == ("rollback_to", ("s1", 8, 6, "回退"), {})
    assert tool_api.TOOLS["rollback_to"].writes
    assert not tool_api.TOOLS["rollback_preview"].writes
