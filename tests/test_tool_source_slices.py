"""Agent-created slices share the Operations surface used by the UI."""
from pathlib import Path

import pytest

from narrative import tool_api
from narrative.operations import Operations
from narrative.project_catalog import create_graph_project


@pytest.fixture()
def source(tmp_path: Path):
    path = tmp_path / "slices.nproj"
    ops = create_graph_project(path)
    text = "序😀言\n第一章\n甲进门。\n第二章\n乙离开。"
    source_id = ops.import_source("原文", text)
    return path, source_id, text


def test_tool_batch_is_persistent_and_atomic(source):
    path, source_id, text = source
    cut = text.index("第二章")
    args = {"source_id": source_id, "slices": [
        {"title": "前半", "start": 0, "end": cut},
        {"title": "后半", "start": cut, "end": len(text)},
    ]}
    created = tool_api.call("create_source_slices", args, project=path)
    assert created["status"] == "created"
    listed = tool_api.call("list_source_slices", {"source_id": source_id}, project=path)
    assert len(listed["slices"]) == 2
    preview = tool_api.call("read_source_slice", {
        "source_id": source_id, "slice_id": listed["slices"][0]["id"],
    }, project=path)
    assert preview["text"] == text[:cut]
    assert Operations.open(path).source(source_id)["text"] == text
    with pytest.raises(tool_api.ToolError):
        tool_api.call("create_source_slices", {"source_id": source_id, "slices": [
            {"title": "合法", "start": 1, "end": 3},
            {"title": "越界", "start": 3, "end": len(text) + 1},
        ]}, project=path)
    after = tool_api.call("list_source_slices", {"source_id": source_id}, project=path)
    assert after == listed


@pytest.mark.parametrize("slices", [
    [], {}, [None], [{"title": "bad", "start": True, "end": 3}],
    [{"title": "bad", "start": 1.0, "end": 3}],
    [{"title": "", "start": 0, "end": 3}],
    [{"title": "bad", "start": 0, "end": 3, "text": "replacement"}],
])
def test_invalid_batch_does_not_create_any_slice(source, slices):
    path, source_id, _text = source
    with pytest.raises(tool_api.ToolError):
        tool_api.call("create_source_slices", {"source_id": source_id, "slices": slices}, project=path)
    assert tool_api.call("list_source_slices", {"source_id": source_id}, project=path)["slices"] == []


def test_batch_schema_explains_coordinate_system():
    schema = tool_api.TOOLS["create_source_slices"].schema()
    batch = schema["properties"]["slices"]
    assert batch["items"]["additionalProperties"] is False
    assert set(batch["items"]["required"]) == {"title", "start", "end"}
    assert "Unicode codepoint" in batch["description"]
    assert tool_api.TOOLS["create_source_slices"].writes is True
