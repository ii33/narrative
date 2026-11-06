"""Real local MP4 playback and reviewed generation, with every provider request stubbed."""
import math
import shutil
import threading
from pathlib import Path

import pytest
from screenplay_scene_fixture import seed
from test_chat_scroll import transcript_shell
from test_screenplay_ui import dialog_action, render_ready, visuals
from test_static_check_ui import click, plain
from test_storyboard_ui import storyboard_diagnostics  # noqa: F401


@pytest.mark.usefixtures("storyboard_diagnostics")
def test_phase3_real_animatic_adoption_and_single_submitted_job(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw
    from PySide6.QtCore import Q_ARG, QMetaObject
    from PySide6.QtGui import QFont, QFontDatabase

    from narrative import storyboard_video_provider
    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    submitted, queried = [], []
    release = threading.Event()
    def submit(base, key, request):
        submitted.append(request)
        assert release.wait(3)
        return {"task_id":"isolated-video-job"}
    def query(base, key, identifier):
        queried.append(identifier)
        return {"task":{"id":identifier,"status":"succeeded","content":{"url":"isolated://generated"},"usage":{"output_tokens":1}}}
    monkeypatch.setattr(storyboard_video_provider,"submit",submit)
    monkeypatch.setattr(storyboard_video_provider,"query",query)
    with transcript_shell(tmp_path, monkeypatch) as ui:
        font=QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
        if font>=0:
            ui.app.setFont(QFont(QFontDatabase.applicationFontFamilies(font)[0],10))
        ops=Operations.open(ui.bridge._project_path)
        check_entered=threading.Event()
        check_release=threading.Event()
        original_check=type(ops).check_storyboard
        def slow_first_check(self, unit_id):
            if not check_entered.is_set():
                check_entered.set()
                assert check_release.wait(3)
            return original_check(self,unit_id)
        monkeypatch.setattr(type(ops),"check_storyboard",slow_first_check)
        sid,segment=seed(ops)
        unit=segment["units"][0]
        uid=unit["id"]
        sketch=tmp_path/"sketch.png"
        image=Image.new("RGB",(512,288),"#ebe7d9")
        draw=ImageDraw.Draw(image)
        draw.rectangle((300,30,480,250),outline="#657b78",width=8)
        draw.ellipse((100,50,160,110),fill="#8eaaa0")
        image.save(sketch)
        layer=tmp_path/"layer.png"
        Image.new("RGBA",(64,64),(160,90,40,180)).save(layer)
        board=ops.apply_storyboard_changes(uid,0,[{"op":"create_shot","title":"敲窗预演","duration_ms":2000,"source_block_ids":[unit["blocks"][0]["id"]]},{"op":"create_object","name":"手部示意","kind":"prop","x":0.2,"y":0.5}])
        shot=board["shots"][0]
        actor=board["objects"][0]["id"]
        board=ops.apply_storyboard_changes(uid,board["revision"],[{"op":"set_keyframe","shot_id":shot["id"],"offset_ms":0,"sketch_path":str(sketch)},{"op":"set_action_keyframe","object_id":actor,"time_ms":0,"x":0.2,"interpolation":"linear"},{"op":"set_action_keyframe","object_id":actor,"time_ms":2000,"x":0.8,"y":0.9}])
        ui.window.setProperty("assistantOpen",False)
        ui.window.setProperty("currentPage","screenplay")
        host=ui.window.findChild(object,"screenplayPage")
        QMetaObject.invokeMethod(host,"selectScreenplay",Q_ARG("QVariant",sid))
        ui.wait(lambda:plain(host.property("screenplay")).get("id")==sid)
        ui.window.setProperty("currentPage","director")
        desk=ui.window.findChild(object,"directorDeskPage")
        ui.wait(lambda:desk.property("ready") and plain(desk.property("board")).get("revision")==board["revision"])
        render_ready(ui)
        ui.wait(lambda:check_entered.is_set())
        assert not desk.property("busy")
        click(ui,desk.findChild(object,"storyboardAnimation"))
        animation=desk.findChild(object,"storyboardAnimationDialog")
        ui.wait(lambda:animation.property("opened"))
        check_release.set()
        desk.findChild(object,"storyboardAnimationMode").setProperty("currentIndex",1)
        desk.findChild(object,"storyboardLayerPath").setProperty("text",str(layer))
        desk.findChild(object,"storyboardLayerObject").setProperty("currentIndex",1)
        click(ui,desk.findChild(object,"storyboardKeepLayer"))
        click(ui,desk.findChild(object,"storyboardSaveAnimation"))
        ui.wait(lambda:not animation.property("visible") and not desk.property("busy"))
        assert ops.read_storyboard(uid)["shots"][0]["animation"]["mode"]=="layers"
        render_ready(ui)
        animated=next(x for x in visuals(desk) if x.objectName()=="storyboardAnimatedLayer" and x.property("visible"))
        start_x=animated.x()
        start_width=animated.width()
        QMetaObject.invokeMethod(desk,"seek",Q_ARG("QVariant",1000))
        render_ready(ui)
        assert animated.x()!=start_x and animated.width()!=start_width
        click(ui,desk.findChild(object,"storyboardAnimation"))
        ui.wait(lambda:animation.property("opened"))
        desk.findChild(object,"storyboardAnimationMode").setProperty("currentIndex",0)
        click(ui,desk.findChild(object,"storyboardSaveAnimation"))
        ui.wait(lambda:not animation.property("visible") and not desk.property("busy"))
        render_ready(ui)
        assert not [x for x in visuals(desk) if x.objectName()=="storyboardAnimatedLayer" and x.property("visible")]
        assert desk.findChild(object,"storyboardMonitor").property("zoom")==1
        current=ops.read_storyboard(uid)
        ops.apply_storyboard_changes(uid,current["revision"],[{"op":"set_camera_preset","shot_id":shot["id"],"preset":"zoom","start_ms":0,"end_ms":1999,"amount":10,"end":{"x":0.5,"y":-1,"z":0.4,"yaw":10,"pitch":5,"fov_deg":60}}])
        QMetaObject.invokeMethod(desk,"reload")
        ui.wait(lambda:not desk.property("busy"))
        click(ui,desk.findChild(object,"storyboardAnimation"))
        ui.wait(lambda:animation.property("opened"))
        desk.findChild(object,"storyboardAnimationMode").setProperty("currentIndex",2)
        click(ui,desk.findChild(object,"storyboardSaveAnimation"))
        ui.wait(lambda:not animation.property("visible") and not desk.property("busy"))
        QMetaObject.invokeMethod(desk,"seek",Q_ARG("QVariant",1000))
        render_ready(ui)
        monitor=desk.findChild(object,"storyboardMonitor")
        expected_camera=ops.preview_storyboard(uid,1000)["camera"]
        initial_camera=ops.preview_storyboard(uid,0)["camera"]
        zoom=math.tan(math.radians(initial_camera["fov_deg"]/2))/math.tan(math.radians(expected_camera["fov_deg"]/2))
        hfov=math.degrees(2*math.atan(math.tan(math.radians(expected_camera["fov_deg"]/2))*16/9))
        dx=(expected_camera["position"]["x"]-initial_camera["position"]["x"])*960+(expected_camera["yaw"]-initial_camera["yaw"])*960/hfov
        dy=(expected_camera["position"]["z"]-initial_camera["position"]["z"])*540+(expected_camera["pitch"]-initial_camera["pitch"])*540/expected_camera["fov_deg"]
        assert abs(monitor.property("zoom")-zoom)<1e-8
        assert abs(monitor.property("imageX")-(480*(1-zoom)-zoom*dx))<1e-8
        assert abs(monitor.property("imageY")-(270*(1-zoom)+zoom*dy))<1e-8
        movie=tmp_path/"clean.mp4"
        QMetaObject.invokeMethod(desk,"mediaAction",Q_ARG("QVariant","render_storyboard_animatic"),Q_ARG("QVariant",{"unit_id":uid,"shot_id":shot["id"],"output_path":str(movie),"variant":"sketch","include_audio":False}))
        ui.wait(lambda:movie.is_file() and not desk.property("busy"))
        assert plain(desk.property("lastExport"))["annotation_free"]
        monkeypatch.setattr(storyboard_video_provider,"download",lambda url,path:shutil.copyfile(movie,path))
        click(ui,desk.findChild(object,"storyboardVideo"))
        video=desk.findChild(object,"storyboardVideoDialog")
        ui.wait(lambda:video.property("opened") and not desk.property("busy"))
        desk.findChild(object,"storyboardVideoSection").setProperty("currentIndex",1)
        desk.findChild(object,"storyboardAttachVideoPath").setProperty("text",str(movie))
        desk.findChild(object,"storyboardVideoIn").setProperty("value",100)
        desk.findChild(object,"storyboardVideoOut").setProperty("value",1100)
        desk.findChild(object,"storyboardVideoFit").setProperty("currentIndex",1)
        click(ui,dialog_action(ui,desk,"storyboardVideoDialog","storyboardAttachVideo"))
        ui.wait(lambda:ops.read_storyboard(uid)["shots"][0].get("selected_video") and not desk.property("busy"))
        chosen=ops.read_storyboard(uid)["shots"][0]["selected_video"]
        assert chosen["speed"]==0.5 and desk.property("viewMode")==4
        video.setProperty("visible",False)
        QMetaObject.invokeMethod(desk,"seek",Q_ARG("QVariant",1000))
        selected_player=desk.findChild(object,"storyboardSelectedVideo")
        ui.wait(lambda:abs(selected_player.property("position")-600)<50)
        click(ui,desk.findChild(object,"storyboardApprove"))
        ui.wait(lambda:desk.property("approved") and not desk.property("busy"))
        board=ops.read_storyboard(uid)
        ops.apply_storyboard_changes(uid,board["revision"],[{"op":"set_keyframe","shot_id":shot["id"],"offset_ms":0,"style_path":str(sketch)}])
        QMetaObject.invokeMethod(desk,"reload")
        ui.wait(lambda:not desk.property("busy"))
        click(ui,desk.findChild(object,"storyboardVideo"))
        ui.wait(lambda:video.property("opened") and not desk.property("busy"))
        desk.findChild(object,"storyboardVideoSection").setProperty("currentIndex",0)
        # The key is a deterministic fake, written only through the private settings operation.
        click(ui,desk.findChild(object,"storyboardVideoService"))
        settings=desk.findChild(object,"storyboardVideoSettingsDialog")
        ui.wait(lambda:settings.property("opened"))
        desk.findChild(object,"storyboardVideoApiKey").setProperty("text","isolated-provider-test-key")
        click(ui,desk.findChild(object,"storyboardSaveVideoSettings"))
        ui.wait(lambda:not settings.property("visible") and not desk.property("busy"))
        assert ops.read_storyboard_video_settings()["has_api_key"]
        assert "api_key" not in ops.read_storyboard_video_settings()
        desk.findChild(object,"storyboardVideoPrompt").setProperty("text","保持手部动作与窗的显露顺序。")
        click(ui,dialog_action(ui,desk,"storyboardVideoDialog","storyboardPrepareVideo"))
        ui.wait(lambda:plain(video.property("generation")).get("status")=="prepared" and not desk.property("busy"))
        generation=plain(video.property("generation"))
        assert {item["kind"] for item in generation["inputs"]}=={"motion","style"}
        assert all(Path(item["path"]).is_file() for item in generation["inputs"])
        assert not submitted
        directory=Path("artifacts/storyboard-phase3-ui-20261105")
        directory.mkdir(parents=True,exist_ok=True)
        render_ready(ui)
        assert ui.window.grabWindow().save(str(directory/"input-review.png"))
        submit_button=desk.findChild(object,"storyboardSubmitVideo")
        try:
            click(ui,dialog_action(ui,desk,"storyboardVideoDialog","storyboardSubmitVideo"))
            ui.wait(lambda:bool(submitted))
            assert not submit_button.property("enabled")
            click(ui,submit_button)
        finally:
            release.set()
        ui.wait(lambda:plain(video.property("generation")).get("status")=="submitted" and not desk.property("busy"))
        assert len(submitted)==1
        click(ui,dialog_action(ui,desk,"storyboardVideoDialog","storyboardRefreshVideo"))
        ui.wait(lambda:plain(video.property("generation")).get("status")=="succeeded" and not desk.property("busy"))
        assert queried==["isolated-video-job"]
        assert ops.read_storyboard(uid)["shots"][0]["selected_video"]==chosen
        click(ui,dialog_action(ui,desk,"storyboardVideoDialog","storyboardUseGeneratedVideo"))
        click(ui,dialog_action(ui,desk,"storyboardVideoDialog","storyboardAttachVideo"))
        ui.wait(lambda:not desk.property("busy") and ops.read_storyboard(uid)["shots"][0]["selected_video"]["in_ms"]==0)
        assert ops.read_storyboard(uid)["shots"][0]["selected_video"]["out_ms"]==2000
        video.setProperty("visible",False)
        desk.setProperty("viewMode",2)
        render_ready(ui)
        ui.wait(lambda:plain(desk.property("checkReport")).get("revision")==plain(desk.property("board"))["revision"] and not desk.property("reviewing"))
        checked=plain(desk.property("checkReport"))
        before=plain(desk.property("board"))
        desk.setProperty("busy",True)
        ui.bridge.resultReady.emit("storyboard",{"token":desk.property("reviewToken"),"operation":"check_storyboard","error":"离线检查RuntimeError样本","error_type":"RuntimeError"})
        ui.settle()
        assert desk.property("busy")
        desk.setProperty("busy",False)
        ui.bridge.resultReady.emit("storyboard",{"token":desk.property("reviewToken"),"operation":"check_storyboard","result":checked})
        stale=dict(checked,revision=checked["revision"]-1,diagnostics=[{"message":"迟到旧诊断不应出现"}])
        ui.bridge.resultReady.emit("storyboard",{"token":desk.property("reviewToken"),"operation":"check_storyboard","result":stale})
        ui.settle()
        assert plain(desk.property("checkReport"))==checked
        QMetaObject.invokeMethod(desk,"updateBoard",Q_ARG("QVariant",checked))
        assert plain(desk.property("board"))==before
        render_ready(ui)
        assert ui.window.grabWindow().save(str(directory/"director-desk.png"))
        assert not ui.errors,ui.errors
