"""Exercise readable review and partial decisions through Operations and the UI."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from narrative.operations import OperationError, Operations


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("NARRATIVE_PROJECT_NAME", "review")
    monkeypatch.setattr(
        "narrative.operations.credential_status", lambda: {"available": False, "source": "none"}
    )
    monkeypatch.setattr(
        "narrative.jobs.start", lambda *args, **kwargs: pytest.fail("审核不应调用模型")
    )
    return Operations.open_project(tmp_path, "review")


def open_ui():
    path = Path(__file__).resolve().parents[1] / "src/narrative/ui.py"
    app = AppTest.from_file(str(path), default_timeout=30).run()
    assert not app.exception
    return app


def test_partial_decision_preserves_rejected_items_without_formal_changes(project):
    candidate = project.propose_objects(
        [{"kind": "entity", "id": "a", "name": "甲"}, {"kind": "entity", "id": "b", "name": "乙"}]
    )
    original = project.project_snapshot()
    result = project.select_candidate_operations(candidate, ["a"], 1)
    assert (result["kept_count"], result["rejected_count"], result["version"]) == (1, 1, 2)
    assert project.project_snapshot() == original
    assert project.review_candidate(candidate).change.operations[0].id == "a"
    rejected = project.review_candidate(result["rejected_change_id"])
    assert [op.id for op in rejected.change.operations] == ["b"]
    assert candidate in rejected.change.note
    assert project.candidates("rejected")[0]["id"] == result["rejected_change_id"]
    with pytest.raises(OperationError, match="候选已改变"):
        project.accept_candidate(candidate, 1)
    project.accept_candidate(candidate, 2)
    assert [entity["name"] for entity in project.entities()] == ["甲"]
    restored = Operations(
        project.backup(Path(project.project_info().path).parent / "partial-backup.sqlite3")
    )
    assert restored.candidates() == project.candidates()


def test_excluded_dependency_is_not_invented_or_auto_accepted(project):
    source = project.import_text("原文", "甲说门开着。")
    candidate = project.propose_objects(
        [
            {"kind": "entity", "id": "a", "name": "甲"},
            {"kind": "entity", "id": "door", "name": "门", "entity_type": "item"},
            {
                "kind": "assertion",
                "id": "claim",
                "subject_id": "door",
                "predicate": "开着",
                "value": True,
                "scope": "claim",
                "holder_id": "a",
                "evidence": [project.locate(source, "甲说门开着。")[0].model_dump()],
            },
        ]
    )
    project.select_candidate_operations(candidate, ["door", "claim"], 1)
    review = project.review_candidate(candidate)
    assert any(d.severity == "error" for d in review.diagnostics)
    with pytest.raises(OperationError):
        project.accept_candidate(candidate, review.version)
    assert project.entities() == [] and project.assertions() == []


def test_selection_rejects_stale_and_unknown_decisions_atomically(project):
    candidate = project.propose_entity("甲", "person", [])
    review = project.review_candidate(candidate)
    project.edit_candidate(candidate, review.change.model_dump_json(), 1)
    with pytest.raises(OperationError, match="候选已改变"):
        project.select_candidate_operations(candidate, [], 1)
    for selection in [["missing"], [review.change.operations[0].id] * 2]:
        with pytest.raises(OperationError, match="不能重复"):
            project.select_candidate_operations(candidate, selection, 2)
    assert len(project.candidates("pending")) == 1
    assert project.candidates("rejected") == []
    project.select_candidate_operations(candidate, [], 2)
    assert not project.candidates("pending") and len(project.candidates("rejected")) == 1
    assert project.entities() == []


def test_ui_partial_review_requires_save_and_preserves_exclusion(project):
    candidate = project.propose_objects(
        [{"kind": "entity", "id": "a", "name": "甲"}, {"kind": "entity", "id": "b", "name": "乙"}]
    )
    app = open_ui()
    [checkbox for checkbox in app.checkbox if checkbox.label == "保留本条"][1].uncheck().run()
    assert not app.exception
    assert next(b for b in app.button if b.label == "接受并保存正式资料").disabled
    next(b for b in app.button if b.label == "保存逐条审核结果").click().run()
    assert not app.exception
    assert len(project.candidates("rejected")) == 1
    assert [op.id for op in project.review_candidate(candidate).change.operations] == ["a"]
    next(b for b in app.button if b.label == "接受并保存正式资料").click().run()
    assert not app.exception
    assert [entity["name"] for entity in project.entities()] == ["甲"]


def test_ui_all_diagnostics_visible_despite_bad_structure(project):
    source = project.import_text("原文", "甲说门开着。")
    run = project.create_run(source)
    chunk = project.chunks(run)[0]["id"]
    payload = {
        "entities": [{"ref": "a", "name": "甲"}],
        "assertions": [
            None,
            {
                "subject_ref": "missing",
                "predicate": "开着",
                "validity": None,
                "quote": "不存在的原句",
                "scope": "claim",
            },
        ],
        "events": [
            {
                "ref": "leave",
                "title": "离开",
                "action": "move",
                "actor_ref": "a",
                "location_ref": "missing",
                "time_kind": "point",
                "quote": "甲说门开着。",
            }
        ],
    }
    with pytest.raises(OperationError):
        project.ingest_result(chunk, json.dumps(payload))
    app = open_ui()
    rows = [row for frame in app.dataframe for row in frame.value.to_dict("records")]
    paths = {row.get("字段位置", "") for row in rows}
    assert "$.assertions[0]" in paths
    assert "$.assertions[1].validity" in paths
    assert "$.assertions[1].subject_ref" in paths
    assert "$.assertions[1].quote" in paths
    assert any(path.startswith("$.events[0].time") for path in paths)
    assert not project.candidates() and not project.entities()
    assert project.chunks(run)[0]["raw"] == json.dumps(payload)
