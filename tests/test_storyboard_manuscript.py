"""Text-first manuscripts and portable exports remain independent of the time rail."""
import base64
import re
from io import BytesIO

import pytest
from PIL import Image
from pypdf import PdfReader
from test_screenplay_writing import project as project
from test_storyboard_operations import setup_board


def test_export_scene_without_images_and_protect_existing_copy(project, tmp_path):
    ops, unit, _ = setup_board(project)
    board = ops.apply_storyboard_changes(unit, 0, [{"op": "create_sound_intent", "kind": "dialogue", "speaker": "安德烈", "text": "有人吗？"}])
    intent = board["sound_intents"][0]["id"]
    ops.apply_storyboard_changes(unit, board["revision"], [
        {"op": "create_shot", "title": "街上", "description": "安德烈停住。", "shot_language": "固定中景", "sound_intent_ids": [intent]},
        {"op": "create_shot", "title": "车内", "description": "录音继续播放。", "sound_intent_ids": [intent]}])
    path = tmp_path / "读稿.txt"
    ops.export_storyboard_script(unit, "text", str(path))
    text = path.read_text(encoding="utf-8")
    assert text.index("安德烈停住。") < text.index("录音继续播放。")
    assert "固定中景" in text and "安德烈（续）" in text
    with pytest.raises(FileExistsError):
        ops.export_storyboard_script(unit, "text", str(path))
    assert path.read_text(encoding="utf-8") == text


def test_pdf_and_html_embed_independent_panels_before_scheduling(project, tmp_path):
    ops, unit, _ = setup_board(project)
    image = tmp_path / "底图.png"
    Image.new("RGB", (320, 180), "teal").save(image)
    shot = ops.apply_storyboard_changes(unit, 0, [{"op": "create_shot", "title": "敲窗"}])["shots"][0]["id"]
    ops.apply_storyboard_changes(unit, 1, [
        {"op": "create_panel", "shot_id": shot, "intent": "指尖接触玻璃", "base": {"kind": "image", "path": str(image)}},
        {"op": "create_panel", "shot_id": shot, "intent": "待绘反应"}])
    html = tmp_path / "稿.html"
    result = ops.export_storyboard_script(unit, "html", str(html))
    markup = html.read_text(encoding="utf-8")
    images = re.findall(r'data:image/png;base64,([^"]+)', markup)
    assert len(images) == 2 and result["revision"] == 2 and "待绘反应" in markup
    with Image.open(BytesIO(base64.b64decode(images[0]))) as saved:
        assert saved.getpixel((100, 100)) == (0, 128, 128)
    pdf = tmp_path / "稿.pdf"
    ops.export_storyboard_script(unit, "pdf", str(pdf))
    reader = PdfReader(pdf)
    assert len(reader.pages) == 2 and "指尖接触玻璃" in reader.pages[0].extract_text()
    assert "01A" in reader.pages[1].extract_text() and len(reader.pages[1].images) == 2

    # ReportLab still embeds an image painted outside the page. Check its actual placement.
    located_inside_page = []
    def visit(operator, operands, cm, tm):
        if operator == b"Do":
            located_inside_page.append(cm[5] >= 0 and cm[5] + cm[3] <= float(reader.pages[1].mediabox.height))
    reader.pages[1].extract_text(visitor_operand_before=visit)
    assert located_inside_page == [True, True]
