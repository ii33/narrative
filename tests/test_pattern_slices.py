"""Pattern slicing computes whole batches without model-authored offsets."""
import time

import pytest
from agent_fixtures import project as project

from narrative import assistant, pattern_slices, tool_api
from narrative.project_package import layout

TEXT = "前言😀\r\n" + "".join(f"Chapter {i} title\r\n正文{i}😀\r\n" for i in range(1, 6))
PATTERN = r"^Chapter \d+ [^\r\n]+"


def test_three_chapters_use_fourth_heading_and_leave_prefix_explicit(project):
    sid = project.import_source("原文", TEXT)
    before = project.graph_snapshot()
    plan = project.preview_pattern_slices(sid, PATTERN, limit=3)
    assert project.source_slices(sid) == []
    assert project.graph_snapshot() == before
    assert plan["match_count"] == 5 and plan["selected_count"] == 3
    assert plan["prefix"] == {"start": 0, "end": TEXT.index("Chapter 1"), "policy": "excluded"}
    assert plan["slices"][2]["end"] == TEXT.index("Chapter 4")
    assert plan["slices"][0]["start"] == len("前言😀\r\n")
    created = project.create_pattern_slices(sid, PATTERN, limit=3)
    assert len(project.source_slices(sid)) == 3
    assert "source_hash" not in created and "plan_id" not in created
    again = project.create_pattern_slices(sid, PATTERN, limit=3)
    assert again["slices"] == created["slices"]
    selected = project.preview_pattern_slices(sid, PATTERN, start_index=3)
    assert [s["title"] for s in selected["slices"]] == ["Chapter 4 title", "Chapter 5 title"]
    assert selected["slices"][-1]["end"] == len(TEXT)


def test_creation_recomputes_selected_range_without_preview_tokens_and_is_atomic(project):
    sid = project.import_source("原文", TEXT)
    # An optional earlier preview does not constrain a later explicit selection.
    project.preview_pattern_slices(sid, PATTERN, limit=3)
    created = project.create_pattern_slices(sid, PATTERN, limit=2)
    assert len(created["slices"]) == 2
    before = project.source_slices(sid)
    for pattern in ("[", "missing", "^"):
        with pytest.raises(ValueError):
            project.create_pattern_slices(sid, pattern)
        assert project.source_slices(sid) == before
    with pytest.raises(tool_api.ToolError, match="不认识的参数"):
        tool_api.call("create_pattern_slices", {"source_id": sid, "pattern": PATTERN,
                      "source_hash": "obsolete"}, project=layout(project.project_info().path).entry)


def test_more_than_thousand_chapters_atomic_create_and_compact_tool_samples(project):
    text = "".join(f"Chapter {i} title\n正文\n" for i in range(1200))
    sid = project.import_source("长原文", text)
    path = layout(project.project_info().path).entry
    preview = tool_api.call("preview_pattern_slices", {"source_id": sid, "pattern": PATTERN}, project=path)
    assert preview["match_count"] == preview["selected_count"] == 1200
    assert preview["sampled"] and preview["sample_count"] == 6
    assert "slices" not in preview
    assert preview["slice_samples"][-1]["end"] == len(text)
    result = tool_api.call("create_pattern_slices", {"source_id": sid, "pattern": PATTERN}, project=path)
    assert result["status"] == "created" and result["saved_count"] == 1200
    assert len(project.source_slices(sid)) == 1200
    native = {tool["name"] for tool in assistant.tool_catalog()}
    assert {"preview_pattern_slices", "create_pattern_slices"} <= native


@pytest.mark.parametrize("pattern,start_index,limit", [("[", 0, None), ("^", 0, None),
    ("missing", 0, None), ("(?s).+", 0, None), (PATTERN, True, 3),
    (PATTERN, 0, False), (PATTERN, 99, None), (PATTERN, 0, 1.5)])
def test_invalid_pattern_and_selection_are_readonly(project, pattern, start_index, limit):
    sid = project.import_source("原文", TEXT)
    with pytest.raises(ValueError):
        project.preview_pattern_slices(sid, pattern, start_index, limit)
    assert project.source_slices(sid) == []


def test_backtracking_has_actual_engine_timeout_and_no_write(project, monkeypatch):
    sid = project.import_source("原文", "a" * 100_000 + "!")
    monkeypatch.setattr(pattern_slices, "SCAN_TIMEOUT_SECONDS", 0.01)
    began = time.monotonic()
    with pytest.raises(ValueError, match="扫描超时"):
        project.preview_pattern_slices(sid, r"(a+)+$")
    assert time.monotonic() - began < 2
    assert project.source_slices(sid) == []


def test_excessive_matches_have_bounded_plan_and_no_write(project, monkeypatch):
    sid = project.import_source("原文", TEXT)
    monkeypatch.setattr(pattern_slices, "MAX_HEADINGS", 3)
    with pytest.raises(ValueError, match="匹配超过"):
        project.preview_pattern_slices(sid, PATTERN)
    assert project.source_slices(sid) == []
