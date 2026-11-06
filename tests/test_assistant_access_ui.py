"""Chat-local next-task access changes never rewrite the active task's mode."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready
from test_static_check_ui import click, plain


def test_chat_access_ui_persists_switches_next_task_and_rejects_late_scopes(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest

    from narrative.chat_store import ChatStore
    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        project=str(ui.bridge._project_path)
        first=ui.panel.property("chatId")
        before=ui.store.load(first)
        combo=ui.panel.findChild(object,"assistantAccessMode")
        ui.wait(lambda:ui.panel.property("accessRevision")==0)
        assert ui.panel.property("accessMode")=="standard"
        assert not ui.panel.findChild(object,"assistantFullAccessBadge").property("visible")
        def task(identifier, mode, busy=True):
            ui.bridge.resultReady.emit("conversation_progress",{"event":"assistant_started" if busy else "task_complete","chat_id":first,"project_path":project,"task_id":identifier,"access_mode":mode,"busy":busy})
            ui.settle()
        def select_full(full):
            click(ui,combo)
            QTest.keyClick(ui.window,Qt.Key.Key_End if full else Qt.Key.Key_Home)
            QTest.keyClick(ui.window,Qt.Key.Key_Return)
            ui.wait(lambda:not ui.panel.property("accessSaving") and ui.panel.property("accessMode")==("full" if full else "standard"))
        task("standard-running","standard")
        point=ui.view.mapToScene(QPointF(ui.view.width()/2,ui.view.height()/2))
        ui.app.sendEvent(ui.window,QWheelEvent(point,ui.window.mapToGlobal(point.toPoint()),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False))
        ui.settle()
        anchor=ui.anchor()
        original_set=type(ops).set_assistant_access
        def fail_setting(*args,**kwargs):
            raise OSError("离线访问设置保存失败")
        monkeypatch.setattr(type(ops),"set_assistant_access",fail_setting)
        click(ui,combo)
        QTest.keyClick(ui.window,Qt.Key.Key_End)
        QTest.keyClick(ui.window,Qt.Key.Key_Return)
        ui.wait(lambda:not ui.panel.property("accessSaving") and bool(ui.panel.property("accessError")))
        assert ui.panel.property("accessMode")=="standard" and combo.property("currentIndex")==0
        assert ui.store.load(first)["access_mode"]=="standard"
        monkeypatch.setattr(type(ops),"set_assistant_access",original_set)
        select_full(True)
        after_anchor=ui.anchor()
        assert anchor[0]==after_anchor[0] and abs(anchor[1]-after_anchor[1])<4
        assert ui.panel.property("activeAccessMode")=="standard"
        assert "标准访问" in ui.panel.findChild(object,"assistantTaskAccessMode").property("text")
        assert ui.panel.findChild(object,"assistantFullAccessBadge").property("visible")
        assert ui.store.load(first)["access_mode"]=="full"
        # A delayed chat/read snapshot cannot revert the explicitly saved setting.
        ui.bridge.resultReady.emit("chat",{"chat":before,"items":ui.store.list()})
        ui.bridge.resultReady.emit("chat_access",{"chat_id":first,"project_path":project,"operation":"read","access_mode":"standard","access_revision":0})
        ui.settle()
        assert ui.panel.property("accessMode")=="full"
        ui.bridge.openChat(ui.b["id"])
        ui.wait(lambda:ui.panel.property("chatId")==ui.b["id"] and ui.panel.property("accessRevision")==0)
        assert ui.panel.property("accessMode")=="standard"
        ui.bridge.openChat(first)
        ui.wait(lambda:ui.panel.property("chatId")==first and ui.panel.property("accessMode")=="full")
        assert ui.panel.property("activeAccessMode")=="standard"
        task("standard-running","standard",False)
        task("full-running","full")
        select_full(False)
        assert ui.panel.property("activeAccessMode")=="full"
        assert "完全访问" in ui.panel.findChild(object,"assistantTaskAccessMode").property("text")
        assert ui.store.load(first)["access_revision"]==2
        assert ui.store.load(first)["session"]==before["session"]
        assert ui.store.load(first)["messages"]==before["messages"]
        assert plain(ui.panel.property("activeTaskIds"))[first]=="full-running"
        task("full-running","full",False)
        other_entry=tmp_path/"other-access.nproj"
        other=Operations.create_graph_project(other_entry)
        other_store=ChatStore(other.project_info().path)
        other_chat=other_store.create("另一个工程")
        ui.bridge.openProject(str(other_entry))
        ui.wait(lambda:ui.panel.property("chatId")==other_chat["id"] and ui.panel.property("accessRevision")==0)
        assert ui.panel.property("accessMode")=="standard"
        ui.bridge.resultReady.emit("chat_access",{"chat_id":other_chat["id"],"project_path":project,"operation":"set","access_mode":"full","access_revision":99})
        ui.settle()
        assert ui.panel.property("accessMode")=="standard"
        ui.bridge.openProject(project)
        ui.wait(lambda:ui.panel.property("chatId")==first and ui.panel.property("accessRevision")==2)
        render_ready(ui)
        assert ui.panel.property("accessMode")=="standard"
        assert ops.read_assistant_access(first)["access_revision"]==2
        assert not ui.errors,ui.errors
