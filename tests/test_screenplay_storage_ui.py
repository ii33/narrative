"""Actual QML recovery for an incomplete isolated screenplay storage domain."""
from __future__ import annotations

from PySide6.QtCore import QMetaObject
from test_chat_scroll import transcript_shell
from test_screenplay_ui import create_fixture, dialog_action, render_ready, save_qa
from test_static_check_ui import click, plain


def test_incomplete_screenplay_storage_requires_counted_explicit_reset_then_creates(tmp_path, monkeypatch):
    from narrative.graph_operations import GraphOperations
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        source_id = ops.import_source("保留原文", "甲来到桥头。")
        screenplay_id = create_fixture(ops)
        segment = ops.read_screenplay(screenplay_id)["segments"][0]
        segment = ops.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {
            "plan": {"title": "桥头", "summary": "甲停下脚步。"}}}], segment["outline_revision"], reason="重建隔离样例")
        ops.apply_script_block_changes(segment["units"][0]["id"], [{"op": "insert", "block": {
            "type": "action", "payload": {"text": "须经确认才清除的旧剧本正文。"}}}], [], 0, reason="重建隔离正文")
        ops.save_asset({"category": "material", "title": "应保留的材质", "detail": "重建剧本不可改动"})
        graph_before, adaptation_before = ops.graph_snapshot(), ops.adaptation_snapshot()
        source_before, assets_before = ops.source(source_id), ops.list_assets("material")
        # Deliberate fixture corruption reproduces the missing table. Never use
        # this setup helper against a user project or a production entry point.
        with ops.project.store.connect() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("DROP TABLE script_timelines")
        storage_before = ops.inspect_screenplay_storage()
        assert storage_before["status"] == "incomplete" and storage_before["row_counts"]["script_blocks"] == 1
        original_list = GraphOperations.list_screenplays

        def require_inspection_before_listing(self):
            assert self.inspect_screenplay_storage()["status"] != "incomplete"
            return original_list(self)

        monkeypatch.setattr(GraphOperations, "list_screenplays", require_inspection_before_listing)
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("screenplayId", screenplay_id)
        ui.window.setProperty("currentPage", "screenplay")
        page = ui.window.findChild(object, "screenplayPage")
        ui.wait(lambda: page.property("storageNeedsReset"))
        assert page.findChild(object, "screenplayStorageWarning").property("visible")
        assert "正文块 1" in page.findChild(object, "screenplayStorageCounts").property("text")
        assert not page.findChild(object, "screenplayCreate").property("enabled")
        assert ops.inspect_screenplay_storage() == storage_before
        render_ready(ui)
        save_qa(ui, tmp_path, "screenplay-storage-incomplete.png")
        click(ui, page.findChild(object, "screenplayStorageReset"))
        dialog = page.findChild(object, "screenplayStorageResetDialog")
        ui.wait(lambda: dialog.property("opened"))
        assert not page.findChild(object, "screenplayStorageResetConfirm").property("enabled")
        assert not page.findChild(object, "screenplayStorageDiscard").property("checked")
        click(ui, page.findChild(object, "screenplayStorageResetCancel"))
        ui.wait(lambda: not dialog.property("visible"))
        assert ops.inspect_screenplay_storage() == storage_before
        click(ui, page.findChild(object, "screenplayStorageReset"))
        ui.wait(lambda: dialog.property("opened"))
        click(ui, page.findChild(object, "screenplayStorageDiscard"))
        save_qa(ui, tmp_path, "screenplay-storage-confirmation.png")
        click(ui, dialog_action(ui, page, "screenplayStorageResetDialog", "screenplayStorageResetConfirm"))
        ui.wait(lambda: plain(page.property("storage")).get("status") == "ready")
        ui.wait(lambda: not dialog.property("visible"))
        assert plain(page.property("workspace"))["screenplays"] == []
        assert ui.window.property("screenplayId") == ""
        assert ops.list_screenplays() == []
        assert ops.graph_snapshot() == graph_before
        assert ops.adaptation_snapshot() == adaptation_before
        assert ops.source(source_id) == source_before
        assert ops.list_assets("material") == assets_before
        render_ready(ui)
        click(ui, page.findChild(object, "screenplayCreate"))
        ui.wait(lambda: page.findChild(object, "scriptCreateDialog").property("opened"))
        page.findChild(object, "scriptCreateTitle").setProperty("text", "重建后的剧本")
        click(ui, dialog_action(ui, page, "scriptCreateDialog", "scriptCreateSave"))
        ui.wait(lambda: plain(page.property("screenplay")).get("title") == "重建后的剧本")
        new_id = plain(page.property("screenplay"))["id"]
        assert new_id != screenplay_id
        # An external explicit reset may leave the old selection in the app.
        # Refresh resolves only a valid current ID, rather than reading it.
        ui.window.setProperty("screenplayId", screenplay_id)
        assert QMetaObject.invokeMethod(page, "load")
        ui.wait(lambda: ui.window.property("screenplayId") == new_id)
        assert not ui.errors


def test_explicit_create_can_initialize_an_empty_incomplete_domain(tmp_path, monkeypatch):
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        with ops.project.store.connect(write=True) as db:
            db.execute("CREATE TABLE screenplays(id TEXT PRIMARY KEY,timeline_id TEXT NOT NULL,data TEXT NOT NULL)")
        storage_before = ops.inspect_screenplay_storage()
        assert storage_before["status"] == "incomplete" and storage_before["total_rows"] == 0
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        page = ui.window.findChild(object, "screenplayPage")
        ui.wait(lambda: page.property("storageNeedsReset"))
        assert ops.inspect_screenplay_storage() == storage_before
        assert page.findChild(object, "screenplayCreate").property("enabled")
        render_ready(ui)
        click(ui, page.findChild(object, "screenplayCreate"))
        ui.wait(lambda: page.findChild(object, "scriptCreateDialog").property("opened"))
        page.findChild(object, "scriptCreateTitle").setProperty("text", "空库新剧本")
        click(ui, dialog_action(ui, page, "scriptCreateDialog", "scriptCreateSave"))
        ui.wait(lambda: plain(page.property("screenplay")).get("title") == "空库新剧本")
        assert ops.inspect_screenplay_storage()["status"] == "ready"
        assert not page.property("storageNeedsReset")
        assert not ui.errors
