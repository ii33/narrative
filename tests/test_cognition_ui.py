"""Explicit subjective records render for holder and subject without replacing facts."""
from test_browse_detail_sync import choose
from test_chat_scroll import transcript_shell
from test_static_check_ui import plain

from narrative.assistant import _first_prompt
from narrative.extraction_draft import RESULT_SCHEMA
from narrative.operations import Operations


def texts(item):
    result = [str(item.property("text"))] if item.property("text") is not None else []
    for child in item.childItems():
        result.extend(texts(child))
    return result


def test_scope_purpose_and_holder_contract_are_explicit():
    prompt = _first_prompt("复核")
    assert "世界事实与人物所知、所信、意图和言说分别记录" in prompt
    assert "认知或主张注明认知者、说话人" in prompt
    assert "不按对白形式机械决定scope" in prompt
    fields = RESULT_SCHEMA["properties"]["properties"]["items"]["properties"]
    assert "cognition为某人物所知所信" in fields["scope"]["description"]
    assert "被认知/被谈论的主体" in fields["holder"]["description"]
    assert "甲以为乙是教师：node乙、scope=cognition、holder甲" in fields["holder"]["description"]


def test_real_scope_records_reach_holder_and_subject_panels_without_overwriting_world(tmp_path, monkeypatch):
    text = "乙身体安好。甲以为乙受伤。丙说乙疲惫。"
    quotes = ["乙身体安好。", "甲以为乙受伤。", "丙说乙疲惫。"]
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        sid = ops.import_source("认知与言说", text)
        payload = {"nodes": [
            {"type": "person", "name": "甲", "quotes": [quotes[1]]},
            {"type": "person", "name": "乙", "quotes": [quotes[0]]},
            {"type": "person", "name": "丙", "quotes": [quotes[2]]},
            {"type": "big_event", "name": "身体状态的不同认识", "quotes": [text]},
            {"type": "scene", "name": "观察与谈论", "big_event": "@4", "quotes": [text]},
        ], "properties": []}
        for quote in quotes:
            payload["nodes"].append({"type": "event", "name": quote[:-1], "scene": "@5", "quotes": [quote]})
        for scope, holder, value, index in [("world", None, "安好", 0), ("cognition", "@1", "受伤", 1), ("claim", "@3", "疲惫", 2)]:
            row = {"node": "@2", "property": "身体状态", "value": value, "value_type": "string", "scope": scope,
                   "at_event": f"@{6+index}", "quotes": [quotes[index]]}
            if holder:
                row["holder"] = holder
            payload["properties"].append(row)
        result = ops.submit_extraction(sid, payload)
        assert result["status"] == "accepted", result
        ids = result["node_ids"]
        before = ops.property_records()
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "browse")
        page = ui.window.findChild(object, "browsePage")
        page.load()
        ui.wait(lambda: not ui.window.property("busy"))
        ui.settle()
        for ref, expected_scope, expected_value in [("@1", "cognition", "受伤"), ("@3", "claim", "疲惫")]:
            choose(ui, ids[ref])
            ui.wait(lambda: bool(plain(page.property("claimRows"))))
            ui.wait(lambda: not ui.window.property("busy"))
            claims = plain(page.property("claimRows"))
            assert [(r["node_id"], r["scope"], r["value"]) for r in claims] == [(ids["@2"], expected_scope, expected_value)]
            rendered = "\n".join(texts(page))
            assert "他说的 / 他以为的" in rendered and expected_value in rendered
        choose(ui, ids["@2"])
        ui.wait(lambda: len(plain(page.property("heardRows"))) == 2)
        ui.wait(lambda: not ui.window.property("busy"))
        heard = plain(page.property("heardRows"))
        assert {(r["holder_id"], r["scope"], r["value"]) for r in heard} == {(ids["@1"], "cognition", "受伤"), (ids["@3"], "claim", "疲惫")}
        assert any(row["valueText"] == "安好" for row in plain(page.property("propertyRows")))
        assert not any(row["valueText"] in {"受伤", "疲惫"} for row in plain(page.property("propertyRows")))
        rendered = "\n".join(texts(page))
        assert "别人说的" in rendered and "受伤" in rendered and "疲惫" in rendered
        assert ops.property_records() == before
