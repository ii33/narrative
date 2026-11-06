"""Real shared property UI records changes at events and replays state."""
from agent_fixtures import TEXT, draft_result
from test_chat_scroll import transcript_shell
from test_screenplay_ui import dialog_action, render_ready, visuals
from test_static_check_ui import click, plain


def test_property_change_ui_set_clear_and_correction_preserve_prior_event(tmp_path,monkeypatch):
    from narrative.operations import Operations

    monkeypatch.setenv('QT_QUICK_CONTROLS_STYLE','Fusion')
    with transcript_shell(tmp_path,monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        source=ops.import_source('属性变更界面',TEXT)
        assert ops.submit_extraction(source,draft_result())['status']=='accepted'
        nodes=ops.graph_snapshot()['nodes']
        person=next(row['id'] for row in nodes if row['name']=='甲')
        first=next(row['id'] for row in nodes if row['name']=='甲约好接乙')
        last=next(row['id'] for row in nodes if row['name']=='甲接到乙后离开')
        ui.window.setProperty('assistantOpen',False)
        ui.window.setProperty('currentPage','browse')
        page=ui.window.findChild(object,'browsePage')
        page.load()
        ui.wait(lambda:page.property('revision')==ops.graph_project_info()['revision'])
        page.select(person)
        ui.wait(lambda:plain(page.property('detail')).get('node',{}).get('id')==person)
        def add(op,event,value=''):
            page.openAddProperty()
            dialog=page.findChild(object,'addPropertyDialog')
            ui.wait(lambda:dialog.property('opened'))
            page.findChild(object,'addPropertyField').setProperty('text','测试数值')
            page.findChild(object,'addPropertyOperation').setProperty('currentIndex',op)
            page.findChild(object,'addValueType').setProperty('currentIndex',1)
            page.findChild(object,'addValueField').setProperty('text',value)
            events=plain(page.property('events'))
            page.findChild(object,'addEventField').setProperty('currentIndex',next(i for i,row in enumerate(events) if row['id']==event))
            click(ui,dialog_action(ui,page,'addPropertyDialog','addPropertySubmit'))
            ui.wait(lambda:not dialog.property('visible'))
            ui.wait(lambda:page.property('revision')==ops.graph_project_info()['revision'])
        add(0,first,'12')
        old=next(row for row in ops.property_records() if row.get('property_id')=='测试数值')
        add(3,last)
        rows=[row for row in ops.property_records() if row.get('property_id')=='测试数值']
        assert len(rows)==2 and rows[-1]['op']=='clear' and 'value' not in rows[-1]
        assert next(row for row in rows if row['id']==old['id'])==old
        assert ops.property_state(person,'测试数值',first)['state']=={'kind':'known','value':12}
        assert ops.property_state(person,'测试数值',last)['state']=={'kind':'absent'}
        editor=page.findChild(object,'nodeEditor')
        editor.openFor(person)
        ui.wait(lambda:editor.property('opened') and bool(plain(editor.property('properties'))))
        item=next(row for row in plain(editor.property('properties')) if row['property_id']=='测试数值')
        editor.startEdit(item,item['records'][0])
        ui.wait(lambda:editor.property('editRecordId')==old['id'])
        render_ready(ui)
        field=next(row for row in visuals(editor.property('contentItem')) if row.objectName()=='editorEditValue' and row.property('visible'))
        field.setProperty('text','24')
        render_ready(ui)
        submit=next(row for row in visuals(editor.property('contentItem')) if row.objectName()=='editorSubmitEdit' and row.property('visible'))
        click(ui,submit)
        ui.wait(lambda:next(row for row in ops.property_records() if row['id']==old['id'])['value']==24)
        corrected=next(row for row in ops.property_records() if row['id']==old['id'])
        assert corrected['sequence']==old['sequence']
        assert ops.property_state(person,'测试数值',last)['state']['kind']=='absent'
        assert not ui.errors,ui.errors
