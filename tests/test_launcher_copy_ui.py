"""Recent-project copy through the real Launcher and public Operations."""
from pathlib import Path

from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click


def test_launcher_copy_keeps_active_project_and_lists_new_copy(tmp_path, monkeypatch):
    from PySide6.QtCore import QMetaObject, QUrl

    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        source = ui.bridge._project_path
        source_before = Operations.open(source).project.info()
        chat = ui.bridge._assistant_chat
        runtime = ui.bridge._assistant_runtime
        ui.window.setProperty('mode', 'launcher')
        ui.bridge.loadRecentProjects()
        page = ui.window.findChild(object, 'launcherPage')
        ui.wait(lambda: page.property('projectCount') == 1 and not ui.window.property('busy'))
        render_ready(ui)
        ui.wait(lambda: any(x.objectName() == 'copyRecentProject' for x in visuals(page)))
        button = next(x for x in visuals(page) if x.objectName() == 'copyRecentProject')
        assert button.property('enabled')
        click(ui, button)
        dialog = page.findChild(object, 'projectCopyPicker')
        ui.wait(lambda: dialog.property('visible'))
        assert not button.property('enabled')
        destination = Path(QUrl(dialog.property('selectedFile')).toLocalFile())
        assert destination.name == source.stem + '-副本.nproj'
        QMetaObject.invokeMethod(dialog, 'accepted')
        ui.wait(lambda: any(k == 'project_duplicated' for k, _ in ui.results))
        ui.wait(lambda: page.property('projectCount') == 2)
        assert destination.is_file()
        assert Operations.open(destination).project.info()['title'] == destination.stem
        assert Operations.open(source).project.info() == source_before
        assert ui.bridge._project_path == source
        assert ui.window.property('mode') == 'launcher'
        assert ui.bridge._assistant_chat == chat
        assert ui.bridge._assistant_runtime is runtime
        assert not ui.errors


def test_launcher_missing_project_cannot_copy_and_error_keeps_list(tmp_path, monkeypatch):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.bridge._remember_project(tmp_path / 'missing.nproj')
        ui.window.setProperty('mode', 'launcher')
        ui.bridge.loadRecentProjects()
        page = ui.window.findChild(object, 'launcherPage')
        ui.wait(lambda: page.property('projectCount') == 2 and not ui.window.property('busy'))
        render_ready(ui)
        buttons = [x for x in visuals(page) if x.objectName() == 'copyRecentProject']
        assert sorted(x.property('enabled') for x in buttons) == [False, True]
        source = str(ui.bridge._project_path)
        ui.bridge.duplicateProject(source, source)
        ui.wait(lambda: any(k == 'project_duplicated' for k, _ in ui.errors))
        assert page.property('projectCount') == 2
        assert ui.bridge._project_path.is_file()

def test_launcher_incompatible_schema_copy_button_is_disabled(tmp_path, monkeypatch):
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        monkeypatch.setattr(Operations, 'inspect_graph_project', lambda _: {'status': 'incompatible'})
        ui.window.setProperty('mode', 'launcher')
        ui.bridge.loadRecentProjects()
        page = ui.window.findChild(object, 'launcherPage')
        ui.wait(lambda: page.property('projectCount') == 1 and not ui.window.property('busy'))
        ui.wait(lambda: any(x.objectName() == 'copyRecentProject' for x in visuals(page)))
        button = next(x for x in visuals(page) if x.objectName() == 'copyRecentProject')
        assert not button.property('enabled')
