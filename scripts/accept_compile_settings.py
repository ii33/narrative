"""One real settings-only task; host fixture supplies all screenplay content."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_screenplay_compile import seed as scene_seed

TASK = """请只调整本工程的剧本编译排版设置，不修改正文、场表、原文或时间线。读取当前编译设置及工具字段合同，使用save_compile_settings保存：宋体，14pt，行高1.8倍；页边距上30、右24、下28、左26毫米；场景标题居中；动作段落左缩进8毫米、右缩进6毫米。其他设置保持当前值。使用真实revision与reason。保存后读取确认并报告完成，不需要写正文，也不需要由模型生成HTML或文档。"""


def seed(ops):
    fixture = scene_seed(ops)
    context = ops.read_script_unit_context(fixture["unit_id"])
    ops.submit_script_unit(fixture["unit_id"], [
        {"type": "action", "payload": {"text": "周川将铜钥匙放进林岚掌心。"}},
        {"type": "dialogue", "payload": {"speaker_label": "周川", "text": "编号 <A&B>，别记错。", "parenthetical": "压低声音", "delivery": "on_screen"}},
    ], context["expected_plan_revision"], context["expected_body_revision"], context["span_fingerprint"], context["context_fingerprint"], context["plan_fingerprint"], reason="设置验收受控合成正文，不是真实模型写作结果")
    fixture["before_screenplay"] = ops.read_screenplay(fixture["screenplay_id"])
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    from narrative.tool_api import TOOLS
    attempts = [s for s in audit["steps"] if s["tool"] in TOOLS and TOOLS[s["tool"]].writes]
    writes = [s for s in attempts if s.get("ok")]
    current = ops.load_compile_settings()
    directory = Path(report["audit_directory"])
    outputs = {fmt: ops.compile_screenplay(fixture["screenplay_id"], format=fmt, path=str(directory / ("settings-scene." + fmt))) for fmt in ("html", "pdf", "docx")}
    from docx import Document
    from pypdf import PdfReader
    doc = Document(directory / "settings-scene.docx")
    section = doc.sections[0]
    action = next(p for p in doc.paragraphs if "周川将铜钥匙" in p.text)
    scene = next(p for p in doc.paragraphs if p.text.startswith("1."))
    pdf_text = "\n".join(p.extract_text() for p in PdfReader(directory / "settings-scene.pdf").pages)
    report["settings_evidence"] = {"current": current, "attempted_writes": attempts, "outputs": outputs, "pdf_text": pdf_text, "docx_text": "\n".join(p.text for p in doc.paragraphs), "scope": "Settings tool and deterministic compile only; synthetic body is not a real writing result."}
    settings = current["settings"]
    def close(a, b):
        return abs(a - b) < 0.1
    return {
        "only_settings_write": len(writes) == 1 and writes[0]["tool"] == "save_compile_settings",
        "requested_font_and_line_height": settings["font_family"] == "宋体" and settings["font_size_pt"] == 14 and settings["line_height"] == 1.8,
        "requested_margins": settings["page_margins_mm"] == {"top": 30, "right": 24, "bottom": 28, "left": 26},
        "requested_paragraphs": settings["paragraphs"]["scene"]["alignment"] == "center" and settings["paragraphs"]["action"]["indent_left_mm"] == 8 and settings["paragraphs"]["action"]["indent_right_mm"] == 6,
        "all_formats_same_settings_snapshot": all(o["compile_settings"] == current for o in outputs.values()),
        "html_uses_settings": "font-size:" + str(settings["font_size_pt"]) + "pt" in outputs["html"]["text"] and "line-height:1.8" in outputs["html"]["text"] and "宋体" in outputs["html"]["text"],
        "docx_margins": all(close(getattr(section, key + "_margin").mm, value) for key, value in settings["page_margins_mm"].items()),
        "docx_action_indents": close(action.paragraph_format.left_indent.mm, 8) and close(action.paragraph_format.right_indent.mm, 6),
        "docx_scene_centered": int(scene.alignment) == 1,
        "pdf_body_preserved": "编号<A&B>，别记错。" in "".join(pdf_text.split()),
        "screenplay_content_unchanged": ops.read_screenplay(fixture["screenplay_id"]) == fixture["before_screenplay"],
        "compiler_settings_readonly": ops.load_compile_settings() == current,
    }


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = "compile-settings-real-tool/1"
    qa.FIXTURE_ORIGIN = "Operations-controlled synthetic source and screenplay body; real model task changes project compile settings only."
    return qa.main(validate=validate, required_tools=["load_compile_settings", "save_compile_settings"], use_writing_target=False,
                   prompt_factory=lambda _: TASK)


if __name__ == "__main__":
    raise SystemExit(main())
