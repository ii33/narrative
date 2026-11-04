"""Actual screenplay timeline CRUD, subtree movement and asynchronous scopes."""
from pathlib import Path

from test_chat_scroll import transcript_shell
from test_screenplay_ui import dialog_action, render_ready, visuals
from test_static_check_ui import click, plain


def test_screenplay_timeline_crud_subtree_delete_stale_form_and_late_reply(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from PySide6.QtGui import QFont, QFontDatabase

    from narrative.operations import Operations

    monkeypatch.setenv('QT_QUICK_CONTROLS_STYLE', 'Fusion')
    with transcript_shell(tmp_path, monkeypatch) as ui:
        font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/NotoSansSC-VF.ttf')
        if font_id >= 0:
            ui.app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
        ops = Operations.open(ui.bridge._project_path)
        first = ops.create_screenplay('时间表编辑', reason='实际界面测试')['screenplay']['id']
        second = ops.create_screenplay('另一独立副本', reason='异步隔离')['screenplay']['id']
        source_before, extraction_before = ops.adaptation_snapshot(), ops.graph_snapshot()
        ui.window.setProperty('assistantOpen', False)
        ui.window.setProperty('currentPage', 'screenplay')
        page = ui.window.findChild(object, 'screenplayPage')
        assert QMetaObject.invokeMethod(page, 'selectScreenplay', Q_ARG('QVariant', first))
        ui.wait(lambda: plain(page.property('screenplay')).get('id') == first)
        picker = page.findChild(object, 'scriptRangePicker')
        editor = page.findChild(object, 'scriptTimelineEditor')
        output = Path('artifacts/screenplay-timeline-edit-20261104').resolve()
        output.mkdir(parents=True, exist_ok=True)

        def nodes():
            return ops.read_screenplay_timeline(first)['nodes']

        def ready():
            ui.wait(lambda: not page.property('timelineBusy') and not ui.window.property('busy'))
            render_ready(ui)

        def open_editor(button):
            ready()
            click(ui, page.findChild(object, button))
            ui.wait(lambda: editor.property('opened'))

        def save():
            click(ui, dialog_action(ui, page, 'scriptTimelineEditor', 'scriptTimelineNodeSave'))
            ui.wait(lambda: not editor.property('visible'))
            ready()

        def choose_parent(parent_id):
            ui.settle()
            options = plain(editor.property('parentOptions'))
            editor.findChild(object, 'scriptTimelineNodeParent').setProperty('currentIndex', next(i for i, row in enumerate(options) if row['id'] == parent_id))

        def create(name, kind_index, parent_id=''):
            open_editor('scriptTimelineNew')
            editor.findChild(object, 'scriptTimelineNodeName').setProperty('text', name)
            editor.findChild(object, 'scriptTimelineNodeType').setProperty('currentIndex', kind_index)
            choose_parent(parent_id)
            save()
            return next(n['id'] for n in nodes() if n['name'] == name)

        def select(node_id):
            ready()
            target = next(x for x in visuals(picker) if x.objectName() == 'scriptRangeNode' and x.property('nodeId') == node_id)
            click(ui, target)
            assert picker.property('selectedId') == node_id

        scene = create('桥头', 2)
        event = create('甲抵达桥头', 3, scene)
        action = create('甲停步', 4, event)
        select(scene)
        open_editor('scriptTimelineEdit')
        editor.findChild(object, 'scriptTimelineNodeName').setProperty('text', '旧桥头')
        save()
        assert next(n for n in nodes() if n['id'] == scene)['name'] == '旧桥头'
        other_scene = create('河岸', 2)
        select(event)
        open_editor('scriptTimelineMove')
        choose_parent(other_scene)
        options = plain(editor.property('afterOptions'))
        editor.findChild(object, 'scriptTimelineNodeAfter').setProperty('currentIndex', next(i for i, row in enumerate(options) if row['id'] == other_scene))
        ui.window.grabWindow().save(str(output / 'move-dialog.png'))
        save()
        state = {n['id']: n for n in nodes()}
        assert event in state[other_scene]['events'] and event not in state[scene]['events']
        assert state[other_scene]['sort_id'] < state[event]['sort_id'] < state[action]['sort_id']
        select(other_scene)
        click(ui, page.findChild(object, 'scriptTimelineCopy'))
        ui.wait(lambda: len(nodes()) == 7)
        ready()
        copied = picker.property('selectedId')
        assert copied != other_scene and copied in {n['id'] for n in nodes()}
        copied_nodes = {n['id']: n for n in nodes()}
        copied_event = copied_nodes[copied]['events'][0]
        assert copied_event != event
        assert copied_nodes[copied_event]['events'][0] != action
        for object_name in ('scriptRangeStart', 'scriptRangeEnd'):
            button = next(x for x in visuals(picker) if x.objectName() == object_name and x.property('nodeId') == copied)
            click(ui, button)
        ui.wait(lambda: plain(page.property('rangePreview')).get('status') == 'valid')
        ui.window.grabWindow().save(str(output / 'timeline-copied.png'))
        click(ui, page.findChild(object, 'scriptTimelineDelete'))
        delete = page.findChild(object, 'scriptTimelineDeleteDialog')
        ui.wait(lambda: delete.property('opened'))
        render_ready(ui)
        ui.window.grabWindow().save(str(output / 'delete-dialog.png'))
        click(ui, page.findChild(object, 'scriptTimelineDeleteConfirm'))
        ui.wait(lambda: len(nodes()) == 4)
        ready()
        assert copied not in {n['id'] for n in nodes()}
        assert not picker.property('startId') and not picker.property('endId')
        assert '已删除' in page.property('notice')
        assert ops.adaptation_snapshot() == source_before and ops.graph_snapshot() == extraction_before
        select(other_scene)
        open_editor('scriptTimelineEdit')
        editor.findChild(object, 'scriptTimelineNodeName').setProperty('text', '保留我的未提交名称')
        latest = ops.read_screenplay_timeline(first)
        ops.edit_screenplay_timeline_node(first, scene, {'name': '外部更正'}, expected_timeline_revision=latest['revision'], reason='并发修改')
        error_count = len(ui.errors)
        click(ui, dialog_action(ui, page, 'scriptTimelineEditor', 'scriptTimelineNodeSave'))
        ui.wait(lambda: len(ui.errors) > error_count)
        assert editor.property('opened')
        assert editor.findChild(object, 'scriptTimelineNodeName').property('text') == '保留我的未提交名称'
        assert next(n for n in nodes() if n['id'] == other_scene)['name'] == '河岸'
        assert QMetaObject.invokeMethod(page, 'selectScreenplay', Q_ARG('QVariant', second))
        ui.wait(lambda: plain(page.property('screenplay')).get('id') == second)
        open_editor('scriptTimelineNew')
        editor.findChild(object, 'scriptTimelineNodeName').setProperty('text', '另一剧本的未提交输入')
        ui.bridge.resultReady.emit('screenplay', {'operation': 'edit_screenplay_timeline_node', 'request_screenplay_id': first,
            'result': {'nodes': [], 'created_node_id': 'late-old-node'}})
        ui.settle()
        assert editor.property('opened')
        assert editor.findChild(object, 'scriptTimelineNodeName').property('text') == '另一剧本的未提交输入'
        assert plain(page.property('screenplay'))['id'] == second
        assert len(ui.errors) == error_count + 1
