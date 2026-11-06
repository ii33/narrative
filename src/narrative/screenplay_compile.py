"""Deterministic screenplay compilation and portable text renderers."""
from __future__ import annotations

import html
import json


def compile_elements(state, document, timeline, children, require):
    elements = [{"kind": "title", "text": document["screenplay"]["title"]}]
    diagnostics = []
    segments = {s["id"]: s for s in document["segments"]}
    names = {n["id"]: n["name"] for n in timeline["nodes"]}
    number = 0

    def emit(kind, identifier):
        nonlocal number
        for entry in children(state, kind, identifier):
            child_kind, key = entry["child_kind"], entry["child_id"]
            row = require(state, child_kind, key)
            if child_kind in {"episode", "act"}:
                heading = row["title"]
                if child_kind == "episode":
                    prefix = "第" + str(row["number"]) + "集"
                    if not heading.startswith(prefix):
                        heading = prefix + " " + heading
                elements.append({"kind": child_kind, "text": heading, "id": key})
                emit(child_kind, key)
                continue
            segment = segments[key]
            if not segment["enabled"]:
                if any(u.get("blocks") for u in segment["units"]):
                    diagnostics.append({"severity": "info", "code": "COMPILE_DISABLED_BODY", "message": "未启用段落的正文未纳入编译", "segment_id": key})
                continue
            elements.append({"kind": "segment", "text": segment["title"], "segment_id": key})
            for unit in segment["units"]:
                if not unit["enabled"]:
                    if unit.get("blocks"):
                        diagnostics.append({"severity": "info", "code": "COMPILE_DISABLED_BODY", "message": "未启用场的正文未纳入编译", "unit_id": unit["id"]})
                    continue
                number += 1
                plan = unit["plan"]
                location = plan.get("location_text") or names.get(plan.get("location_id"), "")
                heading = " — ".join(str(x) for x in (plan.get("interior_exterior"), location, plan.get("time_label")) if x and x != "UNSPECIFIED")
                elements.append({"kind": "scene", "text": heading or plan.get("title") or "画面段", "number": number,
                                 "source_scene_number": unit.get("scene_number"), "unit_id": unit["id"], "segment_id": key})
                if not unit.get("blocks"):
                    diagnostics.append({"severity": "warning", "code": "COMPILE_BODY_EMPTY", "message": "启用场尚无正文", "unit_id": unit["id"]})
                for block in unit.get("blocks", []):
                    payload = block["payload"]
                    element = {"kind": block["type"], "text": payload.get("text") or payload.get("kind", ""),
                               "unit_id": unit["id"], "block_id": block["id"]}
                    if block["type"] == "dialogue":
                        element.update(speaker=payload.get("speaker_label") or names.get(payload.get("speaker_id"), "未绑定人物"),
                                       parenthetical=payload.get("parenthetical", ""), delivery=payload.get("delivery", "on_screen"))
                        if payload.get("simultaneous_group_id"):
                            element["simultaneous_group_id"] = payload["simultaneous_group_id"]
                    elements.append(element)
    emit("screenplay", document["screenplay"]["id"])
    return elements, diagnostics


def speaker_text(element):
    suffix = {"off_screen": "O.S.", "voice_over": "V.O.", "inner_voice": "内心声"}.get(element.get("delivery"), "")
    return element["speaker"] + (" (" + suffix + ")" if suffix else "") + ("（同时）" if element.get("simultaneous_group_id") else "")


def render_text(elements, fountain=False):
    lines = []
    for e in elements:
        kind, text = e["kind"], e["text"]
        if kind == "title":
            lines.append(("Title: " if fountain else "") + text)
        elif kind in {"episode", "act", "segment"}:
            lines.extend(["", ("# " if fountain else "") + text])
        elif kind == "scene":
            lines.extend(["", ("." + text + " #" + str(e["number"]) + "#") if fountain else str(e["number"]) + ". " + text])
        elif kind == "dialogue":
            lines.extend(["", ("@" if fountain else "    ") + speaker_text(e)])
            if e.get("parenthetical"):
                lines.append(("" if fountain else "    ") + "(" + e["parenthetical"] + ")")
            lines.append(("" if fountain else "    ") + text)
        elif kind == "transition":
            lines.extend(["", ("> " if fountain else "转场：") + text])
        else:
            prefix = {"screen_text": "字幕：", "sound": "声音："}.get(kind, "")
            lines.extend(["", ("!" if fountain else "") + prefix + text])
    return "\n".join(lines) + "\n"


def render_html(elements, title, settings=None):
    parts = []
    def escape(text):
        return html.escape(str(text), quote=True).replace("\n", "<br>")
    for e in elements:
        kind, text = e["kind"], escape(e["text"])
        if kind == "dialogue":
            content = '<p class="speaker">' + escape(speaker_text(e)) + '</p>'
            if e.get("parenthetical"):
                content += '<p class="parenthetical">(' + escape(e["parenthetical"]) + ')</p>'
            content += '<p class="dialogue">' + text + '</p>'
            parts.append(content)
        elif kind in {"title", "episode", "act", "segment", "scene"}:
            tag = "h1" if kind == "title" else "h2" if kind in {"episode", "act"} else "h3"
            parts.append('<' + tag + ' class="' + kind + '">' + (str(e["number"]) + '. ' if kind == "scene" else '') + text + '</' + tag + '>')
        else:
            prefix = {"screen_text": "字幕：", "sound": "声音："}.get(kind, "")
            parts.append('<p class="' + kind + '">' + prefix + text + '</p>')
    return '<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>' + html.escape(title) + '</title><style>' + CSS + typography_css(settings) + '</style></head><body><main>' + '\n'.join(parts) + '</main></body></html>'


CSS = '''@page{size:A4;margin:25mm 23mm}body{font-family:"Noto Serif CJK SC","SimSun",serif;color:#111;background:white;font-size:12pt;line-height:1.65}main{max-width:170mm;margin:auto}h1{text-align:center;margin:2em 0}h2{margin-top:2em}h3{font-size:12pt;page-break-after:avoid}p{white-space:normal;margin:1em 0}.dialogue{margin:1em 16% 1em 24%;page-break-inside:avoid}.speaker{text-align:center;font-weight:bold}.parenthetical{text-align:center}.transition{text-align:right;font-weight:bold}.screen_text{text-align:center}.sound{font-style:italic}@media print{body{margin:0}main{max-width:none}.episode{break-before:page}}'''


def json_text(document):
    return json.dumps(document, ensure_ascii=False, indent=2)


def render_qt_html(elements, settings=None):
    """Qt uses CSS pixels for paragraph margins; each semantic paragraph is independent."""
    parts = ['<html><head><style>' + typography_css(settings) + '</style></head><body>']
    def escaped(value):
        return html.escape(str(value)).replace("\n", "<br>")
    for e in elements:
        kind, text = e["kind"], escaped(e["text"])
        if kind == "dialogue":
            content = '<p ' + qt_style(settings, 'speaker') + '><b>' + escaped(speaker_text(e)) + '</b></p>'
            if e.get("parenthetical"):
                content += '<p ' + qt_style(settings, 'dialogue', parenthetical=True) + '>(' + escaped(e["parenthetical"]) + ')</p>'
            content += '<p ' + qt_style(settings, 'dialogue') + '>' + text + '</p>'
            parts.append(content)
        elif kind in {"title", "episode", "act", "segment", "scene"}:
            tag = "h1" if kind == "title" else "h2" if kind in {"episode", "act"} else "h3"
            parts.append('<' + tag + (' align="center"' if kind == 'title' else (' ' + qt_style(settings, 'scene') if kind == 'scene' else '')) + '>' + (str(e["number"]) + '. ' if kind == 'scene' else '') + text + '</' + tag + '>')
        else:
            prefix = {"screen_text": "字幕：", "sound": "声音："}.get(kind, "")
            parts.append('<p ' + (qt_style(settings, kind) if kind in {'action', 'scene_description'} else 'align="' + ('right' if kind == 'transition' else 'left') + '"') + '>' + prefix + text + '</p>')
    return ''.join(parts) + '</body></html>'


def typography_css(settings=None):
    from .compile_settings import default_settings
    settings = settings or default_settings()
    m = settings['page_margins_mm']
    css = '@page{margin:' + ' '.join(str(m[k]) + 'mm' for k in ['top', 'right', 'bottom', 'left']) + '}'
    css += 'body{font-family:' + json.dumps(settings['font_family'], ensure_ascii=False) + ';font-size:' + str(settings['font_size_pt']) + 'pt;line-height:' + str(settings['line_height']) + '}'
    size = settings['font_size_pt']
    css += 'h1{font-size:' + str(size * 22 / 12) + 'pt}h2{font-size:' + str(size * 16 / 12) + 'pt}h3{font-size:' + str(size) + 'pt}'
    css += 'main{max-width:' + str(210-m['left']-m['right']) + 'mm}'
    for kind, row in settings['paragraphs'].items():
        css += '.' + kind + '{' + paragraph_css(row) + '}'
    css += '.parenthetical{' + paragraph_css(settings['paragraphs']['dialogue'] | {'space_before_pt': 0, 'space_after_pt': 0}) + '}'
    return css


def paragraph_css(row):
    return 'text-align:' + row['alignment'] + ';margin-left:' + str(row['indent_left_mm']) + 'mm;margin-right:' + str(row['indent_right_mm']) + 'mm;margin-top:' + str(row['space_before_pt']) + 'pt;margin-bottom:' + str(row['space_after_pt']) + 'pt'


def qt_style(settings, kind, parenthetical=False):
    from .compile_settings import default_settings
    row = (settings or default_settings())['paragraphs'][kind]
    if parenthetical:
        row = row | {'space_before_pt': 0, 'space_after_pt': 0}
    css = 'text-align:' + row['alignment']
    for side, key in [('left', 'indent_left_mm'), ('right', 'indent_right_mm')]:
        css += ';margin-' + side + ':' + str(row[key] * 96 / 25.4) + 'px'
    for side, key in [('top', 'space_before_pt'), ('bottom', 'space_after_pt')]:
        css += ';margin-' + side + ':' + str(row[key] * 96 / 72) + 'px'
    return 'align="' + row['alignment'] + '" style="' + css + '"'
