"""Short public IDs, batched Unicode location, and no model-copied pattern hashes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from agent_fixtures import project as project

from narrative import tool_api
from narrative.project_package import layout


def call(ops, name, params):
    return tool_api.call(name, params, project=layout(ops.project_info().path).entry)


def test_sources_and_slices_have_stable_short_ids_and_public_outputs_do_not_expose_hashes(project):
    first = project.import_source("一", "😀甲出门。")
    assert first == "SRC1"
    assert project.import_source("一", "😀甲出门。") == first
    second = project.import_source("二", "乙停下。")
    assert second == "SRC2"
    slices = project.create_source_slices(
        first, [{"title": "完整", "start": 0, "end": len("😀甲出门。")}]
    )['result']
    assert [row["id"] for row in slices] == ["SRC1-SL1"]
    assert (
        project.create_source_slices(
            first, [{"title": "完整", "start": 0, "end": len("😀甲出门。")}]
        )['result']
        == slices
    )
    outputs = [
        call(project, "list_sources", {}),
        call(project, "read_source", {"source_id": first}),
        call(project, "list_source_slices", {"source_id": first}),
    ]
    encoded = json.dumps(outputs, ensure_ascii=False)
    assert not re.search(r"\b[0-9a-f]{64}\b", encoded)
    assert "source_hash" not in encoded and "plan_id" not in encoded


def test_batched_location_reports_duplicate_missing_and_truncated_unicode_matches_in_input_order(
    project, monkeypatch
):
    text = "😀甲进屋。甲停下。😀甲进屋。"
    source = project.import_source("重复定位", text)
    from narrative.graph_store import GraphStore

    original = GraphStore.source
    reads = []

    def counted(self, source_id):
        if source_id == source:
            reads.append(source_id)
        return original(self, source_id)

    monkeypatch.setattr(GraphStore, "source", counted)
    quotes = ["甲进屋。", "😀甲", "不存在", "甲", "甲进屋。"]
    revision = project.graph_project_info()["revision"]
    result = call(
        project, "locate_source", {"source_id": source, "quotes": quotes, "limit_per_quote": 1}
    )
    assert len(reads) == 1
    assert [r["index"] for r in result["results"]] == list(range(len(quotes)))
    assert [r["match_count"] for r in result["results"]] == [2, 2, 0, 2, 2]
    assert [r["count_is_exact"] for r in result["results"]] == [False, False, True, False, False]
    assert result["results"][0]["diagnostic"] == "ambiguous" and result["results"][0]["truncated"]
    assert result["results"][2]["diagnostic"] == "not_found" and not result["results"][2]["matches"]
    for row in result["results"]:
        for match in row["matches"]:
            assert text[match["start"] : match["end"]] == quotes[row["index"]]
    assert result["results"][0]["matches"][0] == {"start": 1, "end": 5}
    assert project.graph_project_info()["revision"] == revision


def test_one_source_infers_source_id_but_multiple_sources_require_explicit_selection(project):
    project.import_source("单篇", "😀甲进屋。")
    result = call(project, "locate_source", {"quotes": ["甲进屋。"]})
    assert result["source_id"] == "SRC1" and result["results"][0]["diagnostic"] == "unique"
    project.import_source("另一篇", "乙停下。")
    with pytest.raises(ValueError):
        call(project, "locate_source", {"quotes": ["甲进屋。"]})


def test_pattern_creation_revalidates_programmatically_without_external_hash_arguments(project):
    text = "序。\n第一章 出门\n甲出门。\n第二章 停步\n乙停下。\n第三章 回来\n甲回来。"
    source = project.import_source("三章", text)
    params = {"source_id": source, "pattern": r"(?m)^第[一二三]章[^\n]*", "limit": 2}
    schema = tool_api.TOOLS["create_pattern_slices"].schema()
    assert "source_hash" not in schema["properties"] and "plan_id" not in schema["properties"]
    before = project.graph_project_info()["revision"]
    result = call(project, "create_pattern_slices", params)
    assert result["status"] == "created" and result["selected_count"] == 2
    slices = project.source_slices(source)
    assert [row["id"] for row in slices] == ["SRC1-SL1", "SRC1-SL2"]
    assert slices[0]["start"] == text.index("第一章") and slices[1]["end"] == text.index("第三章")
    assert "source_hash" not in result and "plan_id" not in result
    assert call(project, "create_pattern_slices", params)["saved_count"] == 2
    assert project.graph_project_info()["revision"] == before


def test_tool_details_omit_protocol_call_id_but_keep_business_identifiers():
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    engine = QQmlEngine()
    component = QQmlComponent(
        engine,
        QUrl.fromLocalFile(str(Path("src/narrative/qml/ToolActivity.qml").resolve())),
    )
    tool = {
        "tool": "read_source",
        "id": "opaque-sdk-call-unique",
        "call_id": "opaque-sdk-call-unique",
        "args": {"source_id": "SRC1", "record_id": "PV1"},
    }
    item = component.createWithInitialProperties(
        {"t": {"muted": "#222", "panelAlt": "#fff"}, "tools": [tool], "expanded": True}
    )
    assert item is not None, [e.toString() for e in component.errors()]
    details = item.property("details")
    assert "opaque-sdk-call-unique" not in details and "SRC1" in details and "PV1" in details
    stored = item.property("tools")
    stored = stored.toVariant() if hasattr(stored, "toVariant") else stored
    assert stored[0]["id"] == "opaque-sdk-call-unique"
    item.deleteLater()
    app.processEvents()
