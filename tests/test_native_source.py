"""File and slice browsing through the public native bridge."""
from __future__ import annotations

import os
import time

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from narrative.graph_operations import GraphOperations
from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge
from narrative.operations import Operations

pytestmark = pytest.mark.skipif(os.environ.get("QT_QPA_PLATFORM") != "offscreen", reason="offscreen Qt required")


def _value(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


def _wait(app, predicate):
    deadline = time.monotonic() + 8
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert predicate()


@pytest.fixture()
def source_workspace(tmp_path):
    app = QApplication.instance() or QApplication([])
    project = tmp_path / "sources.nproj"
    ops = Operations.create_graph_project(project)
    source_id = ops.import_source("original", "First section. Second section.")
    slices = ops.create_source_slices(source_id, [
        {"title": "first", "start": 0, "end": 14},
        {"title": "second", "start": 15, "end": 30},
    ])
    bridge = NativeBridge()
    errors = []
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    bridge.openProject(str(project))
    _wait(app, lambda: bridge._project_path is not None)
    window.setProperty("mode", "workspace")
    window.setProperty("currentPage", "source")
    page = window.findChild(object, "sourcePage")
    assert page is not None
    _wait(app, lambda: _value(page.property("current")).get("id") == source_id)
    try:
        yield app, bridge, page, project, source_id, slices, errors
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()


def test_file_selection_lists_slices_and_previews_content(source_workspace):
    app, _bridge, page, _project, source_id, slices, errors = source_workspace
    _wait(app, lambda: len(_value(page.property("slices"))) == 2)
    assert page.property("selectedId") == source_id
    assert len(_value(page.property("sources"))) == 1
    assert page.property("previewText") == "First section. Second section."
    page.selectSlice(slices[1]["id"])
    _wait(app, lambda: page.property("previewText") == "Second section.")
    page.selectSlice("")
    assert page.property("previewText") == "First section. Second section."
    assert not errors


def test_import_file_url_and_reload_files(source_workspace, tmp_path):
    app, bridge, page, project, _source_id, _slices, errors = source_workspace
    file = tmp_path / "name with spaces.md"
    file.write_text("# Imported\nBody.", encoding="utf-8", newline="\n")
    bridge.importSourceFile("", QUrl.fromLocalFile(str(file)).toString())
    _wait(app, lambda: bool(errors) or len(_value(page.property("sources"))) == 2)
    assert not errors
    assert page.property("previewText") == "# Imported\nBody."
    _wait(app, lambda: len(_value(page.property("sources"))) == 2)
    assert _value(page.property("current"))["title"] == "name with spaces"
    assert len(Operations.open(project).sources()) == 2
    assert not errors


def test_stale_source_and_slice_results_do_not_replace_selection(source_workspace):
    _app, _bridge, page, _project, source_id, slices, _errors = source_workspace
    page.selectSlice(slices[0]["id"])
    page.apply("source_slice", {"source_id": "other", "slice": {"id": slices[0]["id"], "text": "wrong"}})
    assert page.property("previewText") != "wrong"
    page.apply("source_slice", {"source_id": source_id, "slice": {"id": slices[1]["id"], "text": "wrong"}})
    assert page.property("previewText") != "wrong"
    page.apply("source", {"source_id": "other", "source": {"id": "other", "text": "wrong"}})
    assert _value(page.property("current"))["id"] == source_id
    page.load(source_id, 0)
    assert page.property("selectedSliceId") == ""


def test_slice_extraction_and_gap_review_use_selected_range(source_workspace, monkeypatch):
    app, _bridge, page, _project, source_id, slices, errors = source_workspace
    calls = []
    state = {"source_id": source_id, "slice_id": slices[0]["id"], "missing": True,
             "nodes": [], "properties": [], "round": 0}

    def load(_ops, source, slice_id):
        assert (source, slice_id) == (source_id, slices[0]["id"])
        return dict(state)

    def extract(_ops, source, slice_id, *, review=False):
        calls.append((source, slice_id, review))
        state.update(missing=False, round=state["round"] + 1,
                     nodes=[{"name": "Lin", "type": "person"}], session_id="shared-session")
        return dict(state)

    def commit(_ops, source, slice_id):
        calls.append((source, slice_id, "commit"))
        return {"source_id": source, "slice_id": slice_id, "round": 2, "doubtful": [{}]}

    monkeypatch.setattr(GraphOperations, "load_slice_extraction", load)
    monkeypatch.setattr(GraphOperations, "extract_slice", extract)
    monkeypatch.setattr(GraphOperations, "commit_slice_extraction", commit)
    page.selectSlice(slices[0]["id"])
    _wait(app, lambda: page.property("extractionLoaded"))
    assert page.findChild(object, "sourceSliceExtract").property("enabled")
    assert not page.findChild(object, "sourceSliceReview").property("enabled")
    page.runExtraction("review")
    assert calls == []
    page.runExtraction("extract")
    page.runExtraction("extract")
    _wait(app, lambda: not page.property("extractionBusy"))
    assert calls == [(source_id, slices[0]["id"], False)]
    assert page.findChild(object, "sourceSliceReview").property("enabled")
    assert _value(page.property("extraction"))["session_id"] == "shared-session"
    page.runExtraction("review")
    _wait(app, lambda: not page.property("extractionBusy"))
    assert calls[-1] == (source_id, slices[0]["id"], True)
    assert _value(page.property("extraction"))["round"] == 2
    page.runExtraction("commit")
    _wait(app, lambda: not page.property("extractionBusy"))
    assert calls[-1] == (source_id, slices[0]["id"], "commit")
    assert not page.findChild(object, "sourceSliceCommit").property("enabled")
    assert "审核" in page.property("notice")
    assert not errors


def test_stale_slice_extraction_does_not_replace_selected_result(source_workspace):
    app, _bridge, page, _project, source_id, slices, _errors = source_workspace
    page.selectSlice(slices[0]["id"])
    _wait(app, lambda: page.property("extractionLoaded"))
    before = _value(page.property("extraction"))
    page.apply("slice_extraction", {"source_id": "other", "slice_id": slices[0]["id"],
                                     "missing": False, "round": 99})
    page.apply("slice_extraction_action", {"source_id": source_id, "slice_id": slices[1]["id"],
                                            "action": "extract", "extraction": {"round": 99}})
    assert _value(page.property("extraction")) == before


def test_slice_model_failure_releases_action_buttons(source_workspace, monkeypatch):
    app, _bridge, page, _project, _source_id, slices, errors = source_workspace

    def failed(*args, **kwargs):
        raise ValueError("model unavailable")

    monkeypatch.setattr(GraphOperations, "extract_slice", failed)
    page.selectSlice(slices[0]["id"])
    _wait(app, lambda: page.property("extractionLoaded"))
    page.runExtraction("extract")
    _wait(app, lambda: not page.property("extractionBusy"))
    assert page.findChild(object, "sourceSliceExtract").property("enabled")
    assert "model unavailable" in page.property("notice")
    assert errors[-1] == ("slice_extraction_action", "model unavailable")


def test_model_slice_action_is_not_discarded_by_ui_read_deadline(source_workspace):
    _app, bridge, _page, _project, _source_id, _slices, _errors = source_workspace
    request_id = 99999
    bridge._requests[request_id] = (
        "slice_extraction_action", bridge._epoch, None, None,
        time.perf_counter() - bridge.STALE_AFTER_SECONDS - 1,
    )
    bridge._drop_stale()
    assert request_id in bridge._requests
    bridge._requests.pop(request_id)


def test_file_without_slices_can_extract_and_review_whole_text(source_workspace, monkeypatch):
    app, _bridge, page, project, _source_id, _slices, errors = source_workspace
    ops = Operations.open(project)
    text = "A complete chapter without slices."
    source = ops.import_source("unsliced chapter", text)
    saved = {}
    calls = []

    def extract(current_ops, source_id, slice_id, *, review=False):
        part = current_ops.source_slice(source_id, slice_id)
        assert part["text"] == text
        calls.append((slice_id, review))
        saved.update(source_id=source_id, slice_id=slice_id, missing=False,
                     nodes=[{"name": "A", "type": "person"}], properties=[],
                     round=len(calls))
        return dict(saved)

    monkeypatch.setattr(GraphOperations, "extract_slice", extract)
    monkeypatch.setattr(GraphOperations, "load_slice_extraction", lambda *args: dict(saved))
    page.load(source, -1)
    _wait(app, lambda: page.property("extractionLoaded"))
    assert ops.source_slices(source) == []  # Browsing does not create ranges.
    button = page.findChild(object, "sourceSliceExtract")
    assert button.property("visible") and button.property("enabled")
    assert not page.findChild(object, "sourceSliceReview").property("enabled")
    page.runExtraction("extract")
    _wait(app, lambda: not page.property("extractionBusy"))
    parts = ops.source_slices(source)
    assert len(parts) == 1
    assert (parts[0]["start"], parts[0]["end"]) == (0, len(text))
    assert calls == [(parts[0]["id"], False)]
    page.load(source, -1)
    _wait(app, lambda: page.property("extractionLoaded"))
    assert page.findChild(object, "sourceSliceReview").property("enabled")
    page.runExtraction("review")
    _wait(app, lambda: not page.property("extractionBusy"))
    assert calls[-1] == (parts[0]["id"], True)
    assert len(ops.source_slices(source)) == 1
    assert not errors
