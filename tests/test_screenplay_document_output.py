"""Binary output preserves literal screenplay content and spans pages safely."""
import re
from pathlib import Path

import pytest
from docx import Document
from pypdf import PdfReader

from narrative.screenplay_document_output import write_document


def sample_elements():
    return [{"kind": "title", "text": "雨夜交接"},
            {"kind": "segment", "text": "开场"},
            {"kind": "scene", "number": 1, "text": "内景 - 资料室 - 夜"},
            {"kind": "action", "text": "林岚拿起写着 <保留> & 原样的纸条。\n雨声忽然停了。"},
            {"kind": "dialogue", "speaker": "林岚", "parenthetical": "压低声音", "delivery": "voice_over",
             "text": "带好钥匙。" + "门外传来脚步声，她停下来等候。" * 700},
            {"kind": "screen_text", "text": "三分钟后"},
            {"kind": "sound", "text": "电话铃响"},
            {"kind": "transition", "text": "淡出"}]


def test_binary_formats_preserve_literals_and_long_dialogue(tmp_path):
    elements = sample_elements()
    for format in ("pdf", "docx"):
        output = tmp_path / ("剧本." + format)
        result = write_document(elements, "雨夜交接", format, str(output))
        assert result["bytes"] > 1000 and Path(result["path"]) == output
        if format == "pdf":
            reader = PdfReader(output)
            assert len(reader.pages) > 2
            text = "\n".join(re.sub(r"(?m)^\d+\s*$", "", page.extract_text()) for page in reader.pages)
        else:
            document = Document(str(output))
            text = "\n".join(p.text for p in document.paragraphs)
        compact = "".join(text.split())
        for literal in ("<保留>&原样", "林岚(V.O.)", "压低声音", "带好钥匙。", "1.内景-资料室-夜", "字幕：三分钟后", "声音：电话铃响", "淡出"):
            assert literal in compact
        assert compact.count("门外传来脚步声，她停下来等候。") == 700


def test_failed_document_generation_preserves_existing_file(tmp_path, monkeypatch):
    from narrative import screenplay_document_output as output
    target = tmp_path / "existing.pdf"
    target.write_bytes(b"keep existing output")

    def fail(*args):
        raise ValueError("render failed")

    monkeypatch.setattr(output, "_write_pdf", fail)
    with pytest.raises(ValueError, match="render failed"):
        output.write_document(sample_elements(), "雨夜交接", "pdf", str(target))
    assert target.read_bytes() == b"keep existing output"
    assert list(tmp_path.iterdir()) == [target]


def test_saved_typography_controls_word_and_pdf(tmp_path):
    from narrative.compile_settings import default_settings
    settings = default_settings()
    settings.update(font_family="宋体", font_size_pt=17, line_height=1.8)
    settings["page_margins_mm"] = {"top": 30, "right": 24, "bottom": 28, "left": 26}
    settings["paragraphs"]["action"].update(alignment="justify", indent_left_mm=7, indent_right_mm=4,
                                            space_before_pt=6, space_after_pt=9)
    elements = sample_elements()[:4]
    word = tmp_path / "custom.docx"
    pdf = tmp_path / "custom.pdf"
    write_document(elements, "雨夜交接", "docx", str(word), settings)
    write_document(elements, "雨夜交接", "pdf", str(pdf), settings)
    document = Document(str(word))
    assert document.sections[0].top_margin.mm == pytest.approx(30, abs=.03)
    assert document.sections[0].left_margin.mm == pytest.approx(26, abs=.03)
    action = document.paragraphs[-1]
    assert action.runs[0].font.name == "宋体" and action.runs[0].font.size.pt == 17
    assert action.paragraph_format.line_spacing == 1.8
    assert action.paragraph_format.left_indent.mm == pytest.approx(7, abs=.03)
    assert action.paragraph_format.space_after.pt == 9
    sizes = []
    PdfReader(pdf).pages[0].extract_text(visitor_text=lambda text, cm, tm, font, size: sizes.append(size) if text.strip() else None)
    assert 17 in sizes


def test_missing_pdf_font_is_explicit_and_preserves_target(tmp_path, monkeypatch):
    from narrative import screenplay_fonts
    from narrative.compile_settings import default_settings
    monkeypatch.setattr(screenplay_fonts, "_font_roots", lambda: [])
    settings = default_settings()
    settings["font_family"] = "Missing project font"
    target = tmp_path / "previous.pdf"
    target.write_bytes(b"previous")
    with pytest.raises(ValueError, match="未找到字体"):
        write_document(sample_elements(), "雨夜交接", "pdf", str(target), settings)
    assert target.read_bytes() == b"previous"


def test_scene_description_uses_independent_paragraph_layout(tmp_path):
    from narrative.compile_settings import default_settings
    settings = default_settings()
    settings['paragraphs']['scene_description'].update(indent_left_mm=12, alignment='center')
    settings['paragraphs']['action'].update(indent_left_mm=3, alignment='left')
    elements = [{'kind': 'scene_description', 'text': '窗外的雨幕遮住站台。'},
                {'kind': 'action', 'text': '林岚把钥匙放进衣袋。'}]
    for format in ['pdf', 'docx']:
        write_document(elements, '交接', format, str(tmp_path / ('separate.' + format)), settings)
    document = Document(str(tmp_path / 'separate.docx'))
    description, action = document.paragraphs
    assert description.paragraph_format.left_indent.mm == pytest.approx(12, abs=.03)
    assert action.paragraph_format.left_indent.mm == pytest.approx(3, abs=.03)
    assert description.alignment != action.alignment
    text = PdfReader(tmp_path / 'separate.pdf').pages[0].extract_text()
    assert '窗外的雨幕遮住站台。' in text and '林岚把钥匙放进衣袋。' in text
