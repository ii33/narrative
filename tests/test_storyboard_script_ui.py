"""The director desk opens on a readable script and shares shot selection."""
import pytest
from screenplay_scene_fixture import seed
from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready
from test_static_check_ui import click, plain
from test_storyboard_ui import storyboard_diagnostics  # noqa: F401


@pytest.mark.usefixtures("storyboard_diagnostics")
def test_script_default_edit_readonly_and_shared_selection(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations
    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        sid, segment=seed(ops)
        unit=segment["units"][0]
        board=ops.apply_storyboard_changes(unit["id"],0,[{"op":"create_shot","title":"窗外","duration_ms":1000,"source_block_ids":[unit["blocks"][0]["id"]]},{"op":"create_shot","title":"回望","duration_ms":2000,"source_block_ids":[unit["blocks"][0]["id"]]}])
        ui.window.setProperty("assistantOpen",False)
        ui.window.setProperty("currentPage","screenplay")
        host=ui.window.findChild(object,"screenplayPage")
        QMetaObject.invokeMethod(host,"selectScreenplay",Q_ARG("QVariant",sid))
        ui.wait(lambda:plain(host.property("screenplay")).get("id")==sid)
        ui.window.setProperty("currentPage","storyboard")
        desk=ui.window.findChild(object,"storyboardPage")
        ui.wait(lambda:desk.property("ready") and len(plain(desk.property("shots")))==2)
        render_ready(ui)
        assert desk.property("deskMode")==0
        desk.findChild(object,"storyboardScriptDescription").setProperty("text","窗外的人抬起手。")
        desk.findChild(object,"storyboardScriptLanguage").setProperty("text","中景，固定机位。")
        click(ui,desk.findChild(object,"storyboardScriptSave"))
        ui.wait(lambda:ops.read_storyboard(unit["id"])["shots"][0]["description"]=="窗外的人抬起手。")
        desk.findChild(object,"storyboardScriptDescription").setProperty("text","尚未保存的本镜文字")
        QMetaObject.invokeMethod(desk,"seek",Q_ARG("QVariant",1100))
        render_ready(ui)
        QMetaObject.invokeMethod(desk,"seek",Q_ARG("QVariant",0))
        render_ready(ui)
        assert desk.findChild(object,"storyboardScriptDescription").property("text")=="尚未保存的本镜文字"
        click(ui,desk.findChild(object,"storyboardReadOnly"))
        assert desk.property("scriptReadOnly")
        assert not desk.findChild(object,"storyboardScriptSave").property("visible")
        ui.window.setProperty("currentPage","director")
        director=ui.window.findChild(object,"directorDeskPage")
        ui.wait(lambda:director.property("ready"))
        QMetaObject.invokeMethod(director,"seek",Q_ARG("QVariant",1100))
        ui.window.setProperty("currentPage","storyboard")
        ui.wait(lambda:desk.property("ready"))
        assert desk.property("shotIndex")==1
        assert desk.findChild(object,"storyboardScriptRows").property("currentIndex")==1
        assert plain(desk.property("shot"))["id"]==board["shots"][1]["id"]
        board=ops.read_storyboard(unit["id"])
        ops.apply_storyboard_changes(unit["id"],board["revision"],[{"op":"create_panel","shot_id":board["shots"][1]["id"],"intent":"手势方向"}])
        QMetaObject.invokeMethod(desk,"reload")
        ui.wait(lambda:len(plain(desk.property("shot")).get("panels",[]))==1)
        click(ui,desk.findChild(object,"storyboardPanelMode"))
        render_ready(ui)
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        canvas=desk.findChild(object,"storyboardPanelCanvas")
        assert abs(canvas.width()/canvas.height()-16/9)<0.001
        desk.findChild(object,"storyboardPanelMarkTool").setProperty("currentIndex",2)
        pos=canvas.mapToScene(canvas.boundingRect().center()).toPoint()
        QTest.mousePress(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,pos)
        QTest.mouseMove(ui.window,pos+QPoint(40,20),30)
        QTest.mouseRelease(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,pos+QPoint(40,20))
        ui.wait(lambda:len(ops.read_storyboard(unit["id"])["shots"][1]["panels"][0]["marks"])==1)
        mark=ops.read_storyboard(unit["id"])["shots"][1]["panels"][0]["marks"][0]
        assert mark["kind"]=="arrow"
        assert len(mark["points"])==2
        assert all(0<=v<=1 for point in mark["points"] for v in point)
        ui.wait(lambda:not desk.property("busy"))
        render_ready(ui)
        click(ui,desk.findChild(object,"storyboardExportScript"))
        ui.wait(lambda:desk.findChild(object,"storyboardScriptExportDialog").property("opened"))
