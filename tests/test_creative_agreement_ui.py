"""One structured editor for project defaults and per-screenplay creative overrides."""
from pathlib import Path

import pytest
from test_chat_scroll import transcript_shell
from test_screenplay_ui import create_fixture, dialog_action, render_ready, visuals
from test_static_check_ui import click, plain


@pytest.fixture(autouse=True)
def creative_qml_diagnostics():
    from PySide6.QtCore import qInstallMessageHandler
    messages=[]
    previous=qInstallMessageHandler(lambda kind,context,text:messages.append(text))
    try:
        yield
    finally:
        qInstallMessageHandler(previous)
    errors=[message for message in messages if "CreativeAgreementEditor" in message and any(kind in message for kind in ("TypeError","ReferenceError","Binding loop","SyntaxError","deprecated"))]
    assert not errors,"\n".join(errors)


def field(editor,name):
    return next(item for item in visuals(editor.property("contentItem")) if item.objectName()==name)


def test_creative_agreement_defaults_override_empty_effective_and_shared_entrypoints(tmp_path,monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from PySide6.QtGui import QFont, QFontDatabase

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE","Fusion")
    with transcript_shell(tmp_path,monkeypatch) as ui:
        font_id=QFontDatabase.addApplicationFont("C:/Windows/Fonts/NotoSansSC-VF.ttf")
        if font_id>=0:
            ui.app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0],10))
        ops=Operations.open(ui.bridge._project_path)
        sid=create_fixture(ops)
        second=ops.create_screenplay("其他方案",reason="覆盖隔离")["screenplay"]["id"]
        ui.window.setProperty("assistantOpen",False)
        ui.window.setProperty("currentPage","screenplay")
        page=ui.window.findChild(object,"screenplayPage")
        QMetaObject.invokeMethod(page,"selectScreenplay",Q_ARG("QVariant",sid))
        ui.wait(lambda:plain(page.property("screenplay")).get("id")==sid)
        render_ready(ui)
        assert page.findChild(object,"scriptProjectStyle") is None
        click(ui,page.findChild(object,"scriptCreativeAgreement"))
        editor=page.findChild(object,"creativeAgreementEditor")
        ui.wait(lambda:editor.property("opened") and editor.property("loaded"))
        definitions=plain(editor.property("original"))["fields"]
        assert len(definitions)==9
        assert {f["id"] for f in definitions}=={"tone","audiovisual_style","dialogue_style","rhythm","audience","adaptation_principles","format_length","production_constraints","other"}
        field(editor,"creativeAgreementScope").setProperty("currentIndex",0)
        defaults={item["id"]:"工程约定："+item["label"]+"。\n保留用户自定表达。" for item in definitions}
        for key,value in defaults.items():
            field(editor,"creativeProject_"+key).setProperty("text",value)
        click(ui,dialog_action(ui,page,"creativeAgreementEditor","creativeAgreementSave"))
        ui.wait(lambda:not editor.property("busy") and "已保存" in editor.property("message"))
        assert ops.load_creative_agreement(sid)["project_defaults"]==defaults
        assert ops.load_creative_agreement(sid)["script_overrides"]=={}
        field(editor,"creativeAgreementScope").setProperty("currentIndex",1)
        field(editor,"creativeInherit_tone").setProperty("checked",False)
        field(editor,"creativeOverride_tone").setProperty("text","")
        field(editor,"creativeInherit_dialogue_style").setProperty("checked",False)
        field(editor,"creativeOverride_dialogue_style").setProperty("text","本剧本对白单独约定。")
        field(editor,"creativeAgreementTitle").setProperty("text","桥上（独立版）")
        field(editor,"creativeAgreementLanguage").setProperty("text","zh-Hans")
        click(ui,dialog_action(ui,page,"creativeAgreementEditor","creativeAgreementSave"))
        ui.wait(lambda:not editor.property("busy") and plain(editor.property("original"))["script_overrides"].get("dialogue_style")=="本剧本对白单独约定。")
        saved=ops.load_creative_agreement(sid)
        assert saved["script_overrides"]=={"tone":"","dialogue_style":"本剧本对白单独约定。"}
        assert saved["effective"]["tone"]=="" and saved["effective"]["audience"]==defaults["audience"]
        assert saved["title"]=="桥上（独立版）" and saved["language"]=="zh-Hans"
        assert ops.load_creative_agreement(second)["effective"]==defaults
        assert "明确留空" in field(editor,"creativeStatus_tone").property("text")
        output=Path("artifacts/creative-agreement-20261104").resolve()
        output.mkdir(parents=True,exist_ok=True)
        render_ready(ui)
        assert ui.window.grabWindow().save(str(output/"creative-agreement-editor.png"))
        field(editor,"creativeAgreementPreview").setProperty("checked",True)
        assert "留空" in field(editor,"creativeEffective_tone").property("text")
        assert "本剧本对白单独约定。" in field(editor,"creativeEffective_dialogue_style").property("text")
        render_ready(ui)
        assert ui.window.grabWindow().save(str(output/"effective-preview.png"))
        editor.close()
        ui.wait(lambda:not editor.property("visible"))
        render_ready(ui)
        click(ui,page.findChild(object,"scriptWriteSceneTable"))
        ui.wait(lambda:page.findChild(object,"scriptWritingDialog").property("opened"))
        click(ui,page.findChild(object,"scriptWritingAgreementOpen"))
        ui.wait(lambda:editor.property("opened") and editor.property("loaded"))
        assert plain(editor.property("original"))["effective"]==saved["effective"]
        editor.close()
        page.findChild(object,"scriptWritingDialog").close()
        ui.wait(lambda:not editor.property("visible"))
        render_ready(ui)
        assert not ui.errors,ui.errors


def test_creative_agreement_stale_preserves_drafts_explicit_refresh_and_cross_project_replies(tmp_path,monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE","Fusion")
    with transcript_shell(tmp_path,monkeypatch) as ui:
        path=ui.bridge._project_path
        ops=Operations.open(path)
        sid=create_fixture(ops)
        ui.window.setProperty("assistantOpen",False)
        ui.window.setProperty("currentPage","screenplay")
        page=ui.window.findChild(object,"screenplayPage")
        QMetaObject.invokeMethod(page,"selectScreenplay",Q_ARG("QVariant",sid))
        ui.wait(lambda:plain(page.property("screenplay")).get("id")==sid)
        render_ready(ui)
        click(ui,page.findChild(object,"scriptCreativeAgreement"))
        editor=page.findChild(object,"creativeAgreementEditor")
        ui.wait(lambda:editor.property("opened") and editor.property("loaded"))
        field(editor,"creativeInherit_rhythm").setProperty("checked",False)
        field(editor,"creativeOverride_rhythm").setProperty("text","未提交的节奏约定。")
        current=ops.load_creative_agreement(sid)
        external=dict(current["project_defaults"])
        external["tone"]="其他编辑刚保存的基调。"
        ops.save_creative_agreement(external,current["project_revision"],"并发编辑",screenplay_id=sid,script_overrides=current["script_overrides"],expected_script_revision=current["script_revision"])
        click(ui,dialog_action(ui,page,"creativeAgreementEditor","creativeAgreementSave"))
        ui.wait(lambda:not editor.property("busy") and bool(editor.property("message")))
        assert editor.property("opened") and field(editor,"creativeOverride_rhythm").property("text")=="未提交的节奏约定。"
        assert ops.load_creative_agreement(sid)["script_overrides"]=={}
        click(ui,page.findChild(object,"creativeAgreementRefresh"))
        ui.wait(lambda:not editor.property("busy") and editor.property("showLatest"))
        assert field(editor,"creativeOverride_rhythm").property("text")=="未提交的节奏约定。"
        field(editor,"creativeProject_tone").setProperty("text","其他编辑刚保存的基调。")
        click(ui,dialog_action(ui,page,"creativeAgreementEditor","creativeAgreementSave"))
        ui.wait(lambda:not editor.property("busy") and ops.load_creative_agreement(sid)["script_overrides"].get("rhythm")=="未提交的节奏约定。")
        old_token=editor.property("token")
        old=ops.load_creative_agreement(sid)
        other_path=tmp_path/"other-agreement.nproj"
        other=Operations.create_graph_project(other_path)
        other_sid=create_fixture(other)
        ui.bridge.openProject(str(other_path))
        ui.wait(lambda:ui.bridge._project_path==other_path)
        assert not editor.property("visible") and not editor.property("loaded")
        ui.window.setProperty("currentPage","screenplay")
        QMetaObject.invokeMethod(page,"selectScreenplay",Q_ARG("QVariant",other_sid))
        ui.wait(lambda:plain(page.property("screenplay")).get("id")==other_sid)
        render_ready(ui)
        click(ui,page.findChild(object,"scriptCreativeAgreement"))
        ui.wait(lambda:editor.property("opened") and editor.property("loaded"))
        before=plain(editor.property("original"))
        ui.bridge.resultReady.emit("screenplay",{"operation":"load_creative_agreement","request_token":old_token,"result":old})
        ui.settle()
        assert plain(editor.property("original"))==before
        assert not any(other.load_creative_agreement(other_sid)["effective"].values())
