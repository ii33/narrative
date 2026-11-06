"""Real phase-two controls share marker, camera and temporary-audio timing."""
import wave
from pathlib import Path

import pytest
from screenplay_scene_fixture import seed
from test_chat_scroll import transcript_shell
from test_screenplay_ui import dialog_action, render_ready, visuals
from test_static_check_ui import click, plain
from test_storyboard_ui import sketch_provider_stub, storyboard_diagnostics  # noqa: F401


@pytest.mark.usefixtures("storyboard_diagnostics")
def test_phase2_marker_impact_camera_preset_audio_and_subtracks(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from PySide6.QtGui import QFont, QFontDatabase

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        font = QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
        if font >= 0:
            ui.app.setFont(QFont(QFontDatabase.applicationFontFamilies(font)[0], 10))
        ops = Operations.open(ui.bridge._project_path)
        sid, segment = seed(ops)
        unit = segment["units"][0]
        uid, block = unit["id"], unit["blocks"][0]["id"]
        audio = tmp_path / "cue.wav"
        with wave.open(str(audio), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(8000)
            writer.writeframes(b"\x00\x00"*8000)
        board = ops.apply_storyboard_changes(uid, 0, [{"op": "create_shot", "title": "敲窗正面", "duration_ms": 2000, "source_block_ids": [block]}, {"op": "create_shot", "title": "反打重看", "duration_ms": 2000, "action_start_ms": 500, "source_block_ids": [block]}, {"op": "create_object", "name": "敲窗者", "kind": "person", "x": 0.5, "y": 0.2, "height": 0.3}, {"op": "create_object", "name": "背景窗框", "kind": "set", "x": 0.8, "y": 0.9, "height": 0.5}, {"op": "create_marker", "name": "指节触窗", "time_ms": 1000}])
        first, second = board["shots"]
        actor, marker = board["objects"][0]["id"], board["markers"][0]
        board = ops.generate_storyboard_sketch(uid, first["id"], 0, {}, board["revision"])
        board = ops.generate_storyboard_sketch(uid, second["id"], 0, {}, board["revision"])
        board = ops.apply_storyboard_changes(uid, board["revision"], [{"op": "set_keyframe", "shot_id": first["id"], "offset_ms": 1000, "sketch_path": board["shots"][0]["keyframes"][0]["sketch_path"]}, {"op": "set_action_keyframe", "object_id": actor, "time_ms": 1000, "x": 0.55}, {"op": "set_camera_preset", "shot_id": first["id"], "preset": "dolly_zoom", "target_object_id": actor, "start_ms": 0, "end_ms": 1999, "amount": 0.25}, {"op": "create_sound", "shot_id": first["id"], "kind": "effect", "label": "触窗声", "marker_id": marker["id"], "duration_ms": 400, "audio_path": str(audio)}])
        frame_id = board["shots"][0]["keyframes"][1]["id"]
        key_id = board["objects"][0]["keyframes"][0]["id"]
        board = ops.apply_storyboard_changes(uid, board["revision"], [{"op": "bind_keyframe_marker", "shot_id": first["id"], "frame_id": frame_id, "marker_id": marker["id"]}, {"op": "bind_action_keyframe_marker", "object_id": actor, "keyframe_id": key_id, "marker_id": marker["id"]}])
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        host = ui.window.findChild(object, "screenplayPage")
        QMetaObject.invokeMethod(host, "selectScreenplay", Q_ARG("QVariant", sid))
        ui.wait(lambda: plain(host.property("screenplay")).get("id") == sid)
        ui.window.setProperty("currentPage", "director")
        desk = ui.window.findChild(object, "directorDeskPage")
        ui.wait(lambda: desk.property("ready") and plain(desk.property("board")).get("revision") == board["revision"])
        def seek(value):
            QMetaObject.invokeMethod(desk, "seek", Q_ARG("QVariant", value))
            render_ready(ui)
        ratios = []
        for at in (0, 700):
            seek(at)
            actual = ops.preview_storyboard(uid, at)["camera"]
            projected = plain(desk.property("cameraView"))
            assert abs(actual["fov_deg"]-projected["fov_deg"]) < 1e-8
            for a, b in zip(actual["projections"], projected["projections"], strict=True):
                assert abs(a["screen_x"]-b["screen_x"]) < 1e-8
                assert abs(a["size_ratio"]-b["size_ratio"]) < 1e-8
            ratios.append([p["size_ratio"] for p in projected["projections"]])
        assert abs(ratios[0][0]-ratios[1][0]) < 1e-8
        assert ratios[0][1] != ratios[1][1]
        QMetaObject.invokeMethod(desk.findChild(object,"storyboardMarkersDialog"), "openFor", Q_ARG("QVariant", marker))
        markers = desk.findChild(object,"storyboardMarkersDialog")
        ui.wait(lambda: markers.property("opened"))
        desk.findChild(object,"storyboardMarkerTime").setProperty("value",1200)
        click(ui,desk.findChild(object,"storyboardSaveMarker"))
        impact = desk.findChild(object,"storyboardImpactDialog")
        ui.wait(lambda: impact.property("opened"))
        assert plain(desk.property("impactPreview"))["valid"]
        assert ops.read_storyboard(uid)["revision"] == board["revision"]
        click(ui,desk.findChild(object,"storyboardConfirmImpact"))
        ui.wait(lambda:not desk.property("busy") and not impact.property("visible"))
        updated = ops.read_storyboard(uid)
        assert updated["markers"][0]["time_ms"] == 1200
        assert updated["shots"][0]["keyframes"][1]["offset_ms"] == 1200
        assert updated["objects"][0]["keyframes"][0]["time_ms"] == 1200
        seek(1400)
        sound_id = updated["shots"][0]["sounds"][0]["id"]
        player = next(x for x in visuals(desk) if x.objectName()=="storyboardAudioClip" and x.property("soundId")==sound_id)
        ui.wait(lambda: abs(player.property("playerPosition")-200)<30)
        click(ui,desk.findChild(object,"storyboardExpandTracks"))
        assert {x.property("pointKind") for x in visuals(desk) if x.objectName()=="storyboardTrackPoint"} == {"camera","object","sound","marker"}
        click(ui,desk.findChild(object,"storyboardCamera"))
        camera = desk.findChild(object,"storyboardCameraDialog")
        ui.wait(lambda:camera.property("opened") and not desk.property("busy"))
        desk.findChild(object,"storyboardCameraPresetName").setProperty("text","本场推轨设置")
        click(ui,dialog_action(ui,desk,"storyboardCameraDialog","storyboardSaveCameraPreset"))
        ui.wait(lambda:len(ops.load_storyboard_camera_presets()["presets"])==1 and not desk.property("busy"))
        desk.findChild(object,"storyboardCameraPreset").setProperty("currentIndex",7)
        desk.findChild(object,"storyboardCameraAmount").setProperty("text","60")
        click(ui,dialog_action(ui,desk,"storyboardCameraDialog","storyboardApplyCameraPreset"))
        ui.wait(lambda:not camera.property("visible") and not desk.property("busy"))
        assert ops.read_storyboard(uid)["shots"][0]["camera"]["preset"] == "orbit"
        seek(500)
        actual_orbit = ops.preview_storyboard(uid,500)["camera"]
        qml_orbit = plain(desk.property("cameraView"))
        assert abs(actual_orbit["position"]["x"]-qml_orbit["position"]["x"])<1e-8
        assert abs(actual_orbit["position"]["y"]-qml_orbit["position"]["y"])<1e-8
        click(ui,desk.findChild(object,"storyboardCamera"))
        ui.wait(lambda:camera.property("opened") and not desk.property("busy"))
        desk.findChild(object,"storyboardCameraMode").setProperty("currentIndex",1)
        render_ready(ui)
        first_camera = ops.read_storyboard(uid)["shots"][0]["camera"]["keys"][0]
        edit_point = next(x for x in visuals(camera.property("contentItem")) if x.objectName()=="storyboardEditCameraPoint" and x.property("keyframeId")==first_camera["id"])
        click(ui,edit_point)
        desk.findChild(object,"storyboardCameraPointTime").setProperty("value",50)
        desk.findChild(object,"storyboardCameraPointCurve").setProperty("currentIndex",3)
        next(x for x in visuals(camera.property("contentItem")) if x.objectName()=="storyboardCameraPointx").setProperty("text","0.25")
        click(ui,dialog_action(ui,desk,"storyboardCameraDialog","storyboardSaveCameraPoint"))
        ui.wait(lambda:not camera.property("visible") and not desk.property("busy"))
        revised_camera = next(k for k in ops.read_storyboard(uid)["shots"][0]["camera"]["keys"] if k["id"]==first_camera["id"])
        assert revised_camera["offset_ms"] == 50 and revised_camera["x"] == 0.25
        click(ui,desk.findChild(object,"storyboardCamera"))
        ui.wait(lambda:camera.property("opened") and not desk.property("busy"))
        desk.findChild(object,"storyboardHandheld").setProperty("checked",True)
        desk.findChild(object,"storyboardHandheldAmplitude").setProperty("text","0.01")
        desk.findChild(object,"storyboardHandheldFrequency").setProperty("text","1.2")
        desk.findChild(object,"storyboardFocusTarget").setProperty("currentIndex",2)
        desk.findChild(object,"storyboardCameraMode").setProperty("currentIndex",0)
        desk.findChild(object,"storyboardCameraPresetName").setProperty("text","手持与曲线预设")
        click(ui,dialog_action(ui,desk,"storyboardCameraDialog","storyboardSaveCameraPreset"))
        ui.wait(lambda:len(ops.load_storyboard_camera_presets()["presets"])==2 and not desk.property("busy"))
        saved_settings=ops.load_storyboard_camera_presets()["presets"][-1]["settings"]
        assert saved_settings["easing"] == "ease_in_out" and saved_settings["handheld"]["amplitude"]==0.01
        assert "focus_target_object_id" not in saved_settings
        click(ui,dialog_action(ui,desk,"storyboardCameraDialog","storyboardSaveCameraOverlays"))
        ui.wait(lambda:not camera.property("visible") and not desk.property("busy"))
        seek(700)
        overlay_expected=ops.preview_storyboard(uid,700)["camera"]
        overlay_actual=plain(desk.property("cameraView"))
        for field in ("yaw","pitch","fov_deg","focus_distance"):
            assert abs(overlay_expected[field]-overlay_actual[field])<1e-8
        for field in ("x","y","z"):
            assert abs(overlay_expected["position"][field]-overlay_actual["position"][field])<1e-8
        assert [p["in_focus"] for p in overlay_expected["projections"]]==[p["in_focus"] for p in overlay_actual["projections"]]
        # A shared marker move that pushes audio beyond the shot cannot be confirmed.
        current_marker = ops.read_storyboard(uid)["markers"][0]
        QMetaObject.invokeMethod(markers,"openFor",Q_ARG("QVariant",current_marker))
        ui.wait(lambda:markers.property("opened"))
        desk.findChild(object,"storyboardMarkerTime").setProperty("value",1900)
        before_conflict = ops.read_storyboard(uid)
        click(ui,desk.findChild(object,"storyboardSaveMarker"))
        ui.wait(lambda:impact.property("opened"))
        assert not plain(desk.property("impactPreview"))["valid"]
        assert not desk.findChild(object,"storyboardConfirmImpact").property("enabled")
        assert ops.read_storyboard(uid) == before_conflict
        impact.setProperty("visible",False)
        markers.setProperty("visible",False)
        # A silent pause is a real editable sound clip, with no fake audio file.
        click(ui,desk.findChild(object,"storyboardSounds"))
        sounds = desk.findChild(object,"storyboardSoundsDialog")
        ui.wait(lambda:sounds.property("opened"))
        click(ui,desk.findChild(object,"storyboardNewSound"))
        sound_editor = desk.findChild(object,"storyboardSoundDialog")
        ui.wait(lambda:sound_editor.property("opened"))
        desk.findChild(object,"storyboardSoundKind").setProperty("currentIndex",2)
        desk.findChild(object,"storyboardSoundLabel").setProperty("text","留白停顿")
        desk.findChild(object,"storyboardSoundTime").setProperty("value",0)
        desk.findChild(object,"storyboardSoundDuration").setProperty("value",200)
        click(ui,dialog_action(ui,desk,"storyboardSoundDialog","storyboardSaveSound"))
        ui.wait(lambda:not sound_editor.property("visible") and not desk.property("busy"))
        assert any(s["kind"]=="silence" and not s.get("audio_path") for s in ops.read_storyboard(uid)["shots"][0]["sounds"])
        sounds.setProperty("visible",False)
        click(ui,desk.findChild(object,"storyboardSource"))
        source_dialog = desk.findChild(object,"storyboardSourceDialog")
        ui.wait(lambda:source_dialog.property("opened"))
        linked = next(x for x in visuals(source_dialog.property("contentItem")) if x.property("text")=="反打重看")
        click(ui,linked)
        ui.wait(lambda:desk.property("shotIndex")==1 and not source_dialog.property("visible"))
        assert desk.property("actionTime") == 500
        seek(0)
        render_ready(ui)
        moment_point = next(x for x in visuals(desk) if x.objectName()=="storyboardTrackPoint" and x.property("pointKind")=="marker" and x.property("pointId")==marker["id"])
        click(ui,moment_point)
        assert desk.property("actionTime") == 1200
        assert plain(desk.property("cameraView"))["action_ms"] == 1200
        desk.setProperty("showCameraView",True)
        seek(700)
        render_ready(ui)
        destination = Path("artifacts/storyboard-phase2-ui-20261105/director-desk.png")
        destination.parent.mkdir(parents=True,exist_ok=True)
        assert ui.window.grabWindow().save(str(destination))
        assert not ui.errors,ui.errors
