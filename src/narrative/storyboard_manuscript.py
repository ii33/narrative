"""Portable scene manuscript export. Page slots and shot time are independent."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from html import escape
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps


@dataclass
class Figure:
    label: str
    note: str
    png: bytes | None = None


@dataclass
class ShotPage:
    heading: str
    paragraphs: list[str] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)


def panel_letter(index: int) -> str:
    result = ""
    while True:
        index, digit = divmod(index, 26)
        result = chr(65 + digit) + result
        if index == 0:
            return result
        index -= 1


def _thumbnail(image):
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((1280, 720))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def manuscript(board, unit, illustrated, annotated):
    plan = unit.get("plan", {})
    title = f"分镜稿 · {plan.get('title') or unit['id']}"
    interior = {"INT": "内景", "EXT": "外景", "INT_EXT": "内外景"}.get(plan.get("interior_exterior"), "")
    location = " · ".join(filter(None, [interior, plan.get("location_text"), plan.get("time_label")]))
    heading = f"场 {unit.get('scene_number') or unit['id']}　{location}　稿次 {board['revision']}"
    pages = []
    last_heard = {}
    sound_by_id = {row["id"]: row for row in board.get("sound_intents", [])}
    for number, shot in enumerate(board["shots"], 1):
        page = ShotPage(f"{number:02d}　{shot['title']}　({shot['duration_ms']/1000:g} 秒)")
        pages.append(page)
        if shot.get("description"):
            page.paragraphs.append(shot["description"])
        if shot.get("shot_language"):
            page.paragraphs.append("镜头　" + shot["shot_language"])
        for element in shot.get("visual_elements", []):
            details = " · ".join(filter(None, [element["name"], element.get("usage"), element.get("asset_id")]))
            page.paragraphs.append("视觉　" + details)
        for key in shot.get("sound_intent_ids", []):
            cue = sound_by_id[key]
            tag = {"dialogue": "对白", "effect": "声音", "silence": "静默"}[cue["kind"]]
            speaker = cue.get("speaker") or ""
            continued = "（续）" if last_heard.get(key) == number - 1 else ""
            page.paragraphs.append(f"{tag}　{speaker}{continued}　{cue['text']}")
            last_heard[key] = number
        if shot.get("sound_notes"):
            page.paragraphs.append("本镜听感　" + shot["sound_notes"])
        for sound in shot.get("sounds", []):
            if sound.get("label"):
                page.paragraphs.append("声轨　" + sound["label"])
        panels = shot.get("panels", [])
        for i, panel in enumerate(panels):
            figure = Figure(f"{number:02d}{panel_letter(i)}", panel.get("intent") or "待补画格说明")
            if illustrated:
                from .storyboard_panels import render_panel
                figure.png = _thumbnail(render_panel(board, shot, panel, annotated=annotated))
            page.figures.append(figure)
        # Existing shots may have timed images without authored drawing panels.
        if not panels:
            for i, frame in enumerate(shot.get("keyframes", [])):
                figure = Figure(f"{number:02d}{panel_letter(i)}", f"镜内 {frame['offset_ms']/1000:g} 秒")
                if illustrated and frame.get("sketch_path"):
                    with Image.open(frame["sketch_path"]) as image:
                        figure.png = _thumbnail(image)
                page.figures.append(figure)
    return title, heading, pages


def _plain(title, heading, pages):
    lines = [title, heading]
    for page in pages:
        lines.extend(["", page.heading, *page.paragraphs])
        lines.extend(f"{f.label}　{f.note}" for f in page.figures)
    return "\n".join(lines) + "\n"


def _html(title, heading, pages, illustrated):
    def safe(part):
        return escape(part).replace("\n", "<br>")
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
             f'<title>{safe(title)}</title><style>body{{max-width:840px;margin:auto;padding:32px;font:16px/1.6 sans-serif;color:#233833}}article{{border-top:1px solid #bac8c2}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}figure{{margin:0;break-inside:avoid}}.frame{{aspect-ratio:16/9;background:#f1f1ec;display:grid;place-items:center}}img{{width:100%;height:100%;object-fit:contain}}p{{white-space:normal}}</style>',
             f'<h1>{safe(title)}</h1><p>{safe(heading)}</p>']
    for page in pages:
        parts.extend([f"<article><h2>{safe(page.heading)}</h2>", *(f"<p>{safe(line)}</p>" for line in page.paragraphs), '<div class="grid">'])
        for f in page.figures:
            parts.append("<figure>")
            if illustrated:
                img = '<img alt="" src="data:image/png;base64,' + base64.b64encode(f.png).decode() + '">' if f.png else "待绘"
                parts.append(f'<div class="frame">{img}</div>')
            parts.append(f"<figcaption>{safe(f.label)}　{safe(f.note)}</figcaption></figure>")
        parts.append("</div></article>")
    return "".join(parts) + "</html>"


def _pdf(title, heading, pages, illustrated, font_family):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    from .screenplay_fonts import resolve_font

    font = "Screenplay-" + font_family
    if font not in pdfmetrics.getRegisteredFontNames():
        path, index = resolve_font(font_family)
        pdfmetrics.registerFont(TTFont(font, path, subfontIndex=index))
    stream = BytesIO()
    style = ParagraphStyle("body", fontName=font, fontSize=11, leading=17, wordWrap="CJK", spaceAfter=4)
    sub = ParagraphStyle("shot", parent=style, fontSize=14, leading=20, spaceBefore=10, keepWithNext=True)
    from reportlab.platypus import Flowable, PageBreak

    class Grid(Flowable):
        """Fixed picture coordinates on each illustrated appendix page."""

        def __init__(self, figures):
            super().__init__()
            self.width, self.height = 500, 600
            self.figures = figures

        def draw(self):
            for i, f in enumerate(self.figures):
                x = (i % 2) * 250
                y = 440 - (i // 2) * 185
                self.canv.setStrokeColor(colors.HexColor("#87968c"))
                if f.png:
                    self.canv.drawImage(ImageReader(BytesIO(f.png)), x, y, 234, 131.625,
                                        preserveAspectRatio=True, anchor="c")
                self.canv.rect(x, y, 234, 131.625)
                self.canv.setFont(font, 11)
                caption = f.label + "　" + f.note.replace("\n", " ")
                rest = caption
                for line in range(2):
                    end = 1
                    while end <= len(rest) and pdfmetrics.stringWidth(rest[:end], font, 11) <= 231:
                        end += 1
                    text, rest = rest[:end - 1], rest[end - 1:]
                    if line == 1 and rest:
                        text = text[:-1] + "…"
                    self.canv.drawString(x, y - 16 - line * 14, text)
                    if not rest:
                        break
    def paragraph(s, spec=style):
        return Paragraph(escape(s).replace("\n", "<br/>"), spec)

    story = [paragraph(heading)]
    figures = []
    for page in pages:
        story.append(paragraph(page.heading, sub))
        story.extend(paragraph(line) for line in page.paragraphs)
        story.extend(paragraph(f"{f.label}　{f.note}") for f in page.figures)
        figures.extend(page.figures)
    if illustrated:
        for offset in range(0, len(figures), 6):
            story.extend([PageBreak(), paragraph("画格　" + heading, sub), Grid(figures[offset:offset + 6])])

    def decorate(c: Canvas, doc):
        c.setFont(font, 11)
        c.drawString(42, A4[1] - 34, title)
        c.drawRightString(A4[0] - 42, 28, str(doc.page))
    doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=42, rightMargin=42, topMargin=52, bottomMargin=44, title=title)
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return stream.getvalue()


def export_scene(ops, unit_id, format, path, illustrated=True, annotated=True):
    extensions = {"text": ".txt", "pdf": ".pdf", "html": ".html"}
    if format not in extensions or not isinstance(path, str) or not path.strip():
        raise ValueError("请指定 TXT、HTML 或 PDF 的导出文件")
    if type(illustrated) is not bool or type(annotated) is not bool:
        raise ValueError("图文与标注选项须为布尔值")
    target = Path(path).resolve()
    if target.suffix.lower() != extensions[format]:
        raise ValueError("文件扩展名应为 " + extensions[format])
    board = ops.read_storyboard(unit_id)
    unit = ops.read_script_unit_context(unit_id)["unit"]
    illustrated = illustrated and format != "text"
    title, heading, pages = manuscript(board, unit, illustrated, annotated)
    if format == "text":
        data = _plain(title, heading, pages).encode("utf-8")
    elif format == "html":
        data = _html(title, heading, pages, illustrated).encode("utf-8")
    else:
        data = _pdf(title, heading, pages, illustrated, ops.load_compile_settings()["settings"]["font_family"])
    if ops.read_storyboard(unit_id)["revision"] != board["revision"]:
        raise ValueError("导出期间分镜已修改，请重新导出")
    with target.open("xb") as output:
        try:
            output.write(data)
        except OSError:
            output.close()
            target.unlink(missing_ok=True)
            raise
    return {"path": str(target), "format": format, "revision": board["revision"], "shots": len(pages)}
