"""The actual QML graph respects explicit symmetry without inventing reverse facts."""
from __future__ import annotations

import pytest
from test_chat_scroll import transcript_shell
from test_static_check_ui import plain


@pytest.mark.parametrize("symmetric,expected", [(True, True), (False, False), (None, False)])
def test_single_relation_uses_only_explicit_symmetric_definition(tmp_path, monkeypatch, symmetric, expected):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.window.setProperty("currentPage", "relations")
        ui.settle()
        page = ui.window.findChild(object, "relationPage")
        edge = {"from": "P1", "to": "P2", "kind": "合成关系"}
        if symmetric is not None:
            edge["symmetric"] = symmetric
        page.apply({"persons": [{"id": "P1", "name": "甲"}, {"id": "P2", "name": "乙"}], "edges": [edge]})
        ui.wait(lambda: page.property("labelGeometryReady"))
        drawn = plain(page.links())
        assert len(drawn) == 1 and drawn[0]["both"] is expected
        assert drawn[0]["from"] == "P1" and drawn[0]["to"] == "P2"
        assert plain(page.property("graphLabelLayout"))["edges"][0]["edge"]["both"] is expected
        assert plain(page.property("edges")) == [edge]


@pytest.mark.parametrize("different", [None, "state", "scope", "holder_id", "context_id"])
def test_reverse_rows_merge_only_with_matching_relation_context(tmp_path, monkeypatch, different):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.window.setProperty("currentPage", "relations")
        ui.settle()
        page = ui.window.findChild(object, "relationPage")
        first = {"from": "P1", "to": "P2", "kind": "合成关系", "symmetric": False}
        second = dict(first, **{"from": "P2", "to": "P1"})
        if different:
            second[different] = {"state": "旧态", "scope": "claim", "holder_id": "P1", "context_id": "branch"}[different]
        page.apply({"persons": [{"id": "P1", "name": "甲"}, {"id": "P2", "name": "乙"}], "edges": [first, second]})
        ui.wait(lambda: page.property("labelGeometryReady"))
        drawn = plain(page.links())
        assert len(drawn) == (2 if different else 1)
        assert all(row["both"] is (different is None) for row in drawn)
        assert plain(page.property("edges")) == [first, second]
