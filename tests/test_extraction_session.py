from __future__ import annotations

import json

import pytest

from narrative.extraction_session import (
    IncompleteExtraction,
    open_extraction_session,
    record_review_action,
    session_directory,
)
from narrative.harness import HarnessReply, HarnessSettings
from narrative.operations import Operations


class FakeRunner:
    restored = True
    finish_reason = "stop"
    calls: list[tuple[str, str]] = []
    resumed: list[str] = []

    def __init__(self, settings, api_key, directory):
        self.directory = directory

    def resume(self, session):
        self.resumed.append(session)
        return self.restored

    def agent_turn(self, instruction, session):
        self.calls.append((instruction, session))
        return HarnessReply('{"nodes":[],"properties":[]}',
                            {"finish_reason": self.finish_reason, "usage": {"output_tokens": 5}})

    def close(self):
        pass


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeRunner, "restored", True)
    monkeypatch.setattr(FakeRunner, "finish_reason", "stop")
    monkeypatch.setattr(FakeRunner, "calls", [])
    monkeypatch.setattr(FakeRunner, "resumed", [])
    return Operations.create_graph_project(tmp_path / "session.nproj")


def _open(project, tmp_path, settings=None):
    return open_extraction_session(project, settings or HarnessSettings(), "not-a-key", tmp_path, FakeRunner)


def test_rounds_and_review_actions_resume_one_project_conversation(project, tmp_path):
    runner, session = _open(project, tmp_path / "slice-a")
    runner.agent_turn("extract first slice", session)
    # Human review can happen during a model workflow holding the session lease.
    record_review_action(project, {"action": "accept", "candidate_id": "candidate-a"})
    runner.close()
    runner, resumed = _open(project, tmp_path / "slice-b")
    reply = runner.agent_turn("review second slice", resumed)
    assert resumed == session == reply.metadata["session_id"]
    assert FakeRunner.resumed == [session, session]
    assert "candidate-a" in FakeRunner.calls[-1][0]
    runner.agent_turn("continue same review", resumed)
    assert "candidate-a" not in FakeRunner.calls[-1][0]
    runner.close()
    directory = session_directory(project)
    assert len(list((directory / "turns").glob("*/raw.txt"))) == 3
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["turn_count"] == 3


def test_failed_resume_never_silently_loses_previous_context(project, tmp_path, monkeypatch):
    runner, session = _open(project, tmp_path)
    runner.agent_turn("first", session)
    runner.close()
    monkeypatch.setattr(FakeRunner, "restored", False)
    with pytest.raises(ValueError, match="无法恢复"):
        _open(project, tmp_path)
    assert len(FakeRunner.calls) == 1


def test_another_project_cannot_reuse_the_same_conversation_directory(project, tmp_path, monkeypatch):
    from narrative import extraction_session

    runner, session = _open(project, tmp_path)
    runner.agent_turn("first project", session)
    runner.close()
    directory = session_directory(project)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_id"] == project.graph_project_info()["project_id"]
    another = Operations.create_graph_project(tmp_path / "another.nproj")
    monkeypatch.setattr(extraction_session, "session_directory", lambda *_: directory)
    with pytest.raises(ValueError, match="另一工程"):
        _open(another, tmp_path)
    assert len(FakeRunner.calls) == 1
    assert FakeRunner.resumed == [session]


def test_model_change_is_rejected_but_budget_changes_are_recorded(project, tmp_path):
    runner, session = _open(project, tmp_path)
    runner.agent_turn("first", session)
    runner.close()
    with pytest.raises(ValueError, match="已改变"):
        _open(project, tmp_path, HarnessSettings(model="different-model"))
    runner, next_session = _open(project, tmp_path, HarnessSettings(max_tokens=32_768))
    assert next_session == session
    runner.close()
    manifest = json.loads((session_directory(project) / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["context_policy_history"]) == 2


def test_truncated_reply_is_preserved_without_another_model_call(project, tmp_path, monkeypatch):
    monkeypatch.setattr(FakeRunner, "finish_reason", "length")
    runner, session = _open(project, tmp_path)
    try:
        with pytest.raises(IncompleteExtraction, match="不自动重试"):
            runner.agent_turn("extract", session)
    finally:
        runner.close()
    assert len(FakeRunner.calls) == 1
    turn = next((session_directory(project) / "turns").iterdir())
    assert (turn / "raw.txt").read_text(encoding="utf-8") == '{"nodes":[],"properties":[]}'
    assert json.loads((turn / "metadata.json").read_text(encoding="utf-8"))["usage"] == {"output_tokens": 5}


def test_one_million_default_context_reserves_output_and_compaction_headroom():
    settings = HarnessSettings()
    assert settings.context_window == 1_000_000
    assert settings.max_tokens == settings.output_reserve == 65_536
    assert settings.compaction_reserve == 131_072
    assert settings.compaction_max_tokens == 32_768
    assert settings.compaction_threshold == 803_392


def test_read_and_property_relation_review_share_session(project, tmp_path, monkeypatch):
    from narrative import extraction, extraction_review

    class Model(FakeRunner):
        def agent_turn(self, instruction, session):
            self.calls.append((instruction, session))
            if len(self.calls) == 1:
                raw = '{"nodes":[{"name":"甲","type":"person","quotes":["甲见乙"]},{"name":"乙","type":"person","quotes":["甲见乙"]}],"properties":[]}'
            else:
                raw = '{"keep":[],"drop":[],"relations":[]}'
            return HarnessReply(raw, {"finish_reason": "stop"})

    monkeypatch.setattr(extraction, "HarnessRunner", Model)
    monkeypatch.setattr(extraction_review, "HarnessRunner", Model)
    source = project.import_source("第一片", "甲见乙")
    data = extraction.read_chapter("第一片", "甲见乙", settings=HarnessSettings(), api_key="x",
                                   workdir=tmp_path / "read", ops=project, source_id=source)
    data["source_id"] = source
    reviewed = extraction_review.review_properties(project, data, settings=HarnessSettings(), api_key="x",
                                                      workdir=tmp_path / "review")
    assert len(Model.calls) == 3  # extraction, property comparison, relation comparison
    assert {session for _, session in Model.calls} == {data["session_id"]}
    assert reviewed["session_id"] == data["session_id"]


def test_invalid_review_json_does_not_trigger_a_repair_request(project):
    from narrative.extraction_review import _turn

    class BrokenReply:
        calls = 0

        def agent_turn(self, instruction, session):
            self.calls += 1
            return HarnessReply("unfinished JSON", {"finish_reason": "stop"})

    model = BrokenReply()
    with pytest.raises(ValueError):
        _turn(model, "same-session", "review", project, [0])
    assert model.calls == 1


@pytest.mark.parametrize("first_payload", [
    {"keep": [], "drop": [], "unsure": [{"node": "甲", "property": "skin", "value": "白"}]},
    {"keep": [], "drop": [], "supersede": [{"node": "甲", "property": "skin", "value": "白", "at_event": "missing"}]},
])
def test_incomplete_uncertain_or_anchor_review_stops_before_relation_call(project, tmp_path, monkeypatch, first_payload):
    from narrative import extraction_review

    class TruncatedFollowup(FakeRunner):
        def agent_turn(self, instruction, session):
            self.calls.append((instruction, session))
            if len(self.calls) == 1:
                return HarnessReply(json.dumps(first_payload), {"finish_reason": "stop"})
            return HarnessReply("{}", {"finish_reason": "length"})

    monkeypatch.setattr(extraction_review, "HarnessRunner", TruncatedFollowup)
    source = project.import_source("片段", "甲见乙")
    data = {"source_id": source, "properties": [], "nodes": [
        {"name": "甲", "type": "person", "quotes": ["甲见乙"]},
        {"name": "乙", "type": "person", "quotes": ["甲见乙"]},
        {"name": "见面", "type": "event", "quotes": ["甲见乙"]},
    ]}
    with pytest.raises(IncompleteExtraction):
        extraction_review.review_properties(project, data, settings=HarnessSettings(), api_key="x",
                                              workdir=tmp_path / "review")
    assert len(TruncatedFollowup.calls) == 2
