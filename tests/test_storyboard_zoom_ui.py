"""Timeline zoom is a view-only, anchored operation using real Qt input."""
import math

import pytest
from screenplay_scene_fixture import seed
from test_chat_scroll import transcript_shell
from test_screenplay_ui import dialog_action, render_ready, visuals
from test_static_check_ui import click, plain
from test_storyboard_ui import storyboard_diagnostics  # noqa: F401


@pytest.mark.usefixtures("storyboard_diagnostics")
def test_timeline_zoom_anchor_controls_fit_and_edit_guard(tmp_path, monkeypatch):
    from PIL import Image
    from PySide6.QtCore import Q_ARG, QMetaObject, QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        sid, segment = seed(ops)
        uid = segment["units"][0]["id"]
        block = segment["units"][0]["blocks"][0]["id"]
        durations = [4000, 1000, 6000, 1, 2000, 5000]
        board = ops.apply_storyboard_changes(uid, 0, [{"op":"create_shot", "title":f"镜头{i+1}", "duration_ms":duration, "source_block_ids":[block]} for i,duration in enumerate(durations)]+[{"op":"create_object","kind":"person","name":"走位对象","x":0.5,"y":0.5}])
        image = tmp_path / "frame.png"
        Image.new("RGB", (64, 64), "gray").save(image)
        board = ops.apply_storyboard_changes(uid, board["revision"], [{"op":"set_keyframe", "shot_id":board["shots"][2]["id"], "offset_ms":2000, "sketch_path":str(image)},{"op":"set_action_keyframe","object_id":board["objects"][0]["id"],"time_ms":2000,"x":0.5}])
        before = ops.read_storyboard(uid)
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        host = ui.window.findChild(object, "screenplayPage")
        QMetaObject.invokeMethod(host, "selectScreenplay", Q_ARG("QVariant", sid))
        ui.wait(lambda:plain(host.property("screenplay")).get("id")==sid)
        ui.window.setProperty("currentPage", "director")
        desk = ui.window.findChild(object, "directorDeskPage")
        ui.wait(lambda:desk.property("ready") and len(plain(desk.property("shots")))==6)
        track = desk.findChild(object, "storyboardTrack")
        QMetaObject.invokeMethod(desk, "seek", Q_ARG("QVariant",7000))
        render_ready(ui)

        def actual_time(x):
            for clip in visuals(track):
                if clip.objectName() != "storyboardShotClip":
                    continue
                left = clip.mapToItem(track,QPointF()).x()
                if left<=x<=left+clip.width():
                    index = clip.property("index")
                    return sum(durations[:index])+(x-left)/clip.width()*durations[index]
            raise AssertionError(f"No actual clip at {x}, scroll={track.property('contentX')}")

        def wheel(x, modifiers=Qt.KeyboardModifier.ControlModifier, pixels=0):
            point = track.mapToScene(QPointF(x,35))
            event = QWheelEvent(point,ui.window.mapToGlobal(point.toPoint()),QPoint(0,pixels),QPoint(0,0 if pixels else 120),Qt.MouseButton.NoButton,modifiers,Qt.ScrollPhase.NoScrollPhase,False)
            ui.app.sendEvent(ui.window,event)
            render_ready(ui)

        # Buttons prefer the visible playhead and do not change it.
        playhead_x = sum(durations[:2])*desk.property("timelineScale")+2*track.property("spacing")+2000*desk.property("timelineScale")-track.property("contentX")
        click(ui,desk.findChild(object,"storyboardZoomIn"))
        assert desk.property("playhead")==7000
        assert abs(actual_time(playhead_x)-7000)<5
        slider=desk.findChild(object,"storyboardZoomSlider")
        start=slider.mapToScene(QPointF(slider.width()*0.45,slider.height()/2)).toPoint()
        end=slider.mapToScene(QPointF(slider.width()*0.8,slider.height()/2)).toPoint()
        QTest.mousePress(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
        QTest.mouseMove(ui.window,end,30)
        QTest.mouseRelease(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,end)
        render_ready(ui)
        assert 0.1<desk.property("trackZoom")<=1
        anchor=track.width()*0.35
        at=actual_time(anchor)
        scale=desk.property("trackZoom")
        wheel(anchor)
        assert desk.property("trackZoom")>scale
        assert abs(actual_time(anchor)-at)<5
        at=actual_time(anchor)
        wheel(anchor,pixels=40)
        assert abs(actual_time(anchor)-at)<5
        current=desk.property("trackZoom")
        scroll_before=track.property("contentX")
        wheel(anchor,Qt.KeyboardModifier.NoModifier)
        assert track.property("contentX")!=scroll_before
        assert desk.property("trackZoom")==current
        desk.setProperty("busy",True)
        wheel(anchor)
        assert desk.property("trackZoom")>current
        desk.setProperty("busy",False)
        assert desk.property("playhead")==7000
        assert abs(track.property("contentWidth")-(sum(c.width() for c in visuals(track) if c.objectName()=="storyboardShotClip")+5*track.property("spacing")))<0.01
        assert ops.read_storyboard(uid)==before

        # Zoom cannot change frame-drag geometry between press and release.
        QMetaObject.invokeMethod(desk,"seek",Q_ARG("QVariant",7000))
        render_ready(ui)
        frame_id=before["shots"][2]["keyframes"][0]["id"]
        handle=next(x for x in visuals(track) if x.objectName()=="storyboardFrameHandle" and x.property("frameId")==frame_id)
        point=handle.mapToScene(handle.boundingRect().center()).toPoint()
        clip=next(x for x in visuals(track) if x.objectName()=="storyboardShotClip" and x.property("index")==2)
        width=clip.width()
        QTest.mousePress(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        assert desk.property("timelineEditDrags")==1
        frozen=desk.property("trackZoom")
        wheel(anchor)
        assert desk.property("trackZoom")==frozen
        assert not desk.findChild(object,"storyboardZoomIn").property("enabled")
        QTest.mouseMove(ui.window,point+QPoint(10,0),30)
        QTest.mouseRelease(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point+QPoint(10,0))
        ui.wait(lambda:not desk.property("busy") and ops.read_storyboard(uid)["revision"]>before["revision"])
        assert desk.property("timelineEditDrags")==0
        moved=ops.read_storyboard(uid)
        expected=math.floor(2000+10/width*6000+0.5)
        assert moved["shots"][2]["keyframes"][0]["id"]==frame_id
        assert moved["shots"][2]["keyframes"][0]["offset_ms"]==expected
        click(ui,desk.findChild(object,"storyboardZoomOut"))
        click(ui,desk.findChild(object,"storyboardZoomFit"))
        assert desk.property("trackZoom")==0 and abs(track.property("contentX"))<1
        render_ready(ui)
        clips={c.property("index"):c for c in visuals(track) if c.objectName()=="storyboardShotClip"}
        assert abs(clips[0].width()/clips[2].width()-2/3)<1e-8
        assert clips[3].width()<0.1
        assert ops.read_storyboard(uid)==moved
        click(ui,desk.findChild(object,"storyboardZoomIn"))
        click(ui,desk.findChild(object,"storyboardExpandTracks"))
        render_ready(ui)
        action_id=moved["objects"][0]["keyframes"][0]["id"]
        key=next(x for x in visuals(track) if x.objectName()=="storyboardTrackPoint" and x.property("pointKind")=="object" and x.property("pointId")==action_id and x.property("shotId")==before["shots"][2]["id"])
        clip=next(x for x in visuals(track) if x.objectName()=="storyboardShotClip" and x.property("index")==2)
        width=clip.width()
        point=key.mapToScene(key.boundingRect().center()).toPoint()
        QTest.mousePress(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        assert desk.property("timelineEditDrags")==1
        frozen=desk.property("trackZoom")
        wheel(anchor)
        assert desk.property("trackZoom")==frozen
        QTest.mouseMove(ui.window,point+QPoint(12,0),30)
        QTest.mouseRelease(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point+QPoint(12,0))
        click(ui,dialog_action(ui,desk,"storyboardImpactDialog","storyboardConfirmImpact"))
        ui.wait(lambda:not desk.property("busy") and ops.read_storyboard(uid)["revision"]>moved["revision"])
        final=ops.read_storyboard(uid)
        assert final["objects"][0]["keyframes"][0]["id"]==action_id
        assert abs(final["objects"][0]["keyframes"][0]["time_ms"]-math.floor(2000+12/width*6000+0.5))<=math.ceil(6000/width)  # one input pixel at this zoom
        assert desk.property("timelineEditDrags")==0
