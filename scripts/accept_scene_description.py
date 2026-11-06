"""Single real scene-description/action separation task; no service retries."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_screenplay_compile import seed as scene_seed

TASK = """请只为指定场次写三个简短正文块，总字数少于150字：一个scene_description描述环境、空间或光线；一个action写人物具体行为；一个dialogue绑定真实人物。保持钥匙交接这一场剧情，不改场表、历史正文以外的数据或排版设置。先read_script_unit_context，再按真实修订和指纹submit_script_unit一次提交。环境描写与人物行为分块，不生成HTML或文档，不需额外格式探针；完成即报告。"""


def seed(ops):
    fixture = scene_seed(ops)
    current = ops.load_compile_settings()
    settings = current["settings"]
    settings["paragraphs"]["scene_description"].update(indent_left_mm=12, indent_right_mm=9)
    settings["paragraphs"]["action"].update(indent_left_mm=3, indent_right_mm=2)
    fixture["compile_settings"] = ops.save_compile_settings(settings, current["revision"], "验收两种正文块独立排版")
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    unit = ops.read_script_segment(fixture["segment_id"])["units"][0]
    blocks = unit["blocks"]
    directory = Path(report["audit_directory"])
    audit = json.loads((directory / "task.json").read_text(encoding="utf-8"))
    from narrative.tool_api import TOOLS
    writes = [s for s in audit["steps"] if s.get("ok") and s["tool"] in TOOLS and TOOLS[s["tool"]].writes]
    compiled = {fmt: ops.compile_screenplay(fixture["screenplay_id"], format=fmt, path=str(directory / ("scene-description." + fmt))) for fmt in ("html", "pdf", "docx")}
    from docx import Document
    from pypdf import PdfReader
    doc = Document(directory / "scene-description.docx")
    pdf_text = "".join(p.extract_text() for p in PdfReader(directory / "scene-description.pdf").pages)
    paragraphs = {kind: next((p for p in doc.paragraphs if p.text == b["payload"]["text"]), None) for kind in ("scene_description", "action") for b in blocks if b["type"] == kind}
    report["scene_description_evidence"] = {"blocks": blocks, "compiled": compiled, "pdf_text": pdf_text, "writes": writes, "scope": "One short real scene; semantic classification requires reading actual body. Not a migration of historical action blocks."}
    return {
        "one_target_body_submit": len(writes) == 1 and writes[0]["tool"] == "submit_script_unit" and writes[0]["args"]["unit_id"] == fixture["unit_id"],
        "three_distinct_blocks": len(blocks) == 3 and {b["type"] for b in blocks} == {"scene_description", "action", "dialogue"},
        "short_body": sum(len(b["payload"].get("text", "")) for b in blocks) < 150,
        "types_preserved_in_compilation": all({"scene_description", "action", "dialogue"}.issubset({e["kind"] for e in result["elements"]}) for result in compiled.values()),
        "docx_distinct_indents": all(paragraphs.get(kind) is not None and abs(paragraphs[kind].paragraph_format.left_indent.mm - value) < 0.1 for kind, value in (("scene_description", 12), ("action", 3))),
        "pdf_content_preserved": all("".join(b["payload"]["text"].split()) in "".join(pdf_text.split()) for b in blocks),
        "settings_unchanged": ops.load_compile_settings() == fixture["compile_settings"],
        "plan_unchanged": unit["plan"] == fixture["before_plan"],
        "static_ok": ops.check_screenplay(fixture["screenplay_id"])["ok"],
    }


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = "scene-description-block-real/1"
    qa.FIXTURE_ORIGIN = "Controlled single-scene Operations fixture; only three short body blocks are real model output."
    return qa.main(validate=validate, required_tools=["read_script_unit_context", "submit_script_unit"],
                   target_factory=lambda f: {"kind": "unit", "ids": [f["unit_id"]], "screenplay_id": f["screenplay_id"]},
                   prompt_factory=lambda f: TASK + "\nunit_id=" + f["unit_id"])


if __name__ == "__main__":
    raise SystemExit(main())
