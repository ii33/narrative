"""Project prompt editor saves exact text, restores defaults and isolates projects."""
from pathlib import Path

from test_chat_scroll import transcript_shell
from test_screenplay_ui import dialog_action, render_ready
from test_static_check_ui import click, plain


def test_prompt_settings_actual_edit_preview_restore_and_project_isolation(tmp_path, monkeypatch):
    from PySide6.QtGui import QFont, QFontDatabase

    from narrative import native_bridge
    from narrative.operations import Operations

    monkeypatch.setenv('QT_QUICK_CONTROLS_STYLE', 'Fusion')
    monkeypatch.setattr(native_bridge, 'HarnessRunner', lambda *a, **k: (_ for _ in ()).throw(AssertionError('settings cannot start a model')))
    with transcript_shell(tmp_path, monkeypatch) as ui:
        font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/NotoSansSC-VF.ttf')
        if font_id >= 0:
            ui.app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
        first_path = ui.bridge._project_path
        first = Operations.open(first_path)
        first_graph, first_profile = first.graph_snapshot(), first.load_project_settings()
        second_path = tmp_path / '独立提示词.nproj'
        second = Operations.create_graph_project(second_path)
        second_before = second.load_prompt_settings()
        ui.window.setProperty('assistantOpen', False)
        ui.window.setProperty('currentPage', 'project_settings')
        page = ui.window.findChild(object, 'projectSettingsPage')
        ui.wait(lambda: page.property('promptsLoaded') and not ui.window.property('busy'))
        picker = page.findChild(object, 'projectPromptType')
        dialog = page.findChild(object, 'projectPromptEditor')
        text = page.findChild(object, 'projectPromptText')

        def select(identifier):
            prompts = plain(page.property('promptSettings'))['prompts']
            picker.setProperty('currentIndex', next(i for i, row in enumerate(prompts) if row['id'] == identifier))
            render_ready(ui)
            return next(row for row in prompts if row['id'] == identifier)

        default = select('scene_table_shortcut')['effective']
        click(ui, page.findChild(object, 'projectPromptPreview'))
        ui.wait(lambda: dialog.property('opened'))
        assert text.property('readOnly') and text.property('text') == default
        dialog.close()
        ui.wait(lambda: not dialog.property('visible'))
        click(ui, page.findChild(object, 'projectPromptEdit'))
        ui.wait(lambda: dialog.property('opened'))
        custom = '请为当前段落编写散文式片段提纲。\n  保留此处缩进，呈现与节奏由内容决定。'
        text.setProperty('text', custom)
        click(ui, dialog_action(ui, page, 'projectPromptEditor', 'projectPromptSave'))
        ui.wait(lambda: not dialog.property('visible') and not page.property('promptSaving'))
        assert select('scene_table_shortcut')['effective'] == custom
        assert select('scene_table_shortcut')['overridden']
        assert first.load_project_settings() == first_profile
        assert first.graph_snapshot() == first_graph
        assert second.load_prompt_settings() == second_before
        output = Path('artifacts/project-prompt-settings-20261104').resolve()
        output.mkdir(parents=True, exist_ok=True)
        render_ready(ui)
        ui.window.grabWindow().save(str(output / 'settings.png'))
        click(ui, page.findChild(object, 'projectPromptPreview'))
        ui.wait(lambda: dialog.property('opened'))
        assert text.property('text') == custom
        render_ready(ui)
        ui.window.grabWindow().save(str(output / 'effective-preview.png'))
        # Switching project while an editor is open discards that stale target;
        # delayed settings from the previous project cannot overwrite the new UI.
        ui.bridge.openProject(str(second_path))
        ui.wait(lambda: ui.bridge._project_path == second_path and plain(ui.window.property('project'))['path'] == str(second_path))
        ui.window.setProperty('currentPage', 'project_settings')
        ui.wait(lambda: page.property('promptsLoaded') and page.property('boundProjectPath') == str(second_path) and not ui.window.property('busy'))
        assert not dialog.property('visible')
        assert select('scene_table_shortcut')['effective'] == default
        ui.bridge.resultReady.emit('prompt_settings', first.load_prompt_settings() | {'project_path': str(first_path)})
        assert select('scene_table_shortcut')['effective'] == default
        ui.bridge.openProject(str(first_path))
        ui.wait(lambda: ui.bridge._project_path == first_path and plain(ui.window.property('project'))['path'] == str(first_path))
        ui.window.setProperty('currentPage', 'project_settings')
        ui.wait(lambda: page.property('promptsLoaded') and page.property('boundProjectPath') == str(first_path) and not ui.window.property('busy'))
        assert select('scene_table_shortcut')['effective'] == custom
        restore=page.findChild(object,'projectPromptRestore')
        ui.wait(lambda:restore.property('enabled'))
        click(ui,restore)
        ui.wait(lambda: '已恢复' in page.property('promptMessage'))
        assert select('scene_table_shortcut')['effective'] == default
        assert not select('scene_table_shortcut')['overridden']
        assert second.load_prompt_settings() == second_before
        # Empty is an intentional project override; null alone restores defaults.
        click(ui, page.findChild(object, 'projectPromptEdit'))
        ui.wait(lambda: dialog.property('opened'))
        text.setProperty('text', '')
        click(ui, dialog_action(ui, page, 'projectPromptEditor', 'projectPromptSave'))
        ui.wait(lambda: not dialog.property('visible') and not page.property('promptSaving'))
        empty = select('scene_table_shortcut')
        assert empty['effective'] == '' and empty['overridden']
        click(ui, page.findChild(object, 'projectPromptRestore'))
        ui.wait(lambda: not page.property('promptSaving') and select('scene_table_shortcut')['effective'] == default)
        assert not select('scene_table_shortcut')['overridden']
        assert not ui.errors
