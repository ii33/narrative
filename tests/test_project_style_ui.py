"""Shared project creative style editing and writing-dialog linkage."""
from pathlib import Path

from test_chat_scroll import transcript_shell
from test_screenplay_ui import create_fixture, dialog_action, render_ready, visuals
from test_static_check_ui import click, plain


def test_project_style_actual_save_reopen_revert_writing_and_project_isolation(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from PySide6.QtGui import QFont, QFontDatabase

    from narrative.operations import Operations

    monkeypatch.setenv('QT_QUICK_CONTROLS_STYLE', 'Fusion')
    with transcript_shell(tmp_path, monkeypatch) as ui:
        font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/NotoSansSC-VF.ttf')
        if font_id >= 0:
            ui.app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
        path = ui.bridge._project_path
        ops = Operations.open(path)
        first = create_fixture(ops)
        segment = ops.read_screenplay(first)['segments'][0]
        segment = ops.apply_scene_table_changes(segment['id'], [{'op': 'insert', 'unit': {'plan': {'title': '渡桥', 'summary': '甲走过桥。', 'outcome': '结果不能被概要遮蔽'}}}], segment['outline_revision'], reason='结果字段可见性')
        unit = segment['units'][0]
        second = ops.create_screenplay('同工程第二方案', reason='风格共享')['screenplay']['id']
        other_path = tmp_path / '其他风格.nproj'
        other = Operations.create_graph_project(other_path)
        before = ops.graph_snapshot()
        ui.window.setProperty('assistantOpen', False)
        ui.window.setProperty('currentPage', 'screenplay')
        page = ui.window.findChild(object, 'screenplayPage')
        assert QMetaObject.invokeMethod(page, 'selectScreenplay', Q_ARG('QVariant', first))
        ui.wait(lambda: plain(page.property('screenplay')).get('id') == first and page.property('styleLoaded') and not ui.window.property('busy'))
        dialog = page.findChild(object, 'scriptProjectStyleDialog')
        text = page.findChild(object, 'scriptProjectStyleText')

        def open_style():
            render_ready(ui)
            click(ui, page.findChild(object, 'scriptProjectStyle'))
            ui.wait(lambda: dialog.property('opened'))

        open_style()
        assert text.property('text') == ''
        style = '表现力与人物声音优先。\n对白保持各自语感；动作有节奏和张力。\n  视觉与场景表达可随本段创作需要变化。'
        text.setProperty('text', style)
        click(ui, dialog_action(ui, page, 'scriptProjectStyleDialog', 'scriptProjectStyleSave'))
        ui.wait(lambda: not dialog.property('visible') and not page.property('styleSaving'))
        assert ops.load_project_style()['text'] == style
        assert ops.graph_snapshot() == before
        open_style()
        assert text.property('text') == style
        text.setProperty('text', '尚未保存的新草稿')
        click(ui, page.findChild(object, 'scriptProjectStyleRevert'))
        assert text.property('text') == style
        output = Path('artifacts/project-style-20261104').resolve()
        output.mkdir(parents=True, exist_ok=True)
        render_ready(ui)
        ui.window.grabWindow().save(str(output / 'style-editor.png'))
        dialog.close()
        ui.wait(lambda: not dialog.property('visible'))
        render_ready(ui)
        click(ui, page.findChild(object, 'scriptWriteSceneTable'))
        writing = page.findChild(object, 'scriptWritingDialog')
        ui.wait(lambda: writing.property('opened'))
        assert '使用项目风格约定' in page.findChild(object, 'scriptWritingStyleSummary').property('text')
        expected = next(row['effective'] for row in ops.load_prompt_settings()['prompts'] if row['id'] == 'scene_table_shortcut')
        assert page.findChild(object, 'scriptWritingPrompt').property('text') == expected
        click(ui, page.findChild(object, 'scriptWritingStyleOpen'))
        ui.wait(lambda: dialog.property('opened'))
        assert text.property('text') == style
        dialog.close()
        writing.close()
        ui.wait(lambda: not dialog.property('visible') and not writing.property('visible'))
        # Existing non-summary fields are visible and remain editable facts.
        outcome_label = next(item for item in visuals(page) if item.objectName() == 'scriptSceneOutcome' and item.property('unitId') == unit['id'])
        assert outcome_label.property('visible') and '结果不能被概要遮蔽' in outcome_label.property('text')
        plan_editor = page.findChild(object, 'scriptPlanEditor')
        assert QMetaObject.invokeMethod(plan_editor, 'openFor', Q_ARG('QVariant', unit))
        ui.wait(lambda: plan_editor.property('opened'))
        assert '所有字段都会供写作参考' in page.findChild(object, 'scriptSceneTableGuide').property('text')
        assert page.findChild(object, 'scriptPlanOutcome').property('text') == '结果不能被概要遮蔽'
        page.findChild(object, 'scriptPlanOutcome').setProperty('text', '已核对当前事件线的结果')
        click(ui, dialog_action(ui, page, 'scriptPlanEditor', 'scriptPlanSave'))
        ui.wait(lambda: not plan_editor.property('visible'))
        ui.wait(lambda: plain(page.property('unit')).get('plan', {}).get('outcome') == '已核对当前事件线的结果')
        render_ready(ui)
        ui.window.grabWindow().save(str(output / 'style-workspace.png'))
        assert QMetaObject.invokeMethod(page, 'selectScreenplay', Q_ARG('QVariant', second))
        ui.wait(lambda: plain(page.property('screenplay')).get('id') == second)
        assert plain(page.property('projectStyle'))['text'] == style
        # Opening another project closes stale editors and rejects old responses.
        open_style()
        text.setProperty('text', '旧工程未提交的风格')
        ui.bridge.openProject(str(other_path))
        ui.wait(lambda: ui.bridge._project_path == other_path and plain(ui.window.property('project'))['path'] == str(other_path))
        ui.window.setProperty('currentPage', 'screenplay')
        ui.wait(lambda: page.property('styleLoaded') and not ui.window.property('busy'))
        assert not dialog.property('visible') and plain(page.property('projectStyle'))['text'] == ''
        ui.bridge.resultReady.emit('project_style', ops.load_project_style() | {'project_path': str(path)})
        assert plain(page.property('projectStyle'))['text'] == ''
        assert other.load_project_style() == {'revision': 0, 'text': ''}
        ui.bridge.openProject(str(path))
        ui.wait(lambda: ui.bridge._project_path == path and plain(ui.window.property('project'))['path'] == str(path))
        ui.window.setProperty('currentPage', 'screenplay')
        ui.wait(lambda: page.property('styleLoaded') and not ui.window.property('busy'))
        open_style()
        assert text.property('text') == style
        assert not ui.errors
