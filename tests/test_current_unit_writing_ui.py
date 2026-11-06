"""One adaptive scene-writing entry preserves the existing frozen-task contract."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import create_fixture, render_ready
from test_static_check_ui import click, plain


def test_one_current_scene_button_empty_and_existing_body_prompts_and_frozen_targets(tmp_path,monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE","Fusion")
    with transcript_shell(tmp_path,monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        sid=create_fixture(ops)
        segment=ops.read_screenplay(sid)["segments"][0]
        for title in ("待写场","已有正文场"):
            segment=ops.apply_scene_table_changes(segment["id"],[{"op":"insert","unit":{"plan":{"title":title,"summary":title+"的故事进展。"}},"after_id":segment["units"][-1]["id"] if segment["units"] else None}],segment["outline_revision"],reason="合并场写作入口样本")
        empty,existing=segment["units"]
        ops.apply_script_block_changes(existing["id"],[{"op":"insert","block":{"type":"action","payload":{"text":"桥头来人停下脚步。"}}}],[],existing["body_revision"],reason="已有正文样本")
        ui.window.setProperty("assistantOpen",False)
        ui.window.setProperty("currentPage","screenplay")
        page=ui.window.findChild(object,"screenplayPage")
        QMetaObject.invokeMethod(page,"selectScreenplay",Q_ARG("QVariant",sid))
        ui.wait(lambda:plain(page.property("unit")).get("id")==empty["id"] and "unit_writing_shortcut" in plain(page.property("effectivePrompts")))
        captured=[]
        monkeypatch.setattr(ui.bridge,"_assistant_turn",lambda *args,**kwargs:captured.append((args[0],kwargs["writing_task"])))
        button=page.findChild(object,"scriptWriteCurrentUnit")
        render_ready(ui)
        assert button.property("text")=="写当前场"
        click(ui,button)
        dialog=page.findChild(object,"scriptWritingDialog")
        ui.wait(lambda:dialog.property("opened"))
        shortcut=next(row["effective"] for row in ops.load_prompt_settings()["prompts"] if row["id"]=="unit_writing_shortcut")
        assert page.findChild(object,"scriptWritingPrompt").property("text")==shortcut
        frozen=plain(page.property("writingTarget"))
        assert frozen["kind"]=="unit" and frozen["ids"]==[empty["id"]]
        page.setProperty("selectedUnitId",existing["id"])
        assert plain(page.property("writingTarget"))==frozen
        click(ui,page.findChild(object,"scriptWritingSend"))
        ui.wait(lambda:len(captured)==1 and not dialog.property("visible"))
        assert captured[0][1]["unit_ids"]==[empty["id"]]
        assert captured[0][0]==shortcut
        ui.window.setProperty("assistantOpen",False)
        ui.wait(lambda:plain(page.property("unit")).get("id")==existing["id"])
        render_ready(ui)
        assert button.property("text")=="修改当前场"
        click(ui,button)
        ui.wait(lambda:dialog.property("opened"))
        prompt=page.findChild(object,"scriptWritingPrompt")
        assert prompt.property("text")==""
        assert not page.findChild(object,"scriptWritingSend").property("enabled")
        prompt.setProperty("text","保留现有情节，让动作更简洁。")
        frozen=plain(page.property("writingTarget"))
        page.setProperty("selectedUnitId",empty["id"])
        click(ui,page.findChild(object,"scriptWritingSend"))
        ui.wait(lambda:len(captured)==2 and not dialog.property("visible"))
        assert captured[1][0]=="保留现有情节，让动作更简洁。"
        assert captured[1][1]["target"]==frozen
        assert captured[1][1]["unit_ids"]==[existing["id"]]
        assert ops.read_script_segment(segment["id"])["units"][0]["blocks"]==[]
        assert ops.read_script_segment(segment["id"])["units"][1]["blocks"][0]["payload"]["text"]=="桥头来人停下脚步。"
        assert not ui.errors,ui.errors
