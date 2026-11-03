"""Actual multi-level extraction import selection in shared adaptation UI."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click, plain


def test_adaptation_tree_import_multiselect_collapse_cancel_and_source_isolation(tmp_path, monkeypatch):
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
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
        click(ui, page.findChild(object, 'adaptationImport'))
        dialog = page.findChild(object, 'adaptationImportDialog')
        ui.wait(lambda: dialog.property('opened'))
        tree = page.findChild(object, 'adaptationImportTree')
        confirm = page.findChild(object, 'adaptationImportConfirm')
        assert not confirm.property('enabled')
        candidates = plain(page.property('importCandidates'))
        assert len(candidates) == 10
        assert {row['node_type'] for row in candidates} == {'storyline', 'big_event', 'scene', 'event', 'action'}

        def row(name):
            visible = plain(page.property('visibleImportCandidates'))
            index = next(i for i, item in enumerate(visible) if item['id'] == ids[name])
            tree.positionViewAtIndex(index, 0)
            tree.forceLayout()
            render_ready(ui)
            return next(item for item in visuals(tree) if item.objectName() == 'adaptationImportRow' and item.property('nodeId') == ids[name])

        def control(name, object_name):
            return next(item for item in visuals(row(name)) if item.objectName() == object_name)

        # Leaf selection gives honest partial ancestors, then collapse preserves it.
        click(ui, control('乙动作', 'adaptationImportCheck'))
        assert plain(page.property('importSelected')) == [ids['乙动作']]
        assert control('乙故事', 'adaptationImportCheck').property('checkState').value == 1
        click(ui, control('乙故事', 'adaptationImportExpand'))
        assert ids['乙动作'] not in [r['id'] for r in plain(page.property('visibleImportCandidates'))]
        assert plain(page.property('importSelected')) == [ids['乙动作']]
        click(ui, control('乙故事', 'adaptationImportExpand'))
        click(ui, control('乙大事', 'adaptationImportExpand'))
        assert ids['乙事件'] not in [r['id'] for r in plain(page.property('visibleImportCandidates'))]
        click(ui, control('乙大事', 'adaptationImportExpand'))
        # Container selection includes descendants and keeps the other branch leaf.
        click(ui, control('甲故事', 'adaptationImportCheck'))
        assert set(plain(page.property('importSelected'))) == {ids['甲故事'], ids['乙动作']}
        child = control('甲场景', 'adaptationImportCheck')
        assert not child.property('enabled') and child.property('checkState').value == 2
        click(ui, control('甲故事', 'adaptationImportExpand'))
        assert page.property('includedImportCount') == 6
        click(ui, page.findChild(object, 'adaptationImportCancel'))
        ui.wait(lambda: not dialog.property('visible'))
        assert ops.adaptation_snapshot()['nodes'] == []
        # Reopen starts clean, then import through the actual confirmation button.
        click(ui, page.findChild(object, 'adaptationImport'))
        ui.wait(lambda: dialog.property('opened'))
        assert not confirm.property('enabled')
        click(ui, control('甲故事', 'adaptationImportCheck'))
        click(ui, control('乙动作', 'adaptationImportCheck'))
        click(ui, confirm)
        ui.wait(lambda: len(plain(page.property('nodes'))) > 0)
        imported = {n['id'] for n in ops.adaptation_snapshot()['nodes']}
        assert {ids['甲故事'], ids['甲动作'], ids['乙动作']} <= imported
        assert ops.graph_snapshot() == before
        assert not ui.errors

def test_import_tree_large_qml_model_uses_indexed_selection_and_collapse(tmp_path, monkeypatch, request):
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
        assert len(plain(page.property('visibleImportCandidates'))) == 6008
        assert page.property('includedImportCount') == 0
        tree = page.findChild(object, 'adaptationImportTree')

        def root_control(object_name):
            tree.positionViewAtBeginning()
            tree.forceLayout()
            render_ready(ui)
            root = next(item for item in visuals(tree) if item.objectName() == 'adaptationImportRow'
                        and item.property('nodeId') == 'left-storyline')
            return next(item for item in visuals(root) if item.objectName() == object_name)

        click(ui, root_control('adaptationImportCheck'))
        assert plain(page.property('importSelected')) == ['left-storyline']
        assert page.property('includedImportCount') == 3004
        click(ui, root_control('adaptationImportExpand'))
        assert len(plain(page.property('visibleImportCandidates'))) == 3005
        assert plain(page.property('importSelected')) == ['left-storyline']
        click(ui, root_control('adaptationImportExpand'))
        assert len(plain(page.property('visibleImportCandidates'))) == 6008
        click(ui, root_control('adaptationImportCheck'))
        page.setProperty('importSelected', ['left-action-2999', 'right-action-2999'])
        assert page.property('includedImportCount') == 2
        assert plain(page.property('importPartialIds'))['left-storyline']
        assert plain(page.property('importPartialIds'))['right-storyline']
        # Selecting the parent removes only its redundant leaf, using indexed
        # parent links and retaining the independent branch's selected leaf.
        click(ui, root_control('adaptationImportCheck'))
        assert set(plain(page.property('importSelected'))) == {'left-storyline', 'right-action-2999'}
        assert page.property('includedImportCount') == 3005
        request.node.user_properties.append(('large_import_ui_seconds', round(time.perf_counter() - started, 3)))
        click(ui, page.findChild(object, 'adaptationImportCancel'))
        assert not ui.errors
