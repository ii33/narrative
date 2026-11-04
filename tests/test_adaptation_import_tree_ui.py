"""Shared five-column extraction import selection, isolation and large models."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click, plain


def test_adaptation_columns_import_multiselect_filter_cancel_and_source_isolation(tmp_path, monkeypatch):
    from pathlib import Path

    from PySide6.QtGui import QFont, QFontDatabase

    from narrative.operations import Operations

    monkeypatch.setenv('QT_QUICK_CONTROLS_STYLE', 'Fusion')
    with transcript_shell(tmp_path, monkeypatch) as ui:
        font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
        if font_id >= 0:
            ui.app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
        ops = Operations.open(ui.bridge._project_path)
        ids = {}
        for branch in ('甲', '乙'):
            quote = f'{branch}走进屋内，停下。{branch}篇结束。'
            sid = ops.import_source(f'{branch}篇', quote)
            result = ops.submit_extraction(sid, {'nodes': [
                {'type': 'big_event', 'name': branch + '大事', 'quotes': [quote]},
                {'type': 'scene', 'name': branch + '场景', 'big_event': branch + '大事', 'quotes': [quote]},
                {'type': 'event', 'name': branch + '事件', 'scene': branch + '场景', 'quotes': [quote]},
            ], 'properties': []})
            assert result['status'] == 'accepted', result
            named = {n['name']: n['id'] for n in ops.graph_snapshot()['nodes']}
            result = ops.submit_extraction(sid, {'nodes': [
                {'type': 'action', 'name': branch + '动作', 'event': named[branch + '事件'], 'quotes': [quote]},
                {'type': 'storyline', 'name': branch + '故事', 'closing_quote': branch + '篇结束。', 'children': [named[branch + '大事']]},
            ], 'properties': []})
            assert result['status'] == 'accepted', result
        ids = {n['name']: n['id'] for n in ops.graph_snapshot()['nodes']}
        before = ops.graph_snapshot()
        ui.window.setProperty('assistantOpen', False)
        ui.window.setProperty('currentPage', 'adaptation')
        page = ui.window.findChild(object, 'adaptationBrowsePage')
        ui.wait(lambda: not ui.window.property('busy'))
        render_ready(ui)
        click(ui, page.findChild(object, 'adaptationImport'))
        dialog = page.findChild(object, 'adaptationImportDialog')
        ui.wait(lambda: dialog.property('opened'))
        picker = page.findChild(object, 'adaptationImportPicker')
        confirm = page.findChild(object, 'adaptationImportConfirm')
        assert not confirm.property('enabled')
        candidates = plain(page.property('importCandidates'))
        assert len(candidates) == 10
        assert {row['node_type'] for row in candidates} == {'storyline', 'big_event', 'scene', 'event', 'action'}

        def control(name, object_name):
            render_ready(ui)
            return next(item for item in visuals(picker) if item.objectName() == object_name and item.property('nodeId') == ids[name])

        def rows(kind):
            column = next(item for item in visuals(picker) if item.objectName() == 'adaptationImportColumn' and item.property('nodeType') == kind)
            view = next(item for item in visuals(column) if item.objectName() == 'adaptationImportLevelList')
            return plain(view.property('model'))

        for kind in ('storyline', 'big_event', 'scene', 'event', 'action'):
            assert [row['id'] for row in rows(kind)] == [row['id'] for row in candidates if row['node_type'] == kind]
        # Navigation filters rightward, while multi-selection survives branches.
        click(ui, control('乙动作', 'adaptationImportChoice'))
        assert plain(page.property('importSelected')) == [ids['乙动作']]
        assert control('乙故事', 'adaptationImportChoice').property('checkState').value == 1
        click(ui, control('甲故事', 'adaptationImportNavigate'))
        assert [row['id'] for row in rows('event')] == [ids['甲事件']]
        assert plain(page.property('importSelected')) == [ids['乙动作']]
        click(ui, control('甲大事', 'adaptationImportNavigate'))
        assert [row['id'] for row in rows('action')] == [ids['甲动作']]
        click(ui, control('甲故事', 'adaptationImportChoice'))
        assert set(plain(page.property('importSelected'))) == {ids['甲故事'], ids['乙动作']}
        child = control('甲场景', 'adaptationImportChoice')
        assert not child.property('enabled') and child.property('checkState').value == 2
        click(ui, picker.findChild(object, 'adaptationImportAll'))
        assert len(rows('event')) == 2
        assert page.property('includedImportCount') == 6
        output = Path('artifacts/adaptation-import-columns-20261103').resolve()
        output.mkdir(parents=True, exist_ok=True)
        render_ready(ui)
        ui.window.grabWindow().save(str(output / 'five-columns.png'))
        ui.window.resize(900, 900)
        render_ready(ui)
        ui.window.grabWindow().save(str(output / 'five-columns-narrow.png'))
        ui.window.resize(1440, 900)
        render_ready(ui)
        click(ui, page.findChild(object, 'adaptationImportCancel'))
        ui.wait(lambda: not dialog.property('visible'))
        assert ops.adaptation_snapshot()['nodes'] == []
        # Reopen starts clean, then import through the actual confirmation button.
        render_ready(ui)
        click(ui, page.findChild(object, 'adaptationImport'))
        ui.wait(lambda: dialog.property('opened'))
        assert not confirm.property('enabled')
        click(ui, control('甲故事', 'adaptationImportChoice'))
        click(ui, control('乙动作', 'adaptationImportChoice'))
        click(ui, confirm)
        ui.wait(lambda: len(plain(page.property('nodes'))) > 0)
        imported = {n['id'] for n in ops.adaptation_snapshot()['nodes']}
        assert {ids['甲故事'], ids['甲动作'], ids['乙动作']} <= imported
        assert ops.graph_snapshot() == before
        assert not ui.errors

def test_import_columns_large_qml_model_uses_indexed_selection_and_filtering(tmp_path, monkeypatch, request):
    import time

    from PySide6.QtCore import QMetaObject

    # Six thousand leaves under two independent five-level branches. No model
    # service or business write participates in this UI scaling regression.
    candidates = []
    for branch in ('left', 'right'):
        parent = None
        for depth, kind in enumerate(('storyline', 'big_event', 'scene', 'event')):
            node_id = f'{branch}-{kind}'
            children = ([f'{branch}-{("big_event", "scene", "event")[depth]}'] if depth < 3
                        else [f'{branch}-action-{i}' for i in range(3000)])
            candidates.append({'id': node_id, 'name': node_id, 'node_type': kind,
                               'parent_id': parent, 'depth': depth, 'children': children})
            parent = node_id
        candidates.extend({'id': f'{branch}-action-{i}', 'name': f'动作{i}', 'node_type': 'action',
                           'parent_id': parent, 'depth': 4, 'children': []} for i in range(3000))
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.window.setProperty('assistantOpen', False)
        ui.window.setProperty('currentPage', 'adaptation')
        page = ui.window.findChild(object, 'adaptationBrowsePage')
        ui.wait(lambda: not ui.window.property('busy'))
        started = time.perf_counter()
        page.setProperty('importCandidates', candidates)
        dialog = page.findChild(object, 'adaptationImportDialog')
        assert QMetaObject.invokeMethod(dialog, 'open')
        ui.wait(lambda: dialog.property('opened'))
        render_ready(ui)
        picker = page.findChild(object, 'adaptationImportPicker')
        assert page.property('includedImportCount') == 0

        def control(node_id, object_name):
            render_ready(ui)
            return next(item for item in visuals(picker) if item.objectName() == object_name and item.property('nodeId') == node_id)

        def row_count(kind):
            column = next(item for item in visuals(picker) if item.objectName() == 'adaptationImportColumn' and item.property('nodeType') == kind)
            view = next(item for item in visuals(column) if item.objectName() == 'adaptationImportLevelList')
            return len(plain(view.property('model')))

        assert row_count('action') == 6000
        click(ui, control('left-storyline', 'adaptationImportChoice'))
        assert plain(page.property('importSelected')) == ['left-storyline']
        assert page.property('includedImportCount') == 3004
        click(ui, control('left-storyline', 'adaptationImportNavigate'))
        assert row_count('action') == 3000
        assert plain(page.property('importSelected')) == ['left-storyline']
        click(ui, picker.findChild(object, 'adaptationImportAll'))
        assert row_count('action') == 6000
        click(ui, control('left-storyline', 'adaptationImportChoice'))
        page.setProperty('importSelected', ['left-action-2999', 'right-action-2999'])
        assert page.property('includedImportCount') == 2
        assert plain(picker.property('partialIds'))['left-storyline']
        assert plain(picker.property('partialIds'))['right-storyline']
        click(ui, control('left-storyline', 'adaptationImportChoice'))
        assert set(plain(page.property('importSelected'))) == {'left-storyline', 'right-action-2999'}
        assert page.property('includedImportCount') == 3005
        request.node.user_properties.append(('large_import_ui_seconds', round(time.perf_counter() - started, 3)))
        click(ui, page.findChild(object, 'adaptationImportCancel'))
        assert not ui.errors

def test_import_diagnostics_remain_visible_after_success_without_review_gate(tmp_path, monkeypatch):
    from narrative.operations import Operations

    diagnostic = {'code': 'source_interval_conflict', 'severity': 'warning', 'message': '来源单值属性已有并列有效记录，按来源保留'}
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        script_id = ops.create_screenplay('诊断展示', reason='界面诊断测试')['screenplay']['id']
        ui.window.setProperty('assistantOpen', False)
        ui.window.setProperty('currentPage', 'adaptation')
        page = ui.window.findChild(object, 'adaptationBrowsePage')
        ui.wait(lambda: not ui.window.property('busy'))
        ui.bridge.resultReady.emit('adaptation', {'nodes': [], 'import_summary': {'diagnostics': [diagnostic]}})
        assert '来源已有属性区间冲突，请核对' in page.property('adaptationNotice')
        ui.window.setProperty('currentPage', 'screenplay')
        screenplay = ui.window.findChild(object, 'screenplayPage')
        ui.wait(lambda: plain(screenplay.property('screenplay')).get('id') == script_id)
        ui.bridge.resultReady.emit('screenplay', {'operation': 'append_screenplay_timeline', 'request_screenplay_id': script_id,
            'result': {'import_summary': {'added_node_ids': ['E1'], 'diagnostics': [diagnostic]}}})
        assert '来源已有属性区间冲突，请核对' in screenplay.property('notice')
        assert '已导入' in screenplay.property('notice')
        assert not ui.errors
