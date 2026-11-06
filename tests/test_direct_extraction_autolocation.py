"""Submitting exact quotes performs deterministic checks without a lookup preflight."""
import copy
import importlib
import json
from pathlib import Path

import pytest

from narrative.operations import Operations
from narrative.prompt_defaults import DEFAULT_PROMPTS
from narrative.tool_api import TOOLS


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    qa = importlib.import_module("accept_extraction_autolocation")
    ops = Operations.create_graph_project(tmp_path / "automatic.nproj")
    fixture = qa.seed(ops)
    first, second, third = (part + "。" for part in qa.TEXT.split("。") if part)
    draft = {"nodes": [
        {"type": "person", "name": "周宁", "quotes": [first]}, {"type": "person", "name": "林岚", "quotes": [second]},
        {"type": "location", "name": "资料室", "quotes": [first]}, {"type": "item", "name": "铜钥匙", "quotes": [first]},
        {"type": "scene", "name": "交接钥匙", "quotes": [qa.TEXT]},
        *[{"type": "event", "name": name, "scene": "交接钥匙", "participants": participants, "quotes": [quote]} for name, participants, quote in (("保管钥匙", ["周宁"], first), ("交出钥匙", ["周宁", "林岚"], second), ("带钥匙离开", ["林岚"], third))]],
        "properties": [
            {"node": "铜钥匙", "property": "持有者", "value": owner, "value_type": "either", "at_event": event, "op": "set", "quotes": [quote]} for owner, event, quote in (("周宁", "保管钥匙", first), ("林岚", "交出钥匙", second))] + [
            {"node": event, "property": "地点", "value": "资料室", "value_type": "node", "at_event": event, "op": "set", "quotes": [quote]} for event, quote in (("保管钥匙", first), ("交出钥匙", second), ("带钥匙离开", third))]}
    return ops, fixture, draft, qa


def test_direct_submission_locates_all_quotes_and_preserves_temporal_quality(sample, tmp_path):
    ops, fixture, draft, qa = sample
    submitted = ops.submit_extraction(fixture["source_id"], draft)
    assert submitted["status"] == "accepted" and submitted["valid"]
    (tmp_path / "task.json").write_text(json.dumps({"steps": [{"tool": "read_source", "ok": True},
        {"tool": "submit_extraction", "ok": True, "result": submitted}]}), encoding="utf-8")
    checks = qa.validate(ops, fixture, None, None, {"audit_directory": str(tmp_path)})
    assert all(checks.values()), checks
    assert not any("start" in node or "end" in node for node in draft["nodes"])


@pytest.mark.parametrize("quote", ["原文没有这句话", "资料室"])
def test_missing_or_ambiguous_quote_is_rejected_atomically_without_preflight(sample, quote):
    ops, fixture, draft, _qa = sample
    bad = copy.deepcopy(draft)
    bad["nodes"][6]["quotes"] = [quote]
    before, properties = ops.graph_snapshot(), ops.property_records()
    result = ops.submit_extraction(fixture["source_id"], bad)
    assert result["status"] == "staged" and not result["valid"] and result["diagnostics"]
    assert ops.graph_snapshot() == before and ops.property_records() == properties
    assert bad["nodes"][6]["quotes"] == [quote]


def test_default_guidance_delegates_string_matching_but_keeps_semantic_review():
    system = DEFAULT_PROMPTS["system"]
    assert "不是必须顺序执行的检查清单" in system["text"]
    assert "目的已达到就自然结束" in system["text"]
    assert "入库账目由程序" in system["text"]
    assert "不为清零info或建议开启审核循环" in system["text"]
    assert "全部工程工具保持可用" in system["text"]
    for key in ("extraction", "source_extraction", "review", "action_extraction"):
        text = DEFAULT_PROMPTS[key]["text"]
        assert "locate_source" in text and "submit_extraction" in text
        assert "语义" in text and "定位" in text
        assert "accepted" in text and "无error" in text and "info" in text
        assert "具体" in text and "明确" in text and "复核" in text
    assert "不必代替程序逐引文搜索比对" in DEFAULT_PROMPTS["extraction"]["text"]
    assert "不能省略语义证据核对" in DEFAULT_PROMPTS["extraction"]["text"]
    assert "locate_source" in TOOLS and not TOOLS["locate_source"].writes
    assert "preview_extraction" in TOOLS and not TOOLS["preview_extraction"].writes
