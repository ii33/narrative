"""One bounded exact matcher serves optional coordinates and automatic evidence checks."""
import copy

from agent_fixtures import TEXT, draft_result
from agent_fixtures import project as project

from narrative.extraction import _find_quote
from narrative.property_core import validate_records
from narrative.source_location import ExactQuoteLookup, locate_text


class CountedText(str):
    def __new__(cls, value):
        result = super().__new__(cls, value)
        result.find_calls = 0
        return result
    def find(self, *args):
        self.find_calls += 1
        return super().find(*args)


def test_unicode_overlapping_matches_progressive_cache_and_limit_contract():
    text = CountedText("😀aaaa句😀")
    lookup = ExactQuoteLookup(text)
    assert lookup.positions("aa", 1) == [1]
    assert lookup.positions("aa", 2) == [1, 2]
    assert lookup.positions("aa", 100) == [1, 2, 3]
    calls = text.find_calls
    assert lookup.positions("aa", 1000) == [1, 2, 3]
    assert text.find_calls == calls == 4
    row = locate_text(text, ["aa", "aa"], 1)
    assert row[0]["matches"] == [{"start": 1, "end": 3}]
    assert row[0]["match_count"] == 2 and row[0]["truncated"] and not row[0]["count_is_exact"]
    assert row[0]["matches"] == row[1]["matches"]
    assert _find_quote(text, '“句😀”') == (5, "句😀")
    assert _find_quote(text, "句 🙂") == (-1, "句 🙂")


def definitions(quote, count=200):
    return [{"kind": "property_definition", "id": f"定义{i}", "value_type": "string", "cardinality": "one",
             "evidence": {"source_id": "S", "quote": quote}} for i in range(count)]


def test_property_validation_scans_shared_evidence_once_and_never_across_calls():
    text = CountedText("😀唯一引文。")
    graph = {"sources": [{"id": "S", "text": text}]}
    assert validate_records(definitions("唯一引文。"), graph, []) == []
    assert text.find_calls == 2
    repeated = CountedText("唯一引文。唯一引文。")
    errors = validate_records(definitions("唯一引文。"), {"sources": [{"id": "S", "text": repeated}]}, [])
    assert len([row for row in errors if row["code"] == "EVIDENCE"]) == 200
    assert repeated.find_calls == 2
    absent = CountedText("没有该句")
    errors = validate_records(definitions("唯一引文。"), {"sources": [{"id": "S", "text": absent}]}, [])
    assert len([row for row in errors if row["code"] == "EVIDENCE"]) == 200
    assert absent.find_calls == 1


def test_submit_locates_and_validates_without_optional_locate_or_preview(project, monkeypatch):
    sid = project.import_source("自动后台定位", "😀" + TEXT)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Automatic submission must not require optional model lookup or preview")
    monkeypatch.setattr(project, "locate", forbidden)
    monkeypatch.setattr(project, "preview_extraction", forbidden)
    result = project.submit_extraction(sid, draft_result())
    assert result["status"] == "accepted"
    nodes = project.graph_snapshot()["nodes"]
    first = next(node for node in nodes if node["name"] == "甲约好接乙")
    assert first["disclosure"]["start"] == 1
    before = copy.deepcopy(project.graph_snapshot())
    draft = draft_result()
    draft["nodes"][0]["quotes"] = ["原文不存在的引文"]
    rejected = project.submit_extraction(sid, draft)
    assert rejected["status"] == "rejected" and rejected["diagnostics"]
    assert project.graph_snapshot() == before


def test_ambiguous_overlapping_event_evidence_is_still_atomic_rejection(project):
    sid = project.import_source("重叠匹配", "😀aaa")
    before = copy.deepcopy(project.graph_snapshot())
    result = project.submit_extraction(sid, {"nodes": [
        {"type": "scene", "name": "场", "quotes": ["aaa"]},
        {"type": "event", "name": "事件", "scene": "场", "quotes": ["aa"]}], "properties": []})
    assert result["status"] == "rejected"
    assert any("出现多处" in row["message"] for row in result["diagnostics"])
    assert project.graph_snapshot() == before
