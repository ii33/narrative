"""Main.qml rollback preview/execute uses physical source suffix semantics."""

from __future__ import annotations

import copy

from test_chat_scroll import transcript_shell
from test_current_state_contract import QUOTES, TEXT, add_value, raw_values, setup
from test_static_check_ui import click, plain


def test_actual_rollback_buttons_preview_readonly_then_delete_suffix_and_refresh_ui(
    tmp_path, monkeypatch
):
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        sid, ids = setup(ops)
        for i in range(3):
            add_value(ops, sid, ids, i)
        before = copy.deepcopy(ops.graph_snapshot())
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "static_check")
        ui.settle()
        page = ui.window.findChild(object, "staticCheckPage")
        ui.window.findChild(object, "rollbackSource").setProperty("text", sid)
        ui.window.findChild(object, "rollbackStart").setProperty("text", str(TEXT.index(QUOTES[1])))
        ui.settle()
        click(ui, ui.window.findChild(object, "previewRollbackButton"))
        ui.wait(lambda: bool(plain(page.property("rollbackPlan")).get("valid")))
        plan = plain(page.property("rollbackPlan"))
        assert ops.graph_snapshot() == before
        assert plan["expected_revision"] == before["revision"]
        assert ids[QUOTES[2][:-1]] in plan["deleted_node_ids"]
        assert "v2" in plan["deleted_record_ids"] and "v1" in plan["reopened_record_ids"]
        execute = ui.window.findChild(object, "executeRollbackButton")
        assert execute.property("enabled")
        click(ui, execute)
        ui.wait(lambda: any(kind == "rollback" for kind, _ in ui.results))
        result = next(p for k, p in ui.results if k == "rollback")
        assert result["status"] == "accepted" and result["graph_reset_required"]
        assert set(raw_values(ops)) == {"v0", "v1"}
        assert ops.property_state(ids["甲"], "称呼")["state"]["value"] == "B"
        assert raw_values(ops)["v1"].get("valid_to") is None
        ui.wait(
            lambda: (
                plain(ui.window.property("project"))["revision"]
                == ops.graph_project_info()["revision"]
            )
        )
        assert not execute.property("enabled")


def test_changing_rollback_boundary_invalidates_preview_and_stale_execution_is_atomic(
    tmp_path, monkeypatch
):
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        sid, ids = setup(ops)
        for i in range(3):
            add_value(ops, sid, ids, i)
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "static_check")
        ui.settle()
        page = ui.window.findChild(object, "staticCheckPage")
        ui.window.findChild(object, "rollbackSource").setProperty("text", sid)
        boundary = ui.window.findChild(object, "rollbackStart")
        boundary.setProperty("text", str(TEXT.index(QUOTES[1])))
        ui.settle()
        click(ui, ui.window.findChild(object, "previewRollbackButton"))
        ui.wait(lambda: bool(plain(page.property("rollbackPlan")).get("valid")))
        boundary.setProperty("text", str(TEXT.index(QUOTES[1]) - 1))
        ui.settle()
        assert not ui.window.findChild(object, "executeRollbackButton").property("enabled")
        click(ui, ui.window.findChild(object, "previewRollbackButton"))
        ui.wait(lambda: bool(plain(page.property("rollbackPlan")).get("valid")))
        ops.import_source("预览后另一次修改", "额外原文。")
        before = copy.deepcopy(ops.graph_snapshot())
        click(ui, ui.window.findChild(object, "executeRollbackButton"))
        ui.wait(lambda: not page.property("rollbackBusy"))
        assert ops.graph_snapshot() == before
        assert any(kind == "rollback" for kind, _ in ui.errors)
