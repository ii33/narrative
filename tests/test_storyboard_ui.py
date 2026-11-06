import pytest
from screenplay_scene_fixture import seed
from test_chat_scroll import transcript_shell
from test_screenplay_ui import dialog_action, render_ready, visuals
from test_static_check_ui import click, plain


@pytest.fixture(autouse=True)
def sketch_provider_stub(monkeypatch):
    from PIL import Image, ImageDraw

    from narrative import storyboard_style_provider

    calls = []

    def render(guide, prompt, copies_to, reference_images=None):
        calls.append({"guide": guide, "prompt": prompt, "destination": str(copies_to), "reference_images": reference_images})
        image = Image.new("RGB", (320, 180), "#e6dfcf")
        draw = ImageDraw.Draw(image)
        draw.rectangle((180, 20, 300, 155), outline="#526268", width=4)
        draw.ellipse((70, 25, 108, 63), fill="#a8b7b0", outline="#364b4d", width=3)
        draw.line((89, 63, 89, 130), fill="#364b4d", width=5)
        draw.line((89, 80, 190, 75), fill="#364b4d", width=4)
        image.save(copies_to)
        return {"provider": "isolated-test", "model": "stub-image", "path": str(copies_to)}

    monkeypatch.setattr(storyboard_style_provider, "render_sketch_image", render, raising=False)
    return calls


@pytest.fixture
def storyboard_diagnostics():
    from PySide6.QtCore import qInstallMessageHandler
    messages = []
    previous = qInstallMessageHandler(lambda kind, context, msg: messages.append(msg))
    try:
        yield
    finally:
        qInstallMessageHandler(previous)
    assert not [msg for msg in messages if "Storyboard" in msg or "StoryVideo" in msg]


def test_director_desk_tracks_bound_frames_and_plays_across_cut(tmp_path, monkeypatch, storyboard_diagnostics, sketch_provider_stub):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        screenplay_id, segment = seed(ops)
        unit = segment["units"][0]
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "screenplay")
        host = ui.window.findChild(object, "screenplayPage")
        QMetaObject.invokeMethod(host, "selectScreenplay", Q_ARG("QVariant", screenplay_id))
        ui.wait(lambda: plain(host.property("screenplay")).get("id") == screenplay_id)
        ui.window.setProperty("currentPage", "director")
        desk = ui.window.findChild(object, "directorDeskPage")
        ui.wait(lambda: desk.property("ready") and desk.property("unitId")==unit["id"])
        render_ready(ui)
        click(ui, desk.findChild(object, "storyboardAddShot"))
        editor = desk.findChild(object, "storyboardShotDialog")
        ui.wait(lambda: editor.property("opened"))
        desk.findChild(object, "storyboardShotTitle").setProperty("text", "安德烈敲窗")
        check = next(item for item in visuals(editor.property("contentItem")) if item.objectName() == "storyboardSourceBlock")
        click(ui, check)
        click(ui, dialog_action(ui, desk, "storyboardShotDialog", "storyboardSaveShot"))
        ui.wait(lambda: len(plain(desk.property("shots"))) == 1)
        first = ops.read_storyboard(unit["id"])["shots"][0]
        assert first["source_block_ids"] == [unit["blocks"][0]["id"]]
        click(ui, desk.findChild(object, "storyboardFrames"))
        ui.wait(lambda: desk.findChild(object, "storyboardFrameDialog").property("opened"))
        desk.findChild(object, "storyboardSketchPrompt").setProperty("text", "窗外人物抬手敲击，窗内驾驶者回望。")
        desk.findChild(object, "storyboardSketchSimpleColor").setProperty("checked", True)
        click(ui, dialog_action(ui, desk, "storyboardFrameDialog", "storyboardSketch"))
        ui.wait(lambda: desk.property("imagePath") and not desk.property("busy"))
        first_path = desk.property("imagePath")
        assert sketch_provider_stub and "窗外人物" in sketch_provider_stub[0]["prompt"]
        click(ui, desk.findChild(object, "storyboardFrames"))
        ui.wait(lambda: desk.findChild(object,"storyboardFrameDialog").property("opened"))
        assert desk.findChild(object,"storyboardSketchPrompt").property("text") == "窗外人物抬手敲击，窗内驾驶者回望。"
        assert desk.findChild(object,"storyboardSketchSimpleColor").property("checked")
        desk.findChild(object,"storyboardFrameDialog").setProperty("visible",False)
        board = ops.read_storyboard(unit["id"])
        board = ops.apply_storyboard_changes(unit["id"], board["revision"], [
            {"op":"update_shot","shot_id":first["id"],"duration_ms":500},
            {"op":"create_shot","title":"驾驶者抬头","duration_ms":900,"source_block_ids":[unit["blocks"][1]["id"]]}])
        second = board["shots"][1]
        ops.generate_storyboard_sketch(unit["id"], second["id"], 0, {"composition":"two_shot"}, board["revision"])
        QMetaObject.invokeMethod(desk, "reload")
        ui.wait(lambda: len(plain(desk.property("shots"))) == 2)
        render_ready(ui)
        click(ui,desk.findChild(object,"storyboardPlay"))
        ui.wait(lambda: desk.property("playing") and desk.property("shotIndex")==1)
        assert desk.property("imagePath") != first_path
        ui.wait(lambda: not desk.property("playing") and desk.property("playhead")==1400)
        click(ui,desk.findChild(object,"storyboardApprove"))
        ui.wait(lambda: desk.property("approved"))
        assert not desk.findChild(object,"storyboardEditShot").property("enabled")
        assert not ui.errors,ui.errors


def test_image_service_failure_keeps_frame_and_prompt_and_prevents_duplicate_job(tmp_path, monkeypatch, storyboard_diagnostics):
    from pathlib import Path
    from threading import Event

    from PIL import Image
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    entered, release = Event(), Event()
    calls = []

    def fail(guide, prompt, copies_to, reference_images=()):
        calls.append(prompt)
        entered.set()
        release.wait(5)
        raise ValueError('离线服务错误，未生成图片')

    monkeypatch.setattr('narrative.storyboard_style_provider.render_sketch_image', fail)
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        screenplay_id, segment = seed(ops)
        unit = segment['units'][0]
        board = ops.apply_storyboard_changes(unit['id'], 0, [{'op': 'create_shot', 'title': '等候', 'source_block_ids': [unit['blocks'][0]['id']]}])
        original = tmp_path / 'original.png'
        Image.new('RGB', (32, 18), 'blue').save(original)
        board = ops.apply_storyboard_changes(unit['id'], board['revision'], [{'op': 'set_keyframe', 'shot_id': board['shots'][0]['id'], 'offset_ms': 0, 'sketch_path': str(original)}])
        old_path = board['shots'][0]['keyframes'][0]['sketch_path']
        old_bytes = Path(old_path).read_bytes()
        ui.window.setProperty('assistantOpen', False)
        ui.window.setProperty('currentPage', 'screenplay')
        host = ui.window.findChild(object, 'screenplayPage')
        QMetaObject.invokeMethod(host, 'selectScreenplay', Q_ARG('QVariant', screenplay_id))
        ui.wait(lambda: plain(host.property('screenplay')).get('id') == screenplay_id)
        ui.window.setProperty('currentPage', 'director')
        desk = ui.window.findChild(object, 'directorDeskPage')
        ui.wait(lambda: desk.property('ready') and desk.property('unitId') == unit['id'])
        render_ready(ui)
        click(ui, desk.findChild(object, 'storyboardFrames'))
        dialog = desk.findChild(object, 'storyboardFrameDialog')
        ui.wait(lambda: dialog.property('opened'))
        prompt = desk.findChild(object, 'storyboardSketchPrompt')
        prompt.setProperty('text', '保留这个修改要求')
        button = dialog_action(ui, desk, 'storyboardFrameDialog', 'storyboardSketch')
        try:
            click(ui, button)
            ui.wait(lambda: entered.is_set() and desk.property('busy'))
            assert not button.property('enabled') and not prompt.property('enabled')
            click(ui, button)
            assert len(calls) == 1
        finally:
            release.set()
        ui.wait(lambda: not desk.property('busy'))
        assert dialog.property('opened') and '离线服务错误' in dialog.property('errorText')
        assert prompt.property('text') == '保留这个修改要求'
        assert desk.property('imagePath') == old_path and Path(old_path).read_bytes() == old_bytes
        assert ops.read_storyboard(unit['id']) == board
