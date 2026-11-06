"""Scene outcomes remain visible after unifying the creative-agreement editor."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import create_fixture, dialog_action, render_ready, visuals
from test_static_check_ui import click, plain


def test_scene_table_outcome_is_visible_and_editable(tmp_path,monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE","Fusion")
    with transcript_shell(tmp_path,monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        sid=create_fixture(ops)
        segment=ops.read_screenplay(sid)["segments"][0]
        segment=ops.apply_scene_table_changes(segment["id"],[{"op":"insert","unit":{"plan":{"title":"渡桥","summary":"甲走过桥。","outcome":"结果不能被概要遮蔽"}}}],segment["outline_revision"],reason="结果字段可见性")
        unit=segment["units"][0]
        ui.window.setProperty("assistantOpen",False)
        ui.window.setProperty("currentPage","screenplay")
        page=ui.window.findChild(object,"screenplayPage")
        QMetaObject.invokeMethod(page,"selectScreenplay",Q_ARG("QVariant",sid))
        ui.wait(lambda:plain(page.property("unit")).get("id")==unit["id"])
        render_ready(ui)
        label=next(item for item in visuals(page) if item.objectName()=="scriptSceneOutcome" and item.property("unitId")==unit["id"])
        assert label.property("visible") and "结果不能被概要遮蔽" in label.property("text")
        editor=page.findChild(object,"scriptPlanEditor")
        QMetaObject.invokeMethod(editor,"openFor",Q_ARG("QVariant",unit))
        ui.wait(lambda:editor.property("opened"))
        assert "所有字段都会供写作参考" in page.findChild(object,"scriptSceneTableGuide").property("text")
        page.findChild(object,"scriptPlanOutcome").setProperty("text","已核对当前事件线的结果")
        click(ui,dialog_action(ui,page,"scriptPlanEditor","scriptPlanSave"))
        ui.wait(lambda:not editor.property("visible") and plain(page.property("unit")).get("plan",{}).get("outcome")=="已核对当前事件线的结果")
        assert not ui.errors,ui.errors
