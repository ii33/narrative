"""Real QML screenplay workbench: range, scene table, body, structure and export."""
from __future__ import annotations

import json
import os
from pathlib import Path

from test_chat_scroll import transcript_shell
from test_static_check_ui import click, plain


def visuals(item):
    yield item
    for child in item.childItems():
        yield from visuals(child)


def save_qa(ui, tmp_path, name):
    destination = Path(os.environ.get("NARRATIVE_SCREENPLAY_QA_DIR") or tmp_path)
    destination.mkdir(parents=True, exist_ok=True)
    ui.window.grabWindow().save(str(destination / name))


def dialog_action(ui, page, dialog_name, button_name):
    """Wait for the actual popup transition, then expose its real form action."""
    from PySide6.QtCore import QPointF

    dialog = page.findChild(object, dialog_name)
    button = page.findChild(object, button_name)
    ui.wait(lambda: dialog.property("opened") and button.property("enabled")
            and button.width() > 0 and button.height() > 0)
    scrolls = []
    ancestor = button.parentItem()
    while ancestor is not None:
        if ancestor.metaObject().indexOfProperty("contentY") >= 0:
            scrolls.append(ancestor)
        ancestor = ancestor.parentItem()
    for scroll in scrolls:
        point = button.mapToItem(scroll, QPointF(button.width() / 2, button.height() / 2))
        delta = max(0, point.y() + button.height() / 2 - scroll.height())
        if point.y() - button.height() / 2 < 0:
            delta = point.y() - button.height() / 2
        maximum = max(0, scroll.property("contentHeight") - scroll.height())
        scroll.setProperty("contentY", max(0, min(maximum, scroll.property("contentY") + delta)))

    def clickable():
        center = QPointF(button.width() / 2, button.height() / 2)
        scene = button.mapToScene(center)
        return (dialog.property("opened") and button.property("visible")
                and 0 <= scene.x() < ui.window.width() and 0 <= scene.y() < ui.window.height()
                and all(0 <= button.mapToItem(scroll, center).y() <= scroll.height()
                        for scroll in scrolls))

    ui.wait(clickable)
    return button


def render_ready(ui):
    """Observe a rendered frame after a data-driven panel has changed size."""
    import threading

    rendered = threading.Event()
    ui.window.frameSwapped.connect(rendered.set)
    try:
        ui.window.update()
        ui.wait(rendered.is_set)
    finally:
        ui.window.frameSwapped.disconnect(rendered.set)


def test_screenplay_manual_workflow_repeated_body_edit_and_export(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from PySide6.QtGui import QFont, QFontDatabase

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
        if font_id >= 0:
            ui.app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
        ops = Operations.open(ui.bridge._project_path)
        snap = ops.edit_adaptation_node(changes={"node_type": "scene", "name": "渡桥"})
        scene_id = snap["nodes"][0]["id"]
        ops.edit_adaptation_node(changes={"node_type": "event", "name": "甲上桥"}, parent_id=scene_id)
        ops.edit_adaptation_node(changes={"node_type": "event", "name": "桥上传来呼唤"}, parent_id=scene_id)
        original = ops.graph_snapshot()
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        page = ui.window.findChild(object, "screenplayPage")
        ui.wait(lambda: "screenplays" in plain(page.property("workspace")))
        click(ui, page.findChild(object, "screenplayCreate"))
        ui.wait(lambda: page.findChild(object, "scriptCreateDialog").property("opened"))
        page.findChild(object, "scriptCreateTitle").setProperty("text", "桥上")
        click(ui, dialog_action(ui, page, "scriptCreateDialog", "scriptCreateSave"))
        ui.wait(lambda: plain(page.property("screenplay")).get("title") == "桥上")
        ui.wait(lambda: not page.findChild(object, "scriptCreateDialog").property("visible"))
        assert plain(page.property("workspace"))["timeline"]["nodes"] == []
        click(ui, page.findChild(object, "scriptTimelineImport"))
        ui.wait(lambda: page.findChild(object, "scriptTimelineImportDialog").property("opened"))
        choices = [b for b in visuals(page.findChild(object, "scriptTimelineImportDialog").property("contentItem"))
                   if b.objectName() == "scriptTimelineImportChoice"]
        click(ui, next(b for b in choices if b.property("nodeId") == scene_id))
        event_choice = next(b for b in choices if b.property("nodeId") != scene_id)
        click(ui, event_choice)
        assert len(plain(page.property("timelineImportSelection"))) == 2
        click(ui, page.findChild(object, "scriptTimelineImportConfirm"))
        ui.wait(lambda: len(plain(page.property("workspace"))["timeline"]["nodes"]) == 3)
        ui.wait(lambda: not page.findChild(object, "scriptTimelineImportDialog").property("visible"))
        picker = page.findChild(object, "scriptRangePicker")
        ui.settle()
        starts = [b for b in visuals(picker) if b.objectName() == "scriptRangeStart"]
        assert starts, (plain(picker.property("rows")), [(c.objectName(), c.metaObject().className()) for c in picker.children()])
        ends = [b for b in visuals(picker) if b.objectName() == "scriptRangeEnd"]
        click(ui, next(b for b in starts if b.property("nodeId") == scene_id))
        click(ui, next(b for b in ends if b.property("nodeId") == scene_id))
        ui.wait(lambda: plain(page.property("rangePreview")).get("status") == "valid")
        page.findChild(object, "scriptSegmentTitle").setProperty("text", "桥上相遇")
        click(ui, page.findChild(object, "scriptCreateSegment"))
        ui.wait(lambda: len(plain(page.property("segments"))) == 1)
        segment_id = page.property("selectedSegmentId")
        render_ready(ui)
        click(ui, page.findChild(object, "scriptAddUnit"))
        ui.wait(lambda: page.findChild(object, "scriptPlanEditor").property("opened"))
        page.findChild(object, "scriptPlanTitle").setProperty("text", "桥头呼唤")
        page.findChild(object, "scriptPlanSummary").setProperty("text", "甲走到桥头，听见身后有人呼唤。")
        click(ui, dialog_action(ui, page, "scriptPlanEditor", "scriptPlanSave"))
        ui.wait(lambda: len(plain(page.property("units"))) == 1)
        ui.wait(lambda: not page.findChild(object, "scriptPlanEditor").property("visible"))
        unit_id = plain(page.property("units"))[0]["id"]
        assert page.property("selectedUnitId") == unit_id
        # Switch through the same scene-table row into its body.
        click(ui, next(b for b in visuals(page) if b.objectName() == "scriptOpenBody"))
        click(ui, page.findChild(object, "scriptAddBlock"))
        ui.wait(lambda: page.findChild(object, "scriptBlockEditor").property("opened"))
        page.findChild(object, "scriptBlockText").setProperty("text", "甲停下脚步。")
        click(ui, dialog_action(ui, page, "scriptBlockEditor", "scriptBlockSave"))
        ui.wait(lambda: len(plain(page.property("unit")).get("blocks", [])) == 1)
        ui.wait(lambda: not page.findChild(object, "scriptBlockEditor").property("visible"))
        block_id = plain(page.property("unit"))["blocks"][0]["id"]
        block_editor = page.findChild(object, "scriptBlockEditor")
        for text in ("甲缓缓停下脚步。", "甲停步，回头。"):
            block = plain(page.property("unit"))["blocks"][0]
            assert QMetaObject.invokeMethod(block_editor, "openFor", Q_ARG("QVariant", block))
            ui.wait(lambda: block_editor.property("opened"))
            page.findChild(object, "scriptBlockText").setProperty("text", text)
            click(ui, dialog_action(ui, page, "scriptBlockEditor", "scriptBlockSave"))
            ui.wait(lambda text=text: plain(page.property("unit"))["blocks"][0]["payload"]["text"] == text)
            ui.wait(lambda: not block_editor.property("visible"))
        assert plain(page.property("unit"))["blocks"][0]["id"] == block_id
        click(ui, page.findChild(object, "scriptAdoptOutline"))
        ui.wait(lambda: plain(page.property("segment")).get("outline_state") == "adopted")
        # Editing a plan after adoption keeps this same unit and its body.
        plan_editor = page.findChild(object, "scriptPlanEditor")
        assert QMetaObject.invokeMethod(plan_editor, "openFor", Q_ARG("QVariant", plain(page.property("unit"))))
        ui.wait(lambda: plan_editor.property("opened"))
        page.findChild(object, "scriptPlanSummary").setProperty("text", "甲走到桥头，停步后回望声音传来的方向。")
        click(ui, dialog_action(ui, page, "scriptPlanEditor", "scriptPlanSave"))
        ui.wait(lambda: plain(page.property("segment")).get("outline_state") == "changed")
        ui.wait(lambda: not plan_editor.property("visible"))
        assert page.property("selectedUnitId") == unit_id
        assert plain(page.property("unit"))["blocks"][0]["id"] == block_id
        # A second picker trip appends new source nodes without replacing this
        # edited body, the local copy, the current range, or the folded columns.
        prior_units = ops.read_script_segment(segment_id)["units"]
        prior_timeline = ops.read_screenplay_timeline(screenplay_id := plain(page.property("screenplay"))["id"])
        prior_names = {node["id"]: node["name"] for node in prior_timeline["nodes"]}
        prior_range = (picker.property("startId"), picker.property("endId"))
        source = ops.adaptation_snapshot()
        old_event_id = next(node["id"] for node in source["nodes"] if node["node_type"] == "event")
        ops.edit_adaptation_node(old_event_id, {"name": "源改编中的新名字"})
        added = ops.edit_adaptation_node(changes={"node_type": "event", "name": "乙走上桥"}, parent_id=scene_id)
        new_event_id = next(node["id"] for node in added["nodes"] if node["name"] == "乙走上桥")
        for name in ("Segment", "Scene"):
            click(ui, page.findChild(object, "script" + name + "ColumnToggle"))
        assert page.findChild(object, "scriptTimelineImport").property("text") == "追加导入时间点"
        click(ui, page.findChild(object, "scriptTimelineImport"))
        import_dialog = page.findChild(object, "scriptTimelineImportDialog")
        ui.wait(lambda: import_dialog.property("opened"))
        assert import_dialog.property("title") == "追加导入时间点"
        choices = [item for item in visuals(import_dialog.property("contentItem")) if item.objectName() == "scriptTimelineImportChoice"]
        old_choice = next(item for item in choices if item.property("nodeId") == scene_id)
        assert "已导入" in old_choice.property("text")
        assert old_choice.property("enabled")
        click(ui, old_choice)
        click(ui, next(item for item in choices if item.property("nodeId") == new_event_id))
        save_qa(ui, tmp_path, "screenplay-append-import.png")
        click(ui, page.findChild(object, "scriptTimelineImportConfirm"))
        ui.wait(lambda: any(node["id"] == new_event_id for node in plain(page.property("workspace"))["timeline"]["nodes"]))
        ui.wait(lambda: len(plain(page.property("rangePreview")).get("effective_node_ids", [])) == 3)
        assert (picker.property("startId"), picker.property("endId")) == prior_range
        assert page.property("selectedSegmentId") == segment_id
        assert page.property("selectedUnitId") == unit_id
        assert ops.read_script_segment(segment_id)["units"] == prior_units
        assert {node["id"]: node["name"] for node in ops.read_screenplay_timeline(screenplay_id)["nodes"] if node["id"] in prior_names} == prior_names
        assert all(page.findChild(object, "script" + name + "Column").property("collapsed") for name in ("Segment", "Scene"))
        for name in ("Segment", "Scene"):
            click(ui, page.findChild(object, "script" + name + "ColumnToggle"))
        ui.settle()
        save_qa(ui, tmp_path, "screenplay-workbench.png")
        click(ui, page.findChild(object, "screenplayExport"))
        export_dialog = page.findChild(object, "scriptExportDialog")
        ui.wait(lambda: export_dialog.property("visible"))
        assert "甲停步，回头。" in page.findChild(object, "scriptExportText").property("text")
        screenplay_id = plain(page.property("screenplay"))["id"]
        export_path = tmp_path / "screenplay.txt"
        ui.bridge.saveScreenplayExport(screenplay_id, "text", str(export_path))
        ui.wait(lambda: export_path.exists())
        assert "甲停步，回头。" in export_path.read_text(encoding="utf-8")
        assert ops.read_script_segment(segment_id)["units"][0]["id"] == unit_id
        assert ops.graph_snapshot() == original
        export_dialog.close()
        ui.wait(lambda: not export_dialog.property("visible"))
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        display = next(b for b in visuals(page) if b.objectName() == "scriptBlockDisplayText")
        click(ui, display)
        QTest.keyClick(ui.window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        assert display.property("selectedText") == "甲停步，回头。"
        # Closing/reopening the actual project restores the same current objects.
        from narrative.project_package import layout

        entry = layout(ops.project_info().path).entry
        ui.bridge.closeProject()
        ui.wait(lambda: ui.window.property("mode") == "launcher")
        ui.bridge.openProject(str(entry))
        ui.wait(lambda: ui.window.property("mode") == "workspace")
        ui.window.setProperty("currentPage", "screenplay")
        ui.wait(lambda: plain(page.property("screenplay")).get("id") == screenplay_id)
        ui.wait(lambda: plain(page.property("unit")).get("id") == unit_id)
        assert plain(page.property("unit"))["blocks"][0]["id"] == block_id
        assert not ui.errors





def create_fixture(ops):
    snap = ops.edit_adaptation_node(changes={"node_type": "scene", "name": "桥上相遇"})
    scene_id = snap["nodes"][0]["id"]
    ops.edit_adaptation_node(changes={"node_type": "event", "name": "渡桥"}, parent_id=scene_id)
    document = ops.create_screenplay("桥上", reason="UI隔离测试")
    screenplay_id = document["screenplay"]["id"]
    ops.import_screenplay_timeline(screenplay_id, [scene_id],
        expected_timeline_revision=ops.read_screenplay_timeline(screenplay_id)["revision"],
        expected_adaptation_revision=ops.adaptation_snapshot()["revision"], reason="UI导入副本")
    span = ops.create_adaptation_span(screenplay_id, "桥上范围",
        selection_ids=[scene_id], expected_timeline_revision=ops.read_screenplay_timeline(screenplay_id)["revision"], reason="UI范围测试")
    for title in ("起步", "相遇"):
        doc = ops.read_screenplay(screenplay_id)
        ops.create_script_segment(screenplay_id, span["span"]["id"], title, doc["screenplay"]["structure_revision"], reason="UI段落测试")
    return screenplay_id


def test_structure_page_real_drag_move_mode_reorganization_and_json_export(tmp_path, monkeypatch):
    import threading

    from PySide6.QtCore import Q_ARG, QMetaObject, QPointF, Qt
    from PySide6.QtTest import QTest

    from narrative.graph_operations import GraphOperations
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        screenplay_id = create_fixture(ops)
        original = ops.graph_snapshot()
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay_structure")
        page = ui.window.findChild(object, "screenplayStructurePage")
        ui.wait(lambda: len(plain(page.property("structureRows"))) == 2)
        ui.settle()
        page.findChild(object, "scriptStructureTree").forceLayout()
        initial_order = ops.read_screenplay(screenplay_id)["segment_order"]
        initial_revision = plain(page.property("screenplay"))["structure_revision"]
        committed, release_result = threading.Event(), threading.Event()
        original_apply = GraphOperations.apply_screenplay_structure
        hold_once = {"pending": True}

        def held_result(self, *args, **kwargs):
            result = original_apply(self, *args, **kwargs)
            if hold_once["pending"]:
                hold_once["pending"] = False
                committed.set()
                assert release_result.wait(5)
            return result

        monkeypatch.setattr(GraphOperations, "apply_screenplay_structure", held_result)
        handles = sorted((b for b in visuals(page) if b.objectName() == "scriptStructureDragHandle"), key=lambda b: b.mapToScene(QPointF(0, 0)).y())
        start = handles[0].mapToScene(QPointF(40, handles[0].height() / 2)).toPoint()
        end = handles[1].mapToScene(QPointF(40, handles[1].height() / 2)).toPoint()
        QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(ui.window, end, 100)
        QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        ui.wait(committed.is_set)
        try:
            assert ops.read_screenplay(screenplay_id)["segment_order"] == list(reversed(initial_order))
            assert page.property("structureBusy")
            assert not page.findChild(object, "scriptAddAct").property("enabled")
        finally:
            release_result.set()
        ui.wait(lambda: plain(page.property("doc")).get("segment_order") == list(reversed(initial_order))
                and plain(page.property("screenplay"))["structure_revision"] > initial_revision
                and not page.property("structureBusy"))
        click(ui, page.findChild(object, "scriptAddAct"))
        dialog = page.findChild(object, "scriptStructureEditor")
        ui.wait(lambda: dialog.property("opened") and page.findChild(object, "scriptStructureSave").property("enabled"))
        page.findChild(object, "scriptStructureTitle").setProperty("text", "第一幕")
        click(ui, page.findChild(object, "scriptStructureSave"))
        ui.wait(lambda: len(plain(page.property("doc")).get("acts", [])) == 1)
        segment = plain(page.property("segments"))[0]
        act_id = plain(page.property("doc"))["acts"][0]["id"]
        assert QMetaObject.invokeMethod(dialog, "openFor", Q_ARG("QVariant", "segment"), Q_ARG("QVariant", segment))
        ui.wait(lambda: dialog.property("opened"))
        dialog.findChild(object, "scriptStructureParent").setProperty("currentIndex", 1)
        click(ui, dialog.findChild(object, "scriptStructureSave"))
        ui.wait(lambda: any(e["child_id"] == segment["id"] and e["parent_id"] == act_id for e in ops.read_screenplay(screenplay_id)["structure_entries"]))
        ui.wait(lambda: any(row["depth"] == 1 for row in plain(page.property("structureRows"))))
        ui.settle()
        save_qa(ui, tmp_path, "screenplay-structure.png")
        stable_order = ops.read_screenplay(screenplay_id)["segment_order"]
        assert QMetaObject.invokeMethod(page, "changeFormat")
        ui.wait(lambda: plain(page.property("screenplay")).get("format") == "series")
        assert len(ops.read_screenplay(screenplay_id)["episodes"]) == 1
        assert ops.read_screenplay(screenplay_id)["segment_order"] == stable_order
        assert QMetaObject.invokeMethod(page, "changeFormat")
        ui.wait(lambda: plain(page.property("screenplay")).get("format") == "single")
        assert ops.read_screenplay(screenplay_id)["episodes"] == []
        assert ops.read_screenplay(screenplay_id)["segment_order"] == stable_order
        ui.window.setProperty("currentPage", "screenplay")
        work = ui.window.findChild(object, "screenplayPage")
        ui.wait(lambda: len(plain(work.property("segments"))) == 2)
        assert [s["id"] for s in plain(work.property("segments"))] == stable_order
        path = tmp_path / "screenplay.json"
        ui.bridge.saveScreenplayExport(screenplay_id, "json", str(path))
        ui.wait(lambda: path.exists())
        exported = json.loads(path.read_text(encoding="utf-8"))
        assert exported["segment_order"] == stable_order
        assert ops.graph_snapshot() == original
        assert not ui.errors


def test_writing_shortcut_freezes_target_and_current_chat_before_background_prepare(tmp_path, monkeypatch):
    import threading

    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.graph_operations import GraphOperations
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        screenplay_id = create_fixture(ops)
        segment = ops.read_screenplay(screenplay_id)["segments"][0]
        segment = ops.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {"plan": {"summary": "甲渡桥。"}}}], segment["outline_revision"], reason="目标测试场")
        unit_id = segment["units"][0]["id"]
        entered, release = threading.Event(), threading.Event()
        prepare = GraphOperations.prepare_script_writing_task
        captured = []

        def blocked_prepare(self, target, prompt=""):
            result = prepare(self, target, prompt=prompt)
            entered.set()
            assert release.wait(5)
            return result

        def capture(text, image, chat, *, writing_task=None):
            captured.append({"text": text, "chat": chat, "task": writing_task})

        monkeypatch.setattr(GraphOperations, "prepare_script_writing_task", blocked_prepare)
        monkeypatch.setattr(ui.bridge, "_assistant_turn", capture)
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        page = ui.window.findChild(object, "screenplayPage")
        ui.wait(lambda: plain(page.property("screenplay")).get("id") == screenplay_id)
        original_chat = ui.a["id"]
        ui.bridge._assistant_chat = original_chat
        assert QMetaObject.invokeMethod(page, "openWriting", Q_ARG("QVariant", "unit"), Q_ARG("QVariant", [unit_id]), Q_ARG("QVariant", "只改目标场。"))
        ui.settle()
        assert plain(page.property("writingTarget"))["ids"] == [unit_id]
        assert unit_id in page.findChild(object, "scriptWritingTargetIds").property("text")
        assert page.findChild(object, "scriptWritingSend").property("enabled")
        click(ui, page.findChild(object, "scriptWritingSend"))
        ui.wait(entered.is_set)
        ui.bridge._assistant_chat = ui.b["id"]
        page.setProperty("writingTarget", {"kind": "unit", "ids": ["changed_ui_target"]})
        release.set()
        ui.wait(lambda: len(captured) == 1)
        assert captured[0]["chat"] == original_chat
        assert captured[0]["task"]["target"]["ids"] == [unit_id]
        assert captured[0]["task"]["target"]["include_existing"] is True
        assert captured[0]["task"]["unit_ids"] == [unit_id]
        assert not ui.errors



def test_four_columns_independently_collapse_resize_and_keep_selection(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject, QPointF, Qt
    from PySide6.QtTest import QTest

    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        screenplay_id = create_fixture(ops)
        segment = ops.read_screenplay(screenplay_id)["segments"][0]
        segment = ops.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {"plan": {"title": "桥头相遇", "summary": "甲在桥头停步，回望呼唤他的乙。"}}}], segment["outline_revision"], reason="四栏测试场表")
        unit_id = segment["units"][0]["id"]
        ops.apply_script_block_changes(unit_id, [{"op": "insert", "block": {"type": "action", "payload": {"text": "甲停步，回头。"}}}], [], 0, reason="四栏测试正文")
        source_before = ops.graph_snapshot()
        timeline_before = ops.read_screenplay_timeline(screenplay_id)
        document_before = ops.read_screenplay(screenplay_id)
        ui.window.setWidth(1800)
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        page = ui.window.findChild(object, "screenplayPage")
        ui.wait(lambda: plain(page.property("unit")).get("id") == unit_id)
        ui.settle()
        columns = [page.findChild(object, "script" + name + "Column") for name in ("Range", "Segment", "Scene", "Body")]
        assert all(not column.property("collapsed") for column in columns)
        assert page.findChild(object, "scriptUnitTable").property("visible")
        assert page.findChild(object, "scriptBodyBlocks").property("visible")
        for column in columns:
            button = page.findChild(object, column.objectName() + "Toggle")
            click(ui, button)
            ui.wait(lambda column=column: column.property("collapsed"))
            assert column.width() <= 43, (column.objectName(), column.width(), [(c.objectName(), c.width(), c.property("collapsed")) for c in columns])
            assert all(not other.property("collapsed") for other in columns if other is not column)
            assert page.property("selectedSegmentId") == segment["id"]
            assert page.property("selectedUnitId") == unit_id
            click(ui, button)
            ui.wait(lambda column=column: not column.property("collapsed") and column.width() >= column.property("minimumExpandedWidth"))
        handles = sorted((item for item in visuals(page) if item.objectName() == "scriptWorkbenchHandle"), key=lambda item: item.mapToScene(QPointF(0, 0)).x())
        before_width = columns[0].width()
        point = handles[0].mapToScene(QPointF(handles[0].width() / 2, 100)).toPoint()
        QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        QTest.mouseMove(ui.window, point + QPointF(35, 0).toPoint(), 100)
        QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point + QPointF(35, 0).toPoint())
        ui.settle()
        assert abs(columns[0].width() - before_width) >= 10
        for column in columns:
            click(ui, page.findChild(object, column.objectName() + "Toggle"))
        assert all(column.property("collapsed") for column in columns)
        save_qa(ui, tmp_path, "screenplay-four-columns-collapsed.png")
        for column in reversed(columns):
            click(ui, page.findChild(object, column.objectName() + "Toggle"))
        ui.window.setWidth(1440)
        ui.settle()
        save_qa(ui, tmp_path, "screenplay-four-columns.png")
        for column in columns[:2]:
            click(ui, page.findChild(object, column.objectName() + "Toggle"))
        save_qa(ui, tmp_path, "screenplay-four-columns-two-collapsed.png")
        for column in columns[:2]:
            click(ui, page.findChild(object, column.objectName() + "Toggle"))
        ui.window.setWidth(1100)
        ui.settle()
        viewport = page.findChild(object, "scriptWorkbenchViewport")
        assert viewport.property("contentWidth") > viewport.width()
        assert QMetaObject.invokeMethod(page, "openBody", Q_ARG("QVariant", unit_id))
        ui.wait(lambda: viewport.property("contentX") > 0)
        save_qa(ui, tmp_path, "screenplay-four-columns-narrow.png")
        block_button = page.findChild(object, "scriptAddBlock")
        assert block_button.mapToScene(QPointF(5, 5)).x() < ui.window.width()
        click(ui, block_button)
        assert page.findChild(object, "scriptBlockEditor").property("visible")
        page.findChild(object, "scriptBlockEditor").close()
        assert ops.read_screenplay_timeline(screenplay_id) == timeline_before
        assert ops.read_screenplay(screenplay_id) == document_before
        assert ops.graph_snapshot() == source_before
        assert not ui.errors


def test_import_picker_has_five_levels_and_keeps_an_independent_copy(tmp_path, monkeypatch):
    from PySide6.QtCore import QMetaObject

    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        parent = None
        ids = {}
        for kind, title in (("storyline", "渡桥篇"), ("big_event", "赴约"), ("scene", "桥头"), ("event", "甲停步"), ("action", "甲回头")):
            snapshot = ops.edit_adaptation_node(changes={"node_type": kind, "name": title}, parent_id=parent)
            parent = next(n["id"] for n in snapshot["nodes"] if n["name"] == title)
            ids[kind] = parent
        document = ops.create_screenplay("渡桥剧本", reason="五级导入测试")
        screenplay_id = document["screenplay"]["id"]
        extracted = ops.graph_snapshot()
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        page = ui.window.findChild(object, "screenplayPage")
        ui.wait(lambda: plain(page.property("screenplay")).get("id") == screenplay_id)
        assert plain(page.property("workspace"))["timeline"]["nodes"] == []
        ui.settle()
        click(ui, page.findChild(object, "scriptTimelineImport"))
        dialog = page.findChild(object, "scriptTimelineImportDialog")
        ui.wait(lambda: dialog.property("visible"))
        assert {row["node_type"] for row in plain(page.property("timelineImportCandidates"))} == set(ids)
        assert [row["node_type"] for row in plain(page.property("timelineImportRows"))] == ["storyline", "big_event", "scene", "event", "action"]
        ui.settle()
        choices = [item for item in visuals(dialog.property("contentItem")) if item.objectName() == "scriptTimelineImportChoice"]
        click(ui, next(item for item in choices if item.property("nodeId") == ids["storyline"]))
        click(ui, next(item for item in choices if item.property("nodeId") == ids["action"]))
        assert len(plain(page.property("timelineImportSelection"))) == 2
        save_qa(ui, tmp_path, "screenplay-timeline-import.png")
        click(ui, page.findChild(object, "scriptTimelineImportConfirm"))
        ui.wait(lambda: len(plain(page.property("workspace"))["timeline"]["nodes"]) == 5)
        copied = ops.read_screenplay_timeline(screenplay_id)
        assert copied["timeline_id"] == document["screenplay"]["timeline_id"]
        assert copied["timeline_id"] != ops.adaptation_snapshot().get("timeline_id")
        ops.edit_adaptation_node(ids["event"], {"name": "源改编已改名"})
        assert QMetaObject.invokeMethod(page, "load")
        ui.wait(lambda: any(n["name"] == "源改编已改名" for n in plain(page.property("workspace"))["adaptation"]["nodes"]))
        assert plain(page.property("workspace"))["timeline"] == copied
        assert next(n for n in plain(page.findChild(object, "scriptRangePicker").property("nodes")) if n["id"] == ids["event"])["name"] == "甲停步"
        assert ops.graph_snapshot() == extracted
        assert not ui.errors





def test_switching_independent_timelines_discards_old_ranges_and_import_replies(tmp_path, monkeypatch):
    import threading

    from PySide6.QtCore import QMetaObject, Qt
    from PySide6.QtTest import QTest

    from narrative.graph_operations import GraphOperations
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        first_id = create_fixture(ops)
        source = ops.adaptation_snapshot()
        scene_id = next(n["id"] for n in source["nodes"] if n["node_type"] == "scene")
        event_id = next(n["id"] for n in source["nodes"] if n["node_type"] == "event")
        second = ops.create_screenplay("另一套独立剧本", reason="跨域UI测试")
        second_id = second["screenplay"]["id"]
        ops.import_screenplay_timeline(second_id, [scene_id], 0, source["revision"], reason="复制同一来源")
        for identifier, name in ((first_id, "甲方案事件"), (second_id, "乙方案事件")):
            local = ops.read_screenplay_timeline(identifier)
            ops.edit_screenplay_timeline_node(identifier, event_id, changes={"name": name}, expected_timeline_revision=local["revision"], reason="独立命名")
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        page = ui.window.findChild(object, "screenplayPage")
        ui.wait(lambda: plain(page.property("screenplay")).get("id") == first_id)
        picker = page.findChild(object, "scriptRangePicker")
        picker.setProperty("startId", scene_id)
        picker.setProperty("endId", scene_id)
        assert QMetaObject.invokeMethod(page, "previewRange")
        ui.wait(lambda: plain(page.property("rangePreview")).get("status") == "valid")
        first_timeline = plain(page.property("rangePreview"))["span"]["timeline_id"]
        preview_entered, candidates_entered = threading.Event(), threading.Event()
        release_preview, release_candidates = threading.Event(), threading.Event()
        preview = GraphOperations.preview_adaptation_span
        candidates = GraphOperations.import_screenplay_timeline_candidates

        def delayed_preview(self, screenplay_id, *args, **kwargs):
            result = preview(self, screenplay_id, *args, **kwargs)
            if screenplay_id == first_id:
                preview_entered.set()
                assert release_preview.wait(5)
            return result

        def delayed_candidates(self, screenplay_id):
            result = candidates(self, screenplay_id)
            if screenplay_id == first_id:
                candidates_entered.set()
                assert release_candidates.wait(5)
            return result

        monkeypatch.setattr(GraphOperations, "preview_adaptation_span", delayed_preview)
        monkeypatch.setattr(GraphOperations, "import_screenplay_timeline_candidates", delayed_candidates)
        assert QMetaObject.invokeMethod(page, "previewRange")
        ui.wait(preview_entered.is_set)
        click(ui, page.findChild(object, "scriptTimelineImport"))
        ui.wait(candidates_entered.is_set)
        pending_results = sum(kind == "screenplay" for kind, _ in ui.results)
        try:
            selector = page.findChild(object, "screenplaySelector")
            click(ui, selector)
            QTest.keyClick(ui.window, Qt.Key.Key_End)
            QTest.keyClick(ui.window, Qt.Key.Key_Return)
            ui.wait(lambda: plain(page.property("screenplay")).get("id") == second_id)
            assert picker.property("startId") == picker.property("endId") == ""
            assert plain(page.property("rangePreview")) == {}
            assert not page.findChild(object, "scriptTimelineImportDialog").property("visible")
        finally:
            release_preview.set()
            release_candidates.set()
        ui.wait(lambda: sum(kind == "screenplay" for kind, _ in ui.results) >= pending_results + 2)
        assert plain(page.property("rangePreview")) == {}
        assert not page.findChild(object, "scriptTimelineImportDialog").property("visible")
        assert next(n for n in plain(picker.property("nodes")) if n["id"] == event_id)["name"] == "乙方案事件"
        picker.setProperty("startId", scene_id)
        picker.setProperty("endId", scene_id)
        assert QMetaObject.invokeMethod(page, "previewRange")
        ui.wait(lambda: plain(page.property("rangePreview")).get("status") == "valid")
        assert plain(page.property("rangePreview"))["span"]["timeline_id"] != first_timeline
        assert plain(page.property("rangePreview"))["span"]["timeline_id"] == second["screenplay"]["timeline_id"]
        assert not ui.errors


def test_scene_table_ui_uses_continuous_space_time_and_preserves_optional_plan_fields(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        screenplay_id = create_fixture(ops)
        segment = ops.read_screenplay(screenplay_id)["segments"][0]
        plan = {"title": "原有场次", "summary": "甲停步回头，仍然穿着湿雨衣。", "production_notes": "保留已有场级备注",
                "continuity_requirements": [{"note": "雨衣仍湿"}], "story_time": {"day": 1}}
        segment = ops.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {"plan": plan}}], segment["outline_revision"], reason="场表界面合同测试")
        unit = segment["units"][0]
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        page = ui.window.findChild(object, "screenplayPage")
        ui.wait(lambda: plain(page.property("unit")).get("id") == unit["id"])
        render_ready(ui)
        click(ui, page.findChild(object, "scriptWriteSceneTable"))
        writing_dialog = page.findChild(object, "scriptWritingDialog")
        ui.wait(lambda: writing_dialog.property("opened"))
        prompt = page.findChild(object, "scriptWritingPrompt").property("text")
        assert "同一连续时空默认一场" in prompt
        assert "情绪、招式、胜负、镜头变化不能单独拆场" in prompt
        assert "不生成分镜、特效方案或镜头要求" in prompt
        assert plain(page.property("writingTarget"))["kind"] == "scene_table"
        assert plain(page.property("writingTarget"))["ids"] == [segment["id"]]
        writing_dialog.close()
        ui.wait(lambda: not writing_dialog.property("visible"))
        editor = page.findChild(object, "scriptPlanEditor")
        assert QMetaObject.invokeMethod(editor, "openFor", Q_ARG("QVariant", unit))
        ui.wait(lambda: editor.property("opened"))
        assert "同一连续时空默认一场" in page.findChild(object, "scriptSceneTableGuide").property("text")
        assert page.findChild(object, "scriptPlanProductionNotes").property("text") == plan["production_notes"]
        editor.setProperty("showAdvanced", False)
        page.findChild(object, "scriptPlanTitle").setProperty("text", "只修改场标题")
        click(ui, dialog_action(ui, page, "scriptPlanEditor", "scriptPlanSave"))
        ui.wait(lambda: plain(page.property("unit")).get("plan", {}).get("title") == "只修改场标题")
        updated = ops.read_script_segment(segment["id"])["units"][0]
        expected = dict(unit["plan"]) | {"title": "只修改场标题"}
        assert updated["id"] == unit["id"] and updated["plan"] == expected
        ui.wait(lambda: not editor.property("visible"))
        assert QMetaObject.invokeMethod(editor, "openFor", Q_ARG("QVariant", None))
        ui.wait(lambda: editor.property("opened"))
        assert not editor.property("showAdvanced")
        assert not page.findChild(object, "scriptPlanProductionNotes").property("visible")
        assert page.findChild(object, "scriptPlanProductionNotes").property("text") == ""
        editor.close()
        assert not ui.errors
