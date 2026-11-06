"""Actual QML rendered screenplay preview and format-specific Operations saves."""
from pathlib import Path

from test_chat_scroll import transcript_shell
from test_screenplay_ui import create_fixture, render_ready
from test_static_check_ui import click, plain


def test_screenplay_compile_rendered_preview_and_all_output_formats(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from PySide6.QtGui import QFont, QFontDatabase

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
        if font_id >= 0:
            ui.app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
        ops = Operations.open(ui.bridge._project_path)
        screenplay_id = create_fixture(ops)
        segment = ops.read_screenplay(screenplay_id)["segments"][0]
        segment = ops.apply_scene_table_changes(segment["id"], [{"op": "insert", "unit": {"plan": {"title": "桥头相遇", "summary": "两人在桥头相遇。", "location_text": "石桥", "interior_exterior": "EXT", "time_label": "清晨"}}}], segment["outline_revision"], reason="排版预览隔离测试")
        unit = segment["units"][0]
        blocks = [{"type": "action", "payload": {"text": "晨光落在桥面，来人停住脚步。"}}, {"type": "dialogue", "payload": {"speaker_label": "旅人", "text": "请问，你在等谁？"}}, {"type": "transition", "payload": {"kind": "cut_to_black"}}]
        after = None
        for block in blocks:
            unit = ops.apply_script_block_changes(unit["id"], [{"op": "insert", "block": block, "after_id": after}], [], unit["body_revision"], reason="排版预览正文")
            after = unit["blocks"][-1]["id"]
        before = ops.read_screenplay(screenplay_id)
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        page = ui.window.findChild(object, "screenplayPage")
        assert QMetaObject.invokeMethod(page, "selectScreenplay", Q_ARG("QVariant", screenplay_id))
        ui.wait(lambda: plain(page.property("screenplay")).get("id") == screenplay_id)
        render_ready(ui)
        click(ui, page.findChild(object, "screenplayExport"))
        dialog = page.findChild(object, "scriptExportDialog")
        ui.wait(lambda: dialog.property("opened"))
        preview = page.findChild(object, "scriptExportText")
        assert QMetaObject.invokeMethod(preview, "selectAll")
        selected = preview.property("selectedText")
        assert "旅人" in selected and "请问，你在等谁？" in selected
        assert "晨光落在桥面" in selected and "<html" not in selected and "<table" not in selected
        QMetaObject.invokeMethod(preview, "deselect")
        assert page.property("exportFormat") == "pdf"
        formats = plain(page.property("exportFormats"))
        assert {row["id"] for row in formats} == {"pdf", "docx", "html", "text", "fountain", "json"}
        for row in formats:
            page.setProperty("exportFormat", row["id"])
            assert page.findChild(object, "scriptExportFile").property("defaultSuffix") == row["extension"]
            assert row["extension"] in plain(page.findChild(object, "scriptExportFile").property("nameFilters"))[0]
            destination = tmp_path / ("剧本." + row["extension"])
            ui.bridge.saveScreenplayExport(screenplay_id, row["id"], str(destination))
            ui.wait(lambda destination=destination: destination.exists() and destination.stat().st_size > 0)
            if row["id"] == "pdf":
                assert destination.read_bytes().startswith(b"%PDF")
            elif row["id"] == "docx":
                assert destination.read_bytes().startswith(b"PK")
            else:
                assert "请问，你在等谁？" in destination.read_text(encoding="utf-8")
        page.setProperty("exportFormat", "pdf")
        output = Path("artifacts/screenplay-compile-20261104").resolve()
        output.mkdir(parents=True, exist_ok=True)
        render_ready(ui)
        assert ui.window.grabWindow().save(str(output / "formatted-preview.png"))
        assert ops.read_screenplay(screenplay_id) == before
        assert not ui.errors, ui.errors


def test_compile_settings_actual_save_reopen_defaults_and_project_isolation(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from test_screenplay_ui import dialog_action, visuals

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        original_path = ui.bridge._project_path
        ops = Operations.open(ui.bridge._project_path)
        screenplay_id = create_fixture(ops)
        initial = ops.load_compile_settings()
        before = ops.read_screenplay(screenplay_id)
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        page = ui.window.findChild(object, "screenplayPage")
        QMetaObject.invokeMethod(page, "selectScreenplay", Q_ARG("QVariant", screenplay_id))
        ui.wait(lambda: plain(page.property("screenplay")).get("id") == screenplay_id)
        render_ready(ui)
        click(ui, page.findChild(object, "screenplayExport"))
        ui.wait(lambda: page.findChild(object, "scriptExportDialog").property("opened"))
        click(ui, page.findChild(object, "scriptCompileSettingsOpen"))
        editor = page.findChild(object, "scriptCompileSettings")
        ui.wait(lambda: editor.property("opened") and editor.property("loaded"))
        def field(name):
            return next(item for item in visuals(editor.property("contentItem")) if item.objectName() == name)
        field("scriptCompileFont").setProperty("editText", "Microsoft YaHei")
        field("scriptCompileFontSize").setProperty("text", "15.5")
        field("scriptCompileLineHeight").setProperty("text", "1.8")
        field("scriptCompile_page_margins_mm_left").setProperty("text", "28")
        field("scriptCompile_dialogue_indent_left_mm").setProperty("text", "25")
        field("scriptCompile_action_space_after_pt").setProperty("text", "18")
        field("scriptCompileAlignment_scene").setProperty("currentIndex", 2)
        click(ui, dialog_action(ui, page, "scriptCompileSettings", "scriptCompileSettingsSave"))
        ui.wait(lambda: ops.load_compile_settings()["revision"] == initial["revision"] + 1 and not editor.property("busy"))
        saved = ops.load_compile_settings()
        assert saved["settings"]["font_size_pt"] == 15.5
        assert saved["settings"]["font_family"] == "Microsoft YaHei"
        assert saved["settings"]["page_margins_mm"]["left"] == 28
        assert saved["settings"]["paragraphs"]["scene"]["alignment"] == "right"
        assert saved["settings"]["paragraphs"]["dialogue"]["indent_left_mm"] == 25
        ui.wait(lambda: plain(page.property("exportResult")).get("compile_settings", {}).get("revision") == saved["revision"])
        assert "15.5" in plain(page.property("exportResult"))["preview_html"]
        preview = page.findChild(object, "scriptExportText")
        assert abs(preview.property("leftPadding") - 28 * 96 / 25.4) < 0.01
        assert abs(preview.width() - 210 * 96 / 25.4) < 0.01
        assert abs(preview.property("topPadding") - 25 * 96 / 25.4) < 0.01
        editor.close()
        ui.wait(lambda: not editor.property("visible"))
        click(ui, page.findChild(object, "scriptCompileSettingsOpen"))
        ui.wait(lambda: editor.property("opened") and editor.property("loaded"))
        assert field("scriptCompileFontSize").property("text") == "15.5"
        field("scriptCompileFontSize").setProperty("text", "16")
        saved = ops.save_compile_settings(saved["settings"], saved["revision"], reason="并发排版修改")
        click(ui, dialog_action(ui, page, "scriptCompileSettings", "scriptCompileSettingsSave"))
        ui.wait(lambda: not editor.property("busy") and "已变化" in editor.property("message"))
        assert editor.property("opened") and field("scriptCompileFontSize").property("text") == "16"
        assert ops.load_compile_settings()["settings"] == saved["settings"]
        assert ops.load_compile_settings()["revision"] == saved["revision"]
        editor.close()
        ui.wait(lambda: not editor.property("visible"))
        click(ui, page.findChild(object, "scriptCompileSettingsOpen"))
        ui.wait(lambda: editor.property("opened") and editor.property("loaded"))
        output = Path("artifacts/screenplay-compile-20261104").resolve()
        output.mkdir(parents=True, exist_ok=True)
        render_ready(ui)
        assert ui.window.grabWindow().save(str(output / "format-settings.png"))
        click(ui, page.findChild(object, "scriptCompileDefaults"))
        assert field("scriptCompileFontSize").property("text") == str(initial["defaults"]["font_size_pt"]).removesuffix(".0")
        assert ops.load_compile_settings()["settings"] == saved["settings"]
        assert ops.load_compile_settings()["revision"] == saved["revision"]  # Defaults are a draft until explicitly saved.
        editor.close()
        page.findChild(object, "scriptExportDialog").close()
        other_path = tmp_path / "different-format.nproj"
        other = Operations.create_graph_project(other_path)
        other_id = create_fixture(other)
        ui.bridge.openProject(str(other_path))
        ui.wait(lambda: str(ui.bridge._project_path) == str(other_path))
        ui.window.setProperty("currentPage", "screenplay")
        QMetaObject.invokeMethod(page, "selectScreenplay", Q_ARG("QVariant", other_id))
        ui.wait(lambda: plain(page.property("screenplay")).get("id") == other_id)
        render_ready(ui)
        click(ui, page.findChild(object, "screenplayExport"))
        ui.wait(lambda: page.findChild(object, "scriptExportDialog").property("opened"))
        click(ui, page.findChild(object, "scriptCompileSettingsOpen"))
        ui.wait(lambda: editor.property("opened") and editor.property("loaded"))
        assert plain(editor.property("draft")) == other.load_compile_settings()["settings"]
        assert field("scriptCompileFontSize").property("text") == "12"
        assert ops.load_compile_settings()["settings"] == saved["settings"]
        assert ops.load_compile_settings()["revision"] == saved["revision"]
        assert ops.read_screenplay(screenplay_id) == before
        page.findChild(object, "scriptExportDialog").close()
        ui.bridge.openProject(str(original_path))
        ui.wait(lambda: str(ui.bridge._project_path) == str(original_path))
        assert not editor.property("visible")  # Open project change invalidates the old editor.
        ui.window.setProperty("currentPage", "screenplay")
        QMetaObject.invokeMethod(page, "selectScreenplay", Q_ARG("QVariant", screenplay_id))
        ui.wait(lambda: plain(page.property("screenplay")).get("id") == screenplay_id)
        render_ready(ui)
        click(ui, page.findChild(object, "screenplayExport"))
        ui.wait(lambda: page.findChild(object, "scriptExportDialog").property("opened"))
        click(ui, page.findChild(object, "scriptCompileSettingsOpen"))
        ui.wait(lambda: editor.property("opened") and editor.property("loaded"))
        assert field("scriptCompileFontSize").property("text") == "15.5"
        assert plain(editor.property("draft")) == saved["settings"]
