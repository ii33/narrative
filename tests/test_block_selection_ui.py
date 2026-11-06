"""Real pointer selection and shared block operations preserve text selection and IDs."""
from test_chat_scroll import transcript_shell
from screenplay_scene_fixture import seed
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click, plain


def test_block_selection_toolbar_double_click_text_copy_move_and_delete(tmp_path,monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject, QPointF, Qt
    from PySide6.QtTest import QTest

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE","Fusion")
    with transcript_shell(tmp_path,monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        sid,segment=seed(ops)
        unit=segment["units"][0]
        unit=ops.apply_script_block_changes(unit["id"],[{"op":"insert","after_id":unit["blocks"][-1]["id"],"block":{"type":"action","payload":{"text":"第三块独立正文。"}}}],[],unit["body_revision"],reason="共享工具栏排序样本")
        ids=[b["id"] for b in unit["blocks"]]
        original=ops.graph_snapshot()
        ui.window.setProperty("assistantOpen",False)
        ui.window.setProperty("currentPage","screenplay")
        page=ui.window.findChild(object,"screenplayPage")
        QMetaObject.invokeMethod(page,"selectScreenplay",Q_ARG("QVariant",sid))
        ui.wait(lambda:plain(page.property("unit")).get("id")==unit["id"])
        editor=page.findChild(object,"scriptBlockEditor")
        modify=page.findChild(object,"scriptBlockModify")
        move=page.findChild(object,"scriptBlockMoveUp")
        delete=page.findChild(object,"scriptBlockDelete")
        assert not modify.property("enabled") and not move.property("enabled") and not delete.property("enabled")
        def card(identifier):
            return next(x for x in visuals(page) if x.objectName()=="scriptBlockCard" and x.property("blockId")==identifier)
        render_ready(ui)
        first=card(ids[0])
        point=first.mapToScene(QPointF(5,5))
        QTest.mouseClick(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point.toPoint())
        ui.wait(lambda:page.property("selectedBlockId")==ids[0])
        assert first.property("selected") and modify.property("enabled") and not move.property("enabled")
        assert not any(x.objectName()=="scriptBlockModify" for x in visuals(first))
        area=next(x for x in visuals(page) if x.objectName()=="scriptBlockDisplayText" and x.property("blockId")==ids[0])
        start=area.mapToScene(QPointF(4,area.height()/2)).toPoint()
        end=area.mapToScene(QPointF(min(140,area.width()-4),area.height()/2)).toPoint()
        QTest.mousePress(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
        QTest.mouseMove(ui.window,end,50)
        QTest.mouseRelease(ui.window,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,end)
        ui.settle()
        assert area.property("selectedText") and not editor.property("visible")
        QTest.keyClick(ui.window,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
        assert ui.app.clipboard().text()==area.property("selectedText")
        # The passive card handler also recognizes a double click on the TextArea.
        point=area.mapToScene(QPointF(20,area.height()/2))
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        for index,event_type in enumerate((QEvent.Type.MouseButtonPress,QEvent.Type.MouseButtonRelease,QEvent.Type.MouseButtonDblClick,QEvent.Type.MouseButtonRelease)):
            buttons=Qt.MouseButton.NoButton if event_type==QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
            event=QMouseEvent(event_type,point,point,Qt.MouseButton.LeftButton,buttons,Qt.KeyboardModifier.NoModifier)
            event.setTimestamp(100000+index*50)
            ui.app.sendEvent(ui.window,event)
        ui.settle()
        ui.wait(lambda:editor.property("opened"))
        assert plain(editor.property("block"))["id"]==ids[0]
        editor.close()
        ui.wait(lambda:not editor.property("visible"))
        render_ready(ui)
        click(ui,card(ids[2]))
        click(ui,move)
        ui.wait(lambda:not page.property("blockActionBusy") and [b["id"] for b in plain(page.property("unit"))["blocks"]]==[ids[0],ids[2],ids[1]])
        assert page.property("selectedBlockId")==ids[2]
        click(ui,move)
        ui.wait(lambda:not page.property("blockActionBusy") and plain(page.property("unit"))["blocks"][0]["id"]==ids[2])
        assert not move.property("enabled")
        for expected in (ids[0],ids[1],""):
            click(ui,ui.window.findChild(object,"screenplayPage").findChild(object,"scriptBlockDelete"))
            ui.wait(lambda expected=expected:ui.window.findChild(object,"screenplayPage").property("selectedBlockId")==expected and not ui.window.findChild(object,"screenplayPage").property("blockActionBusy"))
        ui.wait(lambda:plain(page.property("unit")).get("blocks")==[])
        assert not modify.property("enabled") and not move.property("enabled") and not delete.property("enabled")
        assert ops.graph_snapshot()==original and not ui.errors,ui.errors


def test_pending_block_action_does_not_restore_old_selection_or_old_scene(tmp_path,monkeypatch):
    import threading

    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE","Fusion")
    with transcript_shell(tmp_path,monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        sid,segment=seed(ops)
        unit,other=segment["units"][:2]
        first,second=unit["blocks"]
        ui.window.setProperty("assistantOpen",False)
        ui.window.setProperty("currentPage","screenplay")
        page=ui.window.findChild(object,"screenplayPage")
        QMetaObject.invokeMethod(page,"selectScreenplay",Q_ARG("QVariant",sid))
        ui.wait(lambda:plain(page.property("unit")).get("id")==unit["id"])
        def card(identifier):
            return next(x for x in visuals(page) if x.objectName()=="scriptBlockCard" and x.property("blockId")==identifier)
        original=type(ops).apply_script_block_changes
        release=threading.Event()
        def blocked(self,*args,**kwargs):
            assert release.wait(3)
            return original(self,*args,**kwargs)
        monkeypatch.setattr(type(ops),"apply_script_block_changes",blocked)
        render_ready(ui)
        click(ui,card(second["id"]))
        click(ui,page.findChild(object,"scriptBlockMoveUp"))
        assert page.property("blockActionBusy")
        assert not page.findChild(object,"scriptBlockDelete").property("enabled")
        click(ui,card(first["id"]))
        release.set()
        ui.wait(lambda:not page.property("blockActionBusy") and plain(page.property("unit"))["blocks"][0]["id"]==second["id"])
        assert page.property("selectedBlockId")==first["id"]
        release.clear()
        click(ui,page.findChild(object,"scriptBlockDelete"))
        assert page.property("blockActionBusy")
        page.setProperty("selectedUnitId",other["id"])
        ui.wait(lambda:page.property("selectedBlockId")=="")
        release.set()
        ui.wait(lambda:not page.property("blockActionBusy") and plain(page.property("unit")).get("id")==other["id"])
        assert page.property("selectedBlockId")==""
        assert ops.read_script_segment(segment["id"])["units"][1]["blocks"]==other["blocks"]
        assert [b["id"] for b in ops.read_script_segment(segment["id"])["units"][0]["blocks"]]==[second["id"]]
        assert not ui.errors,ui.errors
