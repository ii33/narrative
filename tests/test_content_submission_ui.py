"""Real form feedback keeps invalid authored content editable and patches the same draft."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import create_fixture, dialog_action, render_ready
from test_static_check_ui import click, plain


def test_scene_plan_staged_fix_and_explicit_reopen_restore(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    monkeypatch.setenv('QT_QUICK_CONTROLS_STYLE', 'Fusion')
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        sid = create_fixture(ops)
        ui.window.setProperty('assistantOpen', False)
        ui.window.setProperty('currentPage', 'screenplay')
        page = ui.window.findChild(object, 'screenplayPage')
        QMetaObject.invokeMethod(page, 'selectScreenplay', Q_ARG('QVariant', sid))
        ui.wait(lambda: plain(page.property('segment')).get('id'))
        render_ready(ui)
        click(ui, page.findChild(object, 'scriptAddUnit'))
        editor = page.findChild(object, 'scriptPlanEditor')
        ui.wait(lambda: editor.property('opened'))
        editor.findChild(object, 'scriptPlanSummary').setProperty('text', '甲走过桥。')
        editor.findChild(object, 'scriptPlanLocationId').setProperty('text', 'missing-place')
        click(ui, dialog_action(ui, page, 'scriptPlanEditor', 'scriptPlanSave'))
        ui.wait(lambda: any(kind == 'content_submission' for kind, _ in ui.results))
        staged = next(p for k, p in reversed(ui.results) if k == 'content_submission')
        assert editor.property('visible')
        assert not plain(page.property('units'))
        assert editor.findChild(object, 'contentSubmissionProblems').property('text')
        # Closing does not discard the durable draft; reopening offers explicit restore.
        QMetaObject.invokeMethod(editor, 'close')
        ui.wait(lambda: not editor.property('visible'))
        click(ui, page.findChild(object, 'scriptAddUnit'))
        ui.wait(lambda: editor.property('opened'))
        restore = editor.findChild(object, 'contentSubmissionRestore')
        ui.wait(lambda: restore.property('visible'))
        click(ui, restore)
        ui.wait(lambda: editor.findChild(object, 'scriptPlanLocationId').property('text') == 'missing-place')
        editor.findChild(object, 'scriptPlanLocationId').setProperty('text', '')
        click(ui, dialog_action(ui, page, 'scriptPlanEditor', 'scriptPlanSave'))
        ui.wait(lambda: not editor.property('visible'))
        ui.wait(lambda: len(plain(page.property('units'))) == 1)
        saved = ops.read_content_submission(staged['submission_id'])
        assert saved['status'] == 'accepted'
        assert len(ops.read_script_segment(page.property('selectedSegmentId'))['units']) == 1
        assert not ui.errors
