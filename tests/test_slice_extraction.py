"""Slice rounds preserve full-source coordinates, history and reviewed graph writes."""
from __future__ import annotations

import json

import pytest

from narrative import extraction, tool_api
from narrative.harness import HarnessSettings
from narrative.operations import Operations
from narrative.slice_extraction import _directory


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ops = Operations.create_graph_project(tmp_path / "rounds.nproj")
    text = "林舟来了。\n😀前言\n【切片】林舟来了。陈墨来了。"
    source_id = ops.import_source("原文", text)
    start = text.index("【切片】")
    part = ops.create_source_slices(source_id, [{"title": "来客", "start": start, "end": len(text)}])[0]
    part = ops.source_slice(source_id, part["id"])
    monkeypatch.setattr("narrative.harness.harness_env", lambda _ops: (HarnessSettings(), "test", tmp_path))
    calls = []

    def read(_title, text, **kwargs):
        calls.append(kwargs)
        names = ["林舟"] if len(calls) == 1 else ["林舟", "陈墨"]
        return {"nodes": [
            {"name": name, "type": "person", "start": text.index(name),
             "quotes": [name + "来了。"], "aliases": [], "participants": [], "scene": ""}
            for name in names], "properties": [], "rejected": [], "session_id": "shared-test"}

    monkeypatch.setattr(extraction, "read_chapter", read)
    monkeypatch.setattr(extraction, "review_chapter", lambda _ops, data, **kw: data)
    return ops, source_id, part, calls


def test_nonzero_slice_offset_emoji_and_repeated_global_quote(workspace):
    ops, source_id, part, calls = workspace
    data = ops.extract_slice(source_id, part["id"])
    assert data["nodes"][0]["start"] == part["start"] + part["length"] - len("林舟来了。陈墨来了。")
    assert data["nodes"][0]["start"] > ops.source(source_id)["text"].index("林舟来了。")
    assert calls[0]["source_id"] == source_id
    assert calls[0]["prior"] is None
    assert data["session_id"] == "shared-test"
    assert data["missing"] is False
    assert len(ops.sources()) == 1


def test_completeness_review_adds_once_preserves_old_and_archives_each_round(workspace):
    ops, source_id, part, calls = workspace
    initial = ops.extract_slice(source_id, part["id"])
    reviewed = ops.extract_slice(source_id, part["id"], review=True)
    assert [node["name"] for node in reviewed["nodes"]] == ["林舟", "陈墨"]
    assert reviewed["nodes"][0] == initial["nodes"][0]
    assert reviewed["round"] == 2 and reviewed["added_nodes"] == 1
    assert calls[1]["prior"]["nodes"][0]["start"] == part["text"].index("林舟")
    again = ops.extract_slice(source_id, part["id"], review=True)
    assert len(again["nodes"]) == 2 and again["added_nodes"] == 0
    directory = _directory(ops, source_id, part["id"])
    assert len(list((directory / "rounds").glob("*/result.json"))) == 3
    original_path = directory / "rounds" / initial["run_id"] / "result.json"
    assert json.loads(original_path.read_text(encoding="utf-8"))["nodes"] == initial["nodes"]


def test_audit_cannot_add_quotes_from_outside_the_slice(workspace, monkeypatch):
    ops, source_id, part, _ = workspace

    def audit(_ops, data, **kw):
        assert data["slice_text"] == part["text"]
        data["nodes"].append({"name": "外部", "type": "person", "start": 0, "quotes": ["😀前言"]})
        return data

    monkeypatch.setattr(extraction, "review_chapter", audit)
    result = ops.extract_slice(source_id, part["id"])
    assert [node["name"] for node in result["nodes"]] == ["林舟"]
    assert result["rejected"][-1]["reason"] == "引文不在当前切片范围内"


@pytest.mark.parametrize("failure", ["model", "audit"])
def test_failed_round_keeps_latest_and_failure_evidence(workspace, monkeypatch, failure):
    ops, source_id, part, _ = workspace
    initial = ops.extract_slice(source_id, part["id"])
    if failure == "model":
        def fail(*args, **kwargs):
            raise ValueError("bounded model failure")
        monkeypatch.setattr(extraction, "read_chapter", fail)
    else:
        monkeypatch.setattr(extraction, "review_chapter", lambda _ops, data, **kw: data | {"review": {"error": "failed"}})
    with pytest.raises(ValueError):
        ops.extract_slice(source_id, part["id"], review=True)
    assert ops.load_slice_extraction(source_id, part["id"])["run_id"] == initial["run_id"]
    directory = _directory(ops, source_id, part["id"])
    assert len(list((directory / "rounds").glob("*/failure.json"))) == 1


def test_review_requires_a_previous_round_and_history_hash_must_match(workspace):
    ops, source_id, part, _ = workspace
    with pytest.raises(ValueError, match="尚未提取"):
        ops.extract_slice(source_id, part["id"], review=True)
    result = ops.extract_slice(source_id, part["id"])
    directory = _directory(ops, source_id, part["id"])
    file = directory / "rounds" / result["run_id"] / "result.json"
    file.write_text(json.dumps(result | {"source_hash": "changed"}), encoding="utf-8")
    with pytest.raises(ValueError, match="已变化"):
        ops.load_slice_extraction(source_id, part["id"])


def test_changed_slice_bounds_block_using_old_results(workspace):
    ops, source_id, part, _ = workspace
    ops.extract_slice(source_id, part["id"])
    # Simulate a corrupted/external metadata edit, not a supported UI operation.
    with ops._store.connect(write=True) as db:
        key = f"source_slices:{source_id}"
        rows = json.loads(db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()["value"])
        rows[0]["start"] += 1
        db.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(rows), key))
    with pytest.raises(ValueError, match="已变化"):
        ops.extract_slice(source_id, part["id"], review=True)


def test_completeness_review_keeps_prior_properties_even_if_audit_omits_them(workspace, monkeypatch):
    ops, source_id, part, _ = workspace
    original_read = extraction.read_chapter

    def read(title, text, **kwargs):
        data = original_read(title, text, **kwargs)
        if kwargs["prior"] is None:
            data["properties"] = [{"node": "林舟", "property": "行动", "value": "来了",
                                   "start": text.index("林舟"), "quotes": ["林舟来了。"]}]
        return data

    monkeypatch.setattr(extraction, "read_chapter", read)
    initial = ops.extract_slice(source_id, part["id"])
    reviewed = ops.extract_slice(source_id, part["id"], review=True)
    assert reviewed["properties"] == initial["properties"]
    assert reviewed["added_properties"] == 0


def test_commit_then_review_does_not_duplicate_existing_entities(workspace):
    ops, source_id, part, _ = workspace
    ops.extract_slice(source_id, part["id"])
    first = ops.commit_slice_extraction(source_id, part["id"])
    assert not first.get("errors")
    if first.get("candidate"):
        ops.change_accept(first["candidate"], 1)
    ops.extract_slice(source_id, part["id"], review=True)
    second = ops.commit_slice_extraction(source_id, part["id"])
    assert not second.get("errors")
    if second.get("candidate"):
        ops.change_accept(second["candidate"], 1)
    nodes = ops.graph_snapshot()["nodes"]
    assert len([node for node in nodes if node["name"] == "林舟"]) == 1
    assert len([node for node in nodes if node["name"] == "陈墨"]) == 1


def test_repeated_commit_of_same_round_reuses_pending_candidate(workspace):
    ops, source_id, part, _ = workspace
    ops.extract_slice(source_id, part["id"])
    first = ops.commit_slice_extraction(source_id, part["id"], review="all")
    assert first.get("candidate")
    from narrative.project_package import layout

    reopened = Operations.open(layout(ops.project_info().path).entry)
    second = reopened.commit_slice_extraction(source_id, part["id"], review="all")
    assert second == first
    assert len(ops.change_candidates("pending")) == 1


def test_a_busy_slice_lease_blocks_before_model_calls(workspace):
    from narrative.leases import ProjectLease

    ops, source_id, part, calls = workspace
    directory = _directory(ops, source_id, part["id"])
    directory.mkdir(parents=True)
    lease = ProjectLease(directory / "workflow.lock")
    try:
        with pytest.raises(ValueError, match="请等待"):
            ops.extract_slice(source_id, part["id"])
        assert not calls
        assert ops.load_slice_extraction(source_id, part["id"])["missing"]
    finally:
        lease.close()


def test_slice_tools_share_the_operations_surface(workspace):
    ops, source_id, part, _ = workspace
    params = {"source_id": source_id, "slice_id": part["id"]}
    from narrative.project_package import layout

    project = layout(ops.project_info().path).entry
    assert tool_api.call("get_slice_extraction", params, project=project)["missing"]
    assert tool_api.call("extract_slice", params, project=project)["round"] == 1
    assert tool_api.call("review_slice", params, project=project)["round"] == 2
    assert tool_api.call("get_slice_extraction", params, project=project)["missing"] is False
