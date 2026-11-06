"""Human scene-description blocks stay separate from actions without rewriting prose."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import create_fixture, dialog_action, render_ready, visuals
from test_static_check_ui import click, plain


def test_scene_description_actual_type_switch_create_and_independent_format(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        sid = create_fixture(ops)
        segment = ops.read_screenplay(sid)["segments"][0]
        segment = ops.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {"plan": {"title": "桥面", "summary": "桥头出现来人。"}}}], segment["outline_revision"], reason="场景描写UI")
        unit = segment["units"][0]
        prose = "晨雾笼罩石桥，远山隐入天际。"
        unit = ops.apply_script_block_changes(unit["id"], [{"op": "insert", "block": {"type": "action", "payload": {"text": prose}}}], [], unit["body_revision"], reason="保留既有动作原句")
        old = unit["blocks"][0]
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        page = ui.window.findChild(object, "screenplayPage")
        QMetaObject.invokeMethod(page, "selectScreenplay", Q_ARG("QVariant", sid))
        ui.wait(lambda: plain(page.property("unit")).get("id") == unit["id"])
        assert ops.read_script_segment(segment["id"])["units"][0]["blocks"][0]["type"] == "action"
        editor = page.findChild(object, "scriptBlockEditor")
        QMetaObject.invokeMethod(editor, "openFor", Q_ARG("QVariant", old))
        ui.wait(lambda: editor.property("opened"))
        types = page.findChild(object, "scriptBlockType")
        kinds = plain(types.property("values"))
        types.setProperty("currentIndex", kinds.index("scene_description"))
        assert page.findChild(object, "scriptBlockText").property("text") == prose
        click(ui, dialog_action(ui, page, "scriptBlockEditor", "scriptBlockSave"))
        ui.wait(lambda: plain(page.property("unit")).get("blocks", [{}])[0].get("type") == "scene_description")
        changed = ops.read_script_segment(segment["id"])["units"][0]["blocks"][0]
        assert changed["id"] == old["id"] and changed["payload"]["text"] == prose
        ui.wait(lambda: not editor.property("visible"))
        render_ready(ui)
        click(ui, page.findChild(object, "scriptAddBlock"))
        ui.wait(lambda: editor.property("opened"))
        assert kinds[types.property("currentIndex")] == "action"  # No automatic reclassification.
        types.setProperty("currentIndex", kinds.index("scene_description"))
        page.findChild(object, "scriptBlockText").setProperty("text", "桥下水声清晰，岸边芦苇轻摇。")
        click(ui, dialog_action(ui, page, "scriptBlockEditor", "scriptBlockSave"))
        ui.wait(lambda: len(plain(page.property("unit")).get("blocks", [])) == 2)
        assert all(b["type"] == "scene_description" for b in ops.read_script_segment(segment["id"])["units"][0]["blocks"])
        assert plain(page.property("blockLabels"))["scene_description"] == "场景描写"
        ui.wait(lambda: not editor.property("visible"))
        render_ready(ui)
        click(ui, page.findChild(object, "screenplayExport"))
        ui.wait(lambda: page.findChild(object, "scriptExportDialog").property("opened"))
        click(ui, page.findChild(object, "scriptCompileSettingsOpen"))
        settings = page.findChild(object, "scriptCompileSettings")
        ui.wait(lambda: settings.property("opened") and settings.property("loaded"))
        assert "scene_description" in plain(settings.property("kinds"))
        field = next(item for item in visuals(settings.property("contentItem")) if item.objectName() == "scriptCompile_scene_description_indent_left_mm")
        field.setProperty("text", "8")
        click(ui, dialog_action(ui, page, "scriptCompileSettings", "scriptCompileSettingsSave"))
        ui.wait(lambda: ops.load_compile_settings()["settings"]["paragraphs"]["scene_description"]["indent_left_mm"] == 8)
        assert ops.load_compile_settings()["settings"]["paragraphs"]["action"]["indent_left_mm"] == 0
        assert not ui.errors, ui.errors
