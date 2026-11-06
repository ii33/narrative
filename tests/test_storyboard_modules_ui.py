"""Independent storyboard/director modules share location and isolate request tokens."""
from screenplay_scene_fixture import seed
from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click, plain


def test_sidebar_modules_keep_shot_and_ignore_other_module_replies(tmp_path,monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE","Fusion")
    with transcript_shell(tmp_path,monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        sid,segment=seed(ops)
        unit=segment['units'][0]
        snapshot=ops.apply_storyboard_changes(unit['id'],0,[{'op':'create_shot','title':'开场','duration_ms':1000},{'op':'create_shot','title':'回望','duration_ms':2000}])
        ui.window.setProperty('assistantOpen',False)
        ui.window.setProperty('currentPage','screenplay')
        host=ui.window.findChild(object,'screenplayPage')
        QMetaObject.invokeMethod(host,'selectScreenplay',Q_ARG('QVariant',sid))
        ui.wait(lambda:plain(host.property('screenplay')).get('id')==sid)
        render_ready(ui)
        click(ui,next(item for item in visuals(ui.window.contentItem()) if item.objectName()=='navigation_storyboard'))
        draft=ui.window.findChild(object,'storyboardPage')
        director=ui.window.findChild(object,'directorDeskPage')
        ui.wait(lambda:draft.property('ready'))
        assert draft.property('visible') and not director.property('visible')
        assert not draft.property('directorOnly')
        assert draft.findChild(object,'storyboardScriptMode').property('visible')
        assert draft.findChild(object,'storyboardTimelineMode') is None
        draft.seek(1375)
        shot_id=snapshot['shots'][1]['id']
        assert plain(draft.property('shot'))['id']==shot_id
        old_token=draft.property('token')
        old_review=draft.property('reviewToken')
        click(ui,next(item for item in visuals(ui.window.contentItem()) if item.objectName()=='navigation_director'))
        ui.wait(lambda:director.property('ready'))
        assert director.property('directorOnly') and not draft.property('visible')
        assert plain(director.property('shot'))['id']==shot_id
        assert director.property('localTime')==375
        assert director.property('token')!=old_token
        ui.wait(lambda:director.property('reviewToken')!=0)
        assert director.property('reviewToken')!=old_review
        before=plain(director.property('board'))
        ui.bridge.resultReady.emit('storyboard',{'token':old_token,'operation':'read_storyboard','error':'other module stale reply'})
        ui.bridge.resultReady.emit('storyboard',{'token':old_review,'operation':'check_storyboard','error':'other module old review'})
        ui.settle()
        assert plain(director.property('board'))==before
        assert director.property('message')!='other module stale reply'
        director.seek(1650)
        click(ui,next(item for item in visuals(ui.window.contentItem()) if item.objectName()=='navigation_storyboard'))
        ui.wait(lambda:draft.property('ready'))
        assert draft.property('localTime')==650
        assert plain(draft.property('shot'))['id']==shot_id
        assert not director.property('playing')
        # Same Ops data, not a second storyboard copy.
        assert plain(draft.property('board'))['revision']==ops.read_storyboard(unit['id'])['revision']
