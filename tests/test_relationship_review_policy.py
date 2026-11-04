"""Evidence-led relationship review keeps history and does not expire conflict by rule."""
import pytest

from narrative.assistant import _first_prompt
from narrative.harness import CONVERSATION_SYSTEM_PROMPT
from narrative.operations import Operations
from narrative.tool_api import call

TEXT = "甲和乙是朋友，丙和丁也是朋友。四人在药铺争执，甲乙决定继续结伴送药，丙丁也约好合作。甲发现乙偷走药款，乙承认背叛。甲说：绝交，从此不再来往。两人的友谊至此结束。丙丁为路线争吵后仍并肩送药。"
QUOTES = ("甲和乙是朋友，丙和丁也是朋友。", "四人在药铺争执，甲乙决定继续结伴送药，丙丁也约好合作。", "甲发现乙偷走药款，乙承认背叛。甲说：绝交，从此不再来往。两人的友谊至此结束。", "丙丁为路线争吵后仍并肩送药。")


@pytest.fixture
def relationship_project(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    entry = tmp_path / "review.nproj"
    ops = Operations.create_graph_project(entry)
    source = ops.import_source("友谊变化", TEXT)
    result = ops.submit_extraction(source, {"nodes": [
        *[{"type": "person", "name": name, "quotes": [name]} for name in "甲乙丙丁"],
        {"type": "scene", "name": "送药风波", "quotes": [TEXT]},
        *[{"type": "event", "name": name, "scene": "送药风波", "participants": list("甲乙丙丁"), "quotes": [quote]} for name, quote in zip(("建立友谊", "争执后合作", "明确断交", "继续送药"), QUOTES, strict=True)]],
        "properties": [{"node": a, "property": "朋友", "value": b, "value_type": "node", "symmetric": True, "at_event": "建立友谊", "quotes": [QUOTES[0]]} for a, b in (("甲", "乙"), ("丙", "丁"))]})
    assert result["status"] == "accepted", result
    ids = {node["name"]: node["id"] for node in ops.graph_snapshot()["nodes"]}
    return ops, entry, source, ids


def test_relationship_prompt_requires_temporal_evidence_and_distinguishes_bad_record():
    prompt = _first_prompt("复核本段关系")
    assert "检查建立依据、可能的终止事件" in prompt
    assert "争吵、一次攻击、失联或后文未再提及不自动等于关系终止" in prompt
    assert "泛称、反讽、否认或追述" in prompt
    assert "submit_extraction.expirations" in prompt and "expire_properties" in prompt
    assert "不把录入错误伪装为故事失效" in prompt
    assert "另起新有效区间" in prompt and "证据不足先保留" in prompt
    assert "冲突、攻击或未再提及不自动等于失效" in CONVERSATION_SYSTEM_PROMPT


@pytest.mark.parametrize("method", ["expire_properties", "submit_extraction"])
def test_review_expiry_preserves_true_history_and_quarrelling_friends(relationship_project, method):
    ops, entry, source, ids = relationship_project
    before = ops.graph_snapshot()["nodes"]
    records = [row for row in ops.property_records() if row.get("kind") == "property_value"]
    ended = next(row for row in records if row["node_id"] == ids["甲"])
    continuing = next(row for row in records if row["node_id"] == ids["丙"])
    end = TEXT.index(QUOTES[2])
    if method == "expire_properties":
        result = call(method, {"record_ids": [ended["id"]], "source_id": source, "start": end,
            "event_id": ids["明确断交"], "reason": "原文明示绝交且友谊结束；保留此前真实成立的关系"}, project=entry)
    else:
        result = ops.submit_extraction(source, {"nodes": [], "properties": [],
            "expirations": [{"record_id": ended["id"], "at_event": ids["明确断交"]}]})
    assert result["status"] == "accepted"
    current = {row["id"]: row for row in ops.property_records() if row.get("kind") == "property_value"}
    assert current[ended["id"]] == ended | {"valid_to": {"source_id": source, "start": end, "event_id": ids["明确断交"]}}
    assert current[continuing["id"]] == continuing
    assert ops.graph_snapshot()["nodes"] == before
    def edges(cut=""):
        return {row["record_id"] for row in call("relation_map", {"cut": cut}, project=entry)["edges"]}
    assert edges(ids["争执后合作"]) == {ended["id"], continuing["id"]}
    assert edges(ids["明确断交"]) == {continuing["id"]}
    assert edges() == {continuing["id"]}
