"""One block editor owns manual edits and fixed-target conversational modifications."""
from test_chat_scroll import transcript_shell
from screenplay_scene_fixture import seed
from test_screenplay_ui import dialog_action, render_ready, visuals
from test_static_check_ui import click, plain


def test_block_modify_saves_then_sends_frozen_target_and_preserves_payload(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE","Fusion")
    with transcript_shell(tmp_path,monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        sid,segment=seed(ops)
        unit=segment["units"][0]
        block=unit["blocks"][0]
        unit=ops.apply_script_block_changes(unit["id"],[{"op":"update","block_id":block["id"],"block":{"type":"dialogue","payload":{"text":"你在等谁？","speaker_label":"旅人","simultaneous_group_id":"group-local"}}}],[block["id"]],unit["body_revision"],reason="合并编辑器完整payload")
        ui.window.setProperty("assistantOpen",False)
        ui.window.setProperty("currentPage","screenplay")
        page=ui.window.findChild(object,"screenplayPage")
        QMetaObject.invokeMethod(page,"selectScreenplay",Q_ARG("QVariant",sid))
        ui.wait(lambda:plain(page.property("unit")).get("id")==unit["id"])
        render_ready(ui)
        assert not [x for x in visuals(page) if x.metaObject().indexOfProperty("text")>=0 and x.property("text")=="对话修改此块"]
        click(ui,next(x for x in visuals(page) if x.objectName()=="scriptBlockCard" and x.property("blockId")==block["id"]))
        button=next(x for x in visuals(page) if x.objectName()=="scriptBlockModify" and x.property("blockId")==block["id"])
        click(ui,button)
        editor=page.findChild(object,"scriptBlockEditor")
        ui.wait(lambda:editor.property("opened"))
        send=page.findChild(object,"scriptBlockDiscussionSend")
        assert send.property("text")=="发送修改意见"
        assert not editor.property("hasChanges")
        page.findChild(object,"scriptBlockText").setProperty("text","我一直在桥头等你。")
        page.findChild(object,"scriptBlockSpeaker").setProperty("text","过客")
        page.findChild(object,"scriptBlockParenthetical").setProperty("text","轻声")
        assert send.property("text")=="保存并发送修改意见"
        assert ops.read_script_segment(segment["id"])["units"][0]["blocks"][0]["payload"]["text"]=="你在等谁？"
        captured=[]
        def capture(*args,**kwargs):
            current=ops.read_script_segment(segment["id"])["units"][0]
            captured.append((args[0],kwargs["writing_task"],current))
        monkeypatch.setattr(ui.bridge,"_assistant_turn",capture)
        # Navigation does not redirect the editor's pending save or its model target.
        page.setProperty("selectedUnitId",segment["units"][1]["id"])
        page.findChild(object,"scriptBlockDiscussion").setProperty("text","保留我的新措辞，润色为更含蓄的语气。")
        click(ui,dialog_action(ui,page,"scriptBlockEditor","scriptBlockDiscussionSend"))
        ui.wait(lambda:len(captured)==1 and not editor.property("visible"))
        prompt,task,saved=captured[0]
        assert task["target"]=={"kind":"blocks","ids":[block["id"]],"screenplay_id":sid}
        assert task["unit_ids"]==[unit["id"]]
        payload=saved["blocks"][0]["payload"]
        assert payload["text"]=="我一直在桥头等你。" and payload["speaker_label"]=="过客"
        assert payload["parenthetical"]=="轻声" and payload["simultaneous_group_id"]=="group-local"
        assert saved["body_revision"]==unit["body_revision"]+1
        assert prompt=="保留我的新措辞，润色为更含蓄的语气。"
        assert page.property("selectedUnitId")==segment["units"][1]["id"]
        assert not ui.errors,ui.errors


def test_block_modify_stale_save_keeps_draft_does_not_start_model_or_close_new_editor(tmp_path,monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE","Fusion")
    with transcript_shell(tmp_path,monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        sid,segment=seed(ops)
        unit=segment["units"][0]
        block=unit["blocks"][0]
        ui.window.setProperty("assistantOpen",False)
        ui.window.setProperty("currentPage","screenplay")
        page=ui.window.findChild(object,"screenplayPage")
        QMetaObject.invokeMethod(page,"selectScreenplay",Q_ARG("QVariant",sid))
        ui.wait(lambda:plain(page.property("unit")).get("id")==unit["id"])
        editor=page.findChild(object,"scriptBlockEditor")
        QMetaObject.invokeMethod(editor,"openFor",Q_ARG("QVariant",block))
        ui.wait(lambda:editor.property("opened"))
        page.findChild(object,"scriptBlockText").setProperty("text","尚未保存的本地改动。")
        page.findChild(object,"scriptBlockDiscussion").setProperty("text","继续润色此段。")
        captured=[]
        monkeypatch.setattr(ui.bridge,"_assistant_turn",lambda *args,**kwargs:captured.append(kwargs))
        ops.apply_script_block_changes(unit["id"],[{"op":"update","block_id":block["id"],"block":{"type":"action","payload":{"text":"另一编辑已保存。"}}}],[block["id"]],unit["body_revision"],reason="正文并发修改")
        click(ui,dialog_action(ui,page,"scriptBlockEditor","scriptBlockDiscussionSend"))
        ui.wait(lambda:not editor.property("saving") and bool(editor.property("errorText")))
        assert editor.property("opened")
        assert page.findChild(object,"scriptBlockText").property("text")=="尚未保存的本地改动。"
        assert page.findChild(object,"scriptBlockDiscussion").property("text")=="继续润色此段。"
        assert not captured
        assert ops.read_script_segment(segment["id"])["units"][0]["blocks"][0]["payload"]["text"]=="另一编辑已保存。"
        old_token=editor.property("saveToken")
        QMetaObject.invokeMethod(editor,"openFor",Q_ARG("QVariant",{}))
        ui.wait(lambda:editor.property("opened"))
        assert not page.findChild(object,"scriptBlockDiscussionSend").property("enabled")
        ui.bridge.resultReady.emit("screenplay",{"operation":"apply_script_block_changes","request_token":old_token,"result":{}})
        ui.settle()
        assert editor.property("opened") and not plain(editor.property("block")).get("id")


def test_block_modify_prepare_failure_keeps_saved_baseline_and_short_window_controls(tmp_path,monkeypatch):
    import threading

    from PySide6.QtCore import Q_ARG, QMetaObject, QPointF

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE","Fusion")
    with transcript_shell(tmp_path,monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        sid,segment=seed(ops)
        unit=segment["units"][0]
        block=unit["blocks"][0]
        ui.window.setHeight(560)
        ui.window.setProperty("assistantOpen",False)
        ui.window.setProperty("currentPage","screenplay")
        page=ui.window.findChild(object,"screenplayPage")
        QMetaObject.invokeMethod(page,"selectScreenplay",Q_ARG("QVariant",sid))
        ui.wait(lambda:plain(page.property("unit")).get("id")==unit["id"])
        editor=page.findChild(object,"scriptBlockEditor")
        QMetaObject.invokeMethod(editor,"openFor",Q_ARG("QVariant",block))
        ui.wait(lambda:editor.property("opened"))
        text=page.findChild(object,"scriptBlockText")
        text.setProperty("text","手工改写后的正文。"*200)
        page.findChild(object,"scriptBlockDiscussion").setProperty("text","依据我已保存的文字修改。")
        send=page.findChild(object,"scriptBlockDiscussionSend")
        render_ready(ui)
        point=send.mapToScene(QPointF(send.width()/2,send.height()/2))
        assert 0<point.y()<ui.window.height() and send.property("visible")
        original_apply=type(ops).apply_script_block_changes
        original_prepare=type(ops).prepare_script_writing_task
        release=threading.Event()
        def delayed_apply(self,*args,**kwargs):
            assert release.wait(3)
            return original_apply(self,*args,**kwargs)
        def reject_prepare(self,*args,**kwargs):
            raise ValueError("修改任务暂时无法启动")
        monkeypatch.setattr(type(ops),"apply_script_block_changes",delayed_apply)
        monkeypatch.setattr(type(ops),"prepare_script_writing_task",reject_prepare)
        captured=[]
        monkeypatch.setattr(ui.bridge,"_assistant_turn",lambda *args,**kwargs:captured.append(kwargs["writing_task"]))
        click(ui,send)
        ui.wait(lambda:editor.property("saving"))
        assert not text.property("enabled")
        assert not page.findChild(object,"scriptBlockDiscussion").property("enabled")
        release.set()
        ui.wait(lambda:not editor.property("pendingStart") and not editor.property("saving") and "无法启动" in editor.property("errorText"))
        assert editor.property("opened") and not captured
        assert page.findChild(object,"scriptBlockDiscussion").property("text")=="依据我已保存的文字修改。"
        assert text.property("text")=="手工改写后的正文。"*200
        saved=ops.read_script_segment(segment["id"])["units"][0]
        assert saved["body_revision"]==unit["body_revision"]+1
        assert plain(editor.property("scope"))["body_revision"]==saved["body_revision"]
        assert not editor.property("hasChanges")
        monkeypatch.setattr(type(ops),"prepare_script_writing_task",original_prepare)
        click(ui,send)
        ui.wait(lambda:len(captured)==1 and not editor.property("visible"))
        assert ops.read_script_segment(segment["id"])["units"][0]["body_revision"]==saved["body_revision"]
