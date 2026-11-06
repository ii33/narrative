"""Phase-one director desk shares edit/action time and records only explicit changes."""
from pathlib import Path

import pytest
from screenplay_scene_fixture import seed
from test_chat_scroll import transcript_shell
from test_screenplay_ui import dialog_action, render_ready, visuals
from test_static_check_ui import click, plain
from test_storyboard_ui import sketch_provider_stub, storyboard_diagnostics  # noqa: F401 - QML diagnostic fixture


@pytest.mark.usefixtures("storyboard_diagnostics")
def test_shared_director_views_motion_record_frame_drag_loop_and_reopen(tmp_path, monkeypatch):
    from PIL import Image
    from PySide6.QtCore import Q_ARG, QMetaObject, QPoint, Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtTest import QTest

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
        image = tmp_path / "hold.png"
        Image.new("RGB", (160, 90), "#879d93").save(image)
        board = ops.apply_storyboard_changes(uid, 0, [{"op": "create_shot", "title": "敲窗", "duration_ms": 1000, "source_block_ids": [block]}, {"op": "create_shot", "title": "侧面重看", "duration_ms": 2000, "action_start_ms": 500, "source_block_ids": [block]}, {"op": "create_object", "kind": "person", "name": "敲窗者", "x": 0.2, "y": 0.4}])
        actor_id = board["objects"][0]["id"]
        first, second = board["shots"]
        board = ops.apply_storyboard_changes(uid, board["revision"], [{"op": "create_object", "kind": "prop", "name": "手中道具", "parent_id": actor_id, "x": 0.1, "y": 0.1, "depth": 2}])
        child_id = board["objects"][1]["id"]
        board = ops.apply_storyboard_changes(uid, board["revision"], [{"op": "set_keyframe", "shot_id": first["id"], "offset_ms": 0, "sketch_path": str(image)}, {"op": "set_keyframe", "shot_id": second["id"], "offset_ms": 0, "sketch_path": str(image)}, {"op": "set_action_keyframe", "object_id": actor_id, "time_ms": 0, "x": 0.2, "rotation": 0, "interpolation": "linear"}, {"op": "set_action_keyframe", "object_id": actor_id, "time_ms": 1000, "x": 0.8, "rotation": 180, "interpolation": "hold"}])
        board = ops.generate_storyboard_sketch(uid, first["id"], 0, {"composition": "two_shot", "framing": "medium"}, board["revision"])
        board = ops.generate_storyboard_sketch(uid, second["id"], 0, {"composition": "single", "framing": "close"}, board["revision"])
        board = ops.apply_storyboard_changes(uid, board["revision"], [{"op": "set_keyframe", "shot_id": first["id"], "offset_ms": 800, "sketch_path": board["shots"][0]["keyframes"][0]["sketch_path"]}])
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        host = ui.window.findChild(object, "screenplayPage")
        QMetaObject.invokeMethod(host, "selectScreenplay", Q_ARG("QVariant", sid))
        ui.wait(lambda: plain(host.property("screenplay")).get("id") == sid)
        ui.window.setProperty("currentPage", "director")
        desk = ui.window.findChild(object, "directorDeskPage")
        ui.wait(lambda: desk.property("ready") and len(plain(desk.property("shots"))) == 2)
        render_ready(ui)
        def seek(ms):
            QMetaObject.invokeMethod(desk, "seek", Q_ARG("QVariant", ms))
            render_ready(ui)
        seek(0)
        held = desk.property("imagePath")
        seek(500)
        assert desk.property("imagePath") == held
        actual = ops.preview_storyboard(uid, 500)
        pose = plain(desk.property("stageObjects"))[0]
        assert abs(pose["world_x"]-actual["objects"][0]["world_x"]) < 1e-9
        assert pose["rotation"] == actual["objects"][0]["rotation"] == 90
        child = plain(desk.property("stageObjects"))[1]
        assert abs(child["world_x"]-actual["objects"][1]["world_x"]) < 1e-9
        assert child["world_depth"] == 2
        seek(1000)
        assert desk.property("shotIndex") == 1 and desk.property("localTime") == 0 and desk.property("actionTime") == 500
        assert plain(desk.property("stageObjects"))[0]["x"] == pose["x"]
        click(ui, desk.findChild(object, "storyboardNextFrame"))
        assert desk.property("playhead") == 1042
        click(ui, desk.findChild(object, "storyboardPreviousFrame"))
        assert desk.property("playhead") == 1000
        def actor():
            return next(x for x in visuals(desk) if x.objectName()=="storyboardStageObject" and x.property("objectId")==actor_id)
        def drag(item, dx, dy=0):
            p = item.mapToScene(item.boundingRect().center()).toPoint()
            QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
            QTest.mouseMove(ui.window, p+QPoint(dx,dy), 30)
            QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p+QPoint(dx,dy))
        revision = ops.read_storyboard(uid)["revision"]
        drag(actor(), 20)
        ui.settle()
        assert ops.read_storyboard(uid)["revision"] == revision
        click(ui, desk.findChild(object, "storyboardRecord"))
        assert desk.property("recording")
        assert "敲窗者" in desk.findChild(object,"storyboardRecordingTarget").property("text")
        drag(actor(), 20, 5)
        ui.wait(lambda: ops.read_storyboard(uid)["revision"] > revision and not desk.property("busy"))
        saved = ops.read_storyboard(uid)
        key = next(k for k in saved["objects"][0]["keyframes"] if k["time_ms"] == 500)
        assert key["x"] > 0.5
        before_rejected = saved["revision"]
        QMetaObject.invokeMethod(desk, "writeObjectPoint", Q_ARG("QVariant", child_id), Q_ARG("QVariant", 500), Q_ARG("QVariant", 0), Q_ARG("QVariant", 0), Q_ARG("QVariant", before_rejected), Q_ARG("QVariant", 0))
        assert "未保存" in desk.property("message")
        assert ops.read_storyboard(uid)["revision"] == before_rejected
        seek(0)
        frame_id = first["keyframes"][0]["id"] if first["keyframes"] else saved["shots"][0]["keyframes"][0]["id"]
        handle = next(x for x in visuals(desk) if x.objectName()=="storyboardFrameHandle" and x.property("frameId")==frame_id)
        drag(handle, 12)
        ui.wait(lambda: ops.read_storyboard(uid)["shots"][0]["keyframes"][0]["offset_ms"] > 0 and not desk.property("busy"))
        assert ops.read_storyboard(uid)["shots"][0]["keyframes"][0]["id"] == frame_id
        seek(100)
        click(ui, desk.findChild(object,"storyboardLoopStart"))
        seek(400)
        click(ui, desk.findChild(object,"storyboardLoopEnd"))
        click(ui, desk.findChild(object,"storyboardLoop"))
        click(ui, desk.findChild(object,"storyboardPlay"))
        ui.wait(lambda: desk.property("playing") and 100 < desk.property("playhead") < 400)
        QMetaObject.invokeMethod(desk,"seek",Q_ARG("QVariant",1000))
        click(ui,desk.findChild(object,"storyboardStageMaximize"))
        assert desk.property("enlargedView") == "stage"
        click(ui,desk.findChild(object,"storyboardStageMaximize"))
        assert desk.property("enlargedView") == ""
        click(ui,desk.findChild(object,"storyboardMonitorMaximize"))
        assert desk.property("enlargedView") == "monitor"
        click(ui,desk.findChild(object,"storyboardMonitorMaximize"))
        seek(1500)
        render_ready(ui)
        directory = Path("artifacts/storyboard-phase1-ui-20261105")
        directory.mkdir(parents=True, exist_ok=True)
        assert ui.window.grabWindow().save(str(directory/"director-desk.png"))
        # Properties, rejected parent deletion, and correcting an accidentally recorded point.
        desk.setProperty("selectedObjectId", actor_id)
        click(ui, desk.findChild(object, "storyboardObjectMenu"))
        click(ui, desk.findChild(object, "storyboardEditObject"))
        object_editor = desk.findChild(object, "storyboardObjectDialog")
        ui.wait(lambda: object_editor.property("opened"))
        desk.findChild(object, "storyboardObjectName").setProperty("text", "敲窗者（修订）")
        click(ui, desk.findChild(object, "storyboardSaveObject"))
        ui.wait(lambda: not object_editor.property("visible") and not desk.property("busy"))
        assert ops.read_storyboard(uid)["objects"][0]["name"] == "敲窗者（修订）"
        before_delete = ops.read_storyboard(uid)
        click(ui, desk.findChild(object, "storyboardObjectMenu"))
        click(ui, desk.findChild(object, "storyboardDeleteObject"))
        ui.wait(lambda: not desk.property("busy") and "父对象" in desk.property("message"))
        assert ops.read_storyboard(uid) == before_delete
        click(ui, desk.findChild(object, "storyboardObjectMenu"))
        click(ui, desk.findChild(object, "storyboardObjectKeys"))
        keys_dialog = desk.findChild(object, "storyboardActionKeysDialog")
        ui.wait(lambda: keys_dialog.property("opened"))
        key_id = key["id"]
        def key_control(name):
            return next(x for x in visuals(keys_dialog.property("contentItem")) if x.objectName()==name and x.property("keyframeId")==key_id)
        key_control("storyboardActionKeyTime").setProperty("value", 600)
        click(ui, key_control("storyboardMoveActionKey"))
        click(ui, dialog_action(ui, desk, "storyboardImpactDialog", "storyboardConfirmImpact"))
        ui.wait(lambda: any(k["id"]==key_id and k["time_ms"]==600 for k in ops.read_storyboard(uid)["objects"][0]["keyframes"]) and not desk.property("busy"))
        render_ready(ui)
        click(ui, key_control("storyboardDeleteActionKey"))
        click(ui, dialog_action(ui, desk, "storyboardImpactDialog", "storyboardConfirmImpact"))
        ui.wait(lambda: not any(k["id"]==key_id for k in ops.read_storyboard(uid)["objects"][0]["keyframes"]) and not desk.property("busy"))
        keys_dialog.setProperty("visible", False)
        desk.setProperty("selectedObjectId", child_id)
        click(ui, desk.findChild(object, "storyboardObjectMenu"))
        click(ui, desk.findChild(object, "storyboardDeleteObject"))
        ui.wait(lambda: len(ops.read_storyboard(uid)["objects"]) == 1 and not desk.property("busy"))
        desk.setProperty("loopStart", 2900)
        desk.setProperty("loopEnd", 3000)
        desk.setProperty("loopEnabled", True)
        seek(0)
        click(ui, desk.findChild(object, "storyboardEditShot"))
        editor = desk.findChild(object, "storyboardShotDialog")
        ui.wait(lambda: editor.property("opened"))
        original_duration = ops.read_storyboard(uid)["shots"][0]["duration_ms"]
        desk.findChild(object, "storyboardShotDuration").setProperty("value", 1)
        render_ready(ui)
        assert editor.property("conflicts")
        assert not desk.findChild(object, "storyboardSaveShot").property("enabled")
        choices = [x for x in visuals(editor.property("contentItem")) if x.objectName()=="storyboardFrameConflictAction"]
        assert len(choices) == 2
        click(ui, choices[0])
        QTest.keyClick(ui.window, Qt.Key.Key_Home)
        QTest.keyClick(ui.window, Qt.Key.Key_Down)
        QTest.keyClick(ui.window, Qt.Key.Key_Return)
        click(ui, choices[1])
        QTest.keyClick(ui.window, Qt.Key.Key_End)
        QTest.keyClick(ui.window, Qt.Key.Key_Return)
        ui.wait(lambda: desk.findChild(object, "storyboardSaveShot").property("enabled"))
        before = ops.read_storyboard(uid)["revision"]
        click(ui, desk.findChild(object, "storyboardSaveShot"))
        ui.wait(lambda: not editor.property("visible") and not desk.property("busy"))
        shortened = ops.read_storyboard(uid)
        assert shortened["shots"][0]["duration_ms"] == 1 < original_duration
        assert len(shortened["shots"][0]["keyframes"]) == 1
        assert shortened["shots"][0]["keyframes"][0]["id"] == frame_id
        assert shortened["shots"][0]["keyframes"][0]["offset_ms"] == 0
        assert not desk.property("loopEnabled")
        assert desk.property("loopStart") == desk.property("loopEnd") == 2001
        assert shortened["revision"] == before+1  # duration and explicit deletion committed together
        reopened = Operations.open(ui.bridge._project_path)
        assert reopened.preview_storyboard(uid, 1500)["objects"] == ops.preview_storyboard(uid, 1500)["objects"]
        assert not ui.errors, ui.errors
        assert any(kind=="storyboard" and "父对象" in str(payload.get("error", "")) for kind,payload in ui.results)
