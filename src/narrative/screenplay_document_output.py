"""Local PDF and editable Word output from the shared screenplay compilation."""
from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast
from xml.sax.saxutils import escape

MIMES = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def _paragraphs(elements):
    from .screenplay_compile import speaker_text

    for element in elements:
        kind = element["kind"]
        if kind == "dialogue":
            yield "speaker", speaker_text(element)
            if element.get("parenthetical"):
                yield "parenthetical", "（" + element["parenthetical"] + "）"
            yield "dialogue", element["text"]
        else:
            prefix = {"screen_text": "字幕：", "sound": "声音："}.get(kind, "")
            if kind == "scene":
                prefix = str(element["number"]) + ". "
            yield kind, prefix + element["text"]


def _style(settings, kind):
    key = kind if kind in {"scene", "speaker", "dialogue", "action", "scene_description"} else "dialogue" if kind == "parenthetical" else "action"
    style = dict(settings["paragraphs"][key])
    if kind == "parenthetical":
        style.update(space_before_pt=0, space_after_pt=0)
    elif kind == "title":
        style.update(alignment="center", space_before_pt=0, space_after_pt=24)
    elif kind == "transition":
        style["alignment"] = "right"
    return style


def _font_size(settings, kind):
    return settings["font_size_pt"] * (22 / 12 if kind == "title" else 16 / 12 if kind in {"episode", "act"} else 1)


def _write_docx(elements, title, path, settings):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor
    from docx.styles.style import ParagraphStyle

    document = Document()
    document.core_properties.title = title
    document.core_properties.author = ""
    # The bundled Word template carries decorative accent rules on Title.
    # A screenplay uses plain black type and whitespace across all outputs.
    for border in document.styles.element.xpath(".//w:pBdr"):
        border.getparent().remove(border)
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    for side, value in settings["page_margins_mm"].items():
        setattr(section, side + "_margin", Mm(value))
    normal = cast(ParagraphStyle, document.styles["Normal"])
    normal.font.name = settings["font_family"]
    normal.font.size = Pt(settings["font_size_pt"])
    normal.font.color.rgb = RGBColor(0, 0, 0)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), settings["font_family"])
    normal.paragraph_format.line_spacing = settings["line_height"]
    for kind, text in _paragraphs(elements):
        paragraph = document.add_paragraph()
        fmt = paragraph.paragraph_format
        fmt.widow_control = True
        run = paragraph.add_run(text)
        if kind == "title":
            paragraph.style = "Title"
        style = _style(settings, kind)
        paragraph.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
                               "right": WD_ALIGN_PARAGRAPH.RIGHT, "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}[style["alignment"]]
        fmt.left_indent, fmt.right_indent = Mm(style["indent_left_mm"]), Mm(style["indent_right_mm"])
        fmt.space_before, fmt.space_after = Pt(style["space_before_pt"]), Pt(style["space_after_pt"])
        fmt.line_spacing = settings["line_height"]
        fmt.keep_with_next = kind in {"title", "episode", "act", "segment", "scene", "speaker", "parenthetical"}
        run.bold = kind in {"title", "episode", "act", "segment", "scene", "speaker"}
        run.font.size = Pt(_font_size(settings, kind))
        run.font.color.rgb = RGBColor(0, 0, 0)
        run.font.name = settings["font_family"]
        run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), settings["font_family"])
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    document.save(path)


def _write_pdf(elements, title, path, settings):
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import CondPageBreak, Flowable, Paragraph, SimpleDocTemplate

    from .screenplay_fonts import resolve_font
    font_path, font_index = resolve_font(settings["font_family"])
    font = "Screenplay-" + settings["font_family"]
    if font not in pdfmetrics.getRegisteredFontNames():
        try:
            pdfmetrics.registerFont(TTFont(font, font_path, subfontIndex=font_index))
        except Exception as error:
            raise ValueError(f"字体“{settings['font_family']}”无法嵌入PDF，请选择支持TrueType轮廓的字体：{error}") from error
    styles = {}
    for kind in {row[0] for row in _paragraphs(elements)}:
        style = _style(settings, kind)
        size = _font_size(settings, kind)
        options = {"fontName": font, "fontSize": size, "leading": size * settings["line_height"], "wordWrap": "CJK",
                   "alignment": {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT, "justify": TA_JUSTIFY}[style["alignment"]],
                   "leftIndent": style["indent_left_mm"] * mm, "rightIndent": style["indent_right_mm"] * mm,
                   "spaceBefore": style["space_before_pt"], "spaceAfter": style["space_after_pt"],
                   "keepWithNext": False}
        styles[kind] = ParagraphStyle(kind, **options)
    story: list[Flowable] = []
    for kind, text in _paragraphs(elements):
        if kind in {"title", "episode", "act", "segment", "scene", "speaker"}:
            # Reserve the heading and a few following lines, rather than
            # forcing an entire multi-page dialogue onto the next page.
            style = styles[kind]
            lines = 4 if kind == "speaker" else 3
            story.append(CondPageBreak(style.leading * lines + style.spaceBefore + style.spaceAfter))
        story.append(Paragraph(escape(text).replace("\n", "<br/>"), styles[kind]))

    def page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 9)
        canvas.drawRightString(A4[0] - settings["page_margins_mm"]["right"] * mm, max(5, settings["page_margins_mm"]["bottom"] / 2) * mm, str(doc.page))
        canvas.restoreState()

    margins = settings["page_margins_mm"]
    document = SimpleDocTemplate(str(path), pagesize=A4, topMargin=margins["top"] * mm, bottomMargin=margins["bottom"] * mm,
                                 leftMargin=margins["left"] * mm, rightMargin=margins["right"] * mm, title=title, author="")
    document.build(story, onFirstPage=page_number, onLaterPages=page_number)


def write_document(elements: list[dict], title: str, format: str, path: str, settings: dict | None = None) -> dict:
    from .compile_settings import CompileSettings
    settings = CompileSettings.model_validate(settings or {}).model_dump()
    if format not in MIMES:
        raise ValueError("文档输出格式仅支持pdf或docx")
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, suffix="." + format, delete=False) as output:
        temporary = Path(output.name)
    try:
        if format == "docx":
            _write_docx(elements, title, temporary, settings)
        else:
            _write_pdf(elements, title, temporary, settings)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(target), "mime": MIMES[format], "bytes": target.stat().st_size}
