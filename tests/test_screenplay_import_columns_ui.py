"""Five peer columns preserve global backend order and independent selection."""
from pathlib import Path

from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click, plain


def test_screenplay_import_columns_preserve_cross_scene_order_filter_and_append(tmp_path, monkeypatch):
    from PySide6.QtGui import QFont, QFontDatabase

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
        if font_id >= 0:
            ui.app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
        ops = Operations.open(ui.bridge._project_path)
        ids = {}
        for branch in ('甲', '乙'):
            parent = None
            for kind, label in [('storyline', '故事'), ('big_event', '大事'), ('scene', '场景'), ('event', '事件'), ('action', '动作')]:
                name = branch + label
                snap = ops.edit_adaptation_node(changes={'node_type': kind, 'name': name}, parent_id=parent)
                parent = next(n['id'] for n in snap['nodes'] if n['name'] == name)
                ids[name] = parent
        # This deliberately crosses parent branches. UI must never group events
        # back beneath their parents and thereby undo the backend's read order.
        ops.move_adaptation_node(ids['乙事件'], after_id=None)
        document = ops.create_screenplay('分列导入', reason='列式导入测试')
        screenplay_id = document['screenplay']['id']
        ui.window.setProperty('assistantOpen', False)
        ui.window.setProperty('currentPage', 'screenplay')
        page = ui.window.findChild(object, 'screenplayPage')
        ui.wait(lambda: plain(page.property('screenplay')).get('id') == screenplay_id)
        render_ready(ui)
        click(ui, page.findChild(object, 'scriptTimelineImport'))
        dialog = page.findChild(object, 'scriptTimelineImportDialog')
        ui.wait(lambda: dialog.property('opened'))
        render_ready(ui)
        picker = page.findChild(object, 'scriptTimelineImportPicker')

        def column(kind):
            return next(x for x in visuals(picker) if x.objectName() == 'scriptTimelineImportColumn' and x.property('nodeType') == kind)

        def model(kind):
            container = column(kind)
            view = next(x for x in visuals(container) if x.objectName() == 'scriptTimelineImportLevelList')
            return plain(view.property('model'))

        def control(node_id, name):
            render_ready(ui)
            return next(x for x in visuals(picker) if x.objectName() == name and x.property('nodeId') == node_id)

        expected = ops.import_screenplay_timeline_candidates(screenplay_id)
        for kind in ('storyline', 'big_event', 'scene', 'event', 'action'):
            assert [r['id'] for r in model(kind)] == [r['id'] for r in expected if r['node_type'] == kind]
        assert [r['id'] for r in model('event')] == [ids['乙事件'], ids['甲事件']]
        click(ui, control(ids['乙事件'], 'scriptTimelineImportChoice'))
        click(ui, control(ids['甲故事'], 'scriptTimelineImportNavigate'))
        assert plain(picker.property('focused')), 'navigation click did not focus'
        assert [r['id'] for r in model('event')] == [ids['甲事件']]
        click(ui, control(ids['甲事件'], 'scriptTimelineImportChoice'))
        assert set(plain(page.property('timelineImportSelection'))) == {ids['甲事件'], ids['乙事件']}
        click(ui, control(ids['甲故事'], 'scriptTimelineImportChoice'))
        assert set(plain(page.property('timelineImportSelection'))) == {ids['甲故事'], ids['乙事件']}
        assert not control(ids['甲事件'], 'scriptTimelineImportChoice').property('enabled')
        click(ui, picker.findChild(object, 'scriptTimelineImportAll'))
        assert [r['id'] for r in model('event')] == [ids['乙事件'], ids['甲事件']]
        assert set(plain(page.property('timelineImportSelection'))) == {ids['甲故事'], ids['乙事件']}
        output = Path('artifacts/screenplay-import-columns-20261103').resolve()
        output.mkdir(parents=True, exist_ok=True)
        ui.window.grabWindow().save(str(output / 'five-columns.png'))
        ui.window.resize(900, 900)
        render_ready(ui)
        ui.window.grabWindow().save(str(output / 'five-columns-narrow.png'))
        ui.window.resize(1440, 900)
        render_ready(ui)
        click(ui, page.findChild(object, 'scriptTimelineImportConfirm'))
        ui.wait(lambda: not dialog.property('visible'))
        ui.wait(lambda: len(plain(page.property('workspace'))['timeline']['nodes']) > 0)
        before = ops.read_screenplay_timeline(screenplay_id)
        new = ops.edit_adaptation_node(changes={'node_type': 'event', 'name': '乙新增事件'}, parent_id=ids['乙场景'])
        added_id = next(n['id'] for n in new['nodes'] if n['name'] == '乙新增事件')
        render_ready(ui)
        click(ui, page.findChild(object, 'scriptTimelineImport'))
        ui.wait(lambda: dialog.property('opened'))
        assert dialog.property('title') == '追加导入时间点'
        click(ui, control(added_id, 'scriptTimelineImportChoice'))
        click(ui, page.findChild(object, 'scriptTimelineImportConfirm'))
        ui.wait(lambda: any(n['id'] == added_id for n in plain(page.property('workspace'))['timeline']['nodes']))
        after = ops.read_screenplay_timeline(screenplay_id)
        after_by_id = {n['id']: n for n in after['nodes']}
        for old in before['nodes']:
            current = after_by_id[old['id']]
            assert {k: v for k, v in old.items() if k != 'events'} == {k: v for k, v in current.items() if k != 'events'}
            assert set(old.get('events', [])) <= set(current.get('events', []))
        assert not ui.errors
