"""Default collapse belongs to the screenplay timeline, not its import picker."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click, plain


def test_script_timeline_defaults_collapsed_preserves_refresh_and_scopes_switches(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        parent = None
        ids = {}
        for kind in ('storyline', 'big_event', 'scene', 'event', 'action'):
            source = ops.edit_adaptation_node(changes={'node_type': kind, 'name': kind}, parent_id=parent)
            parent = next(n['id'] for n in source['nodes'] if n['name'] == kind)
            ids[kind] = parent
        scripts = []
        for title in ('甲剧本', '乙剧本'):
            script = ops.create_screenplay(title, reason='折叠域测试')['screenplay']
            ops.append_screenplay_timeline(script['id'], [ids['storyline']], 0, source['revision'], '导入层级')
            scripts.append(script)
        ui.window.setProperty('assistantOpen', False)
        ui.window.setProperty('currentPage', 'screenplay')
        page = ui.window.findChild(object, 'screenplayPage')
        assert QMetaObject.invokeMethod(page, 'selectScreenplay', Q_ARG('QVariant', scripts[0]['id']))
        ui.wait(lambda: plain(page.property('screenplay')).get('id') == scripts[0]['id'])
        picker = page.findChild(object, 'scriptRangePicker')

        def visible():
            return [r['node']['id'] for r in plain(picker.property('rows'))]

        def toggle(node_id):
            render_ready(ui)
            button = next(x for x in visuals(picker) if x.objectName() == 'scriptRangeToggle' and x.property('nodeId') == node_id)
            click(ui, button)

        assert visible() == [ids['storyline']]
        for kind in ('storyline', 'big_event', 'scene', 'event'):
            toggle(ids[kind])
        assert visible() == [ids[k] for k in ('storyline', 'big_event', 'scene', 'event', 'action')]
        picker.setProperty('startId', ids['event'])
        picker.setProperty('endId', ids['event'])
        page.load()
        ui.wait(lambda: not ui.window.property('busy'))
        assert ids['action'] in visible()  # refresh retains the user's expansion
        assert picker.property('startId') == ids['event']
        # Existing expansion survives append, but newly imported containers start
        # folded even when their parent is already open.
        source = ops.edit_adaptation_node(changes={'node_type': 'event', 'name': '新事件'}, parent_id=ids['scene'])
        added_event = next(n['id'] for n in source['nodes'] if n['name'] == '新事件')
        source = ops.edit_adaptation_node(changes={'node_type': 'action', 'name': '新动作'}, parent_id=added_event)
        added_action = next(n['id'] for n in source['nodes'] if n['name'] == '新动作')
        local = ops.read_screenplay_timeline(scripts[0]['id'])
        ops.append_screenplay_timeline(scripts[0]['id'], [added_event], local['revision'], source['revision'], '追加折叠测试')
        page.load()
        ui.wait(lambda: any(n['id'] == added_event for n in plain(picker.property('nodes'))))
        assert added_event in visible() and added_action not in visible()
        assert ids['action'] in visible()
        toggle(added_event)
        assert added_action in visible()
        # Identical copied IDs must not carry expansion into another timeline.
        assert QMetaObject.invokeMethod(page, 'selectScreenplay', Q_ARG('QVariant', scripts[1]['id']))
        ui.wait(lambda: plain(page.property('screenplay')).get('id') == scripts[1]['id'])
        assert visible() == [ids['storyline']]
        assert QMetaObject.invokeMethod(page, 'selectScreenplay', Q_ARG('QVariant', scripts[0]['id']))
        ui.wait(lambda: plain(page.property('screenplay')).get('id') == scripts[0]['id'])
        assert visible() == [ids['storyline']]
        assert not ui.errors
