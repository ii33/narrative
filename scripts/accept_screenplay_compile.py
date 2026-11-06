"""One bounded real body generation; deterministic compilation is host-owned."""
from __future__ import annotations

import html
import json
from pathlib import Path

import accept_screenplay as qa

BASE_SEED = qa.seed
TASK = """只为指定的一场写一小段正文，不改场表或其他数据。先read_script_unit_context，再submit_script_unit一次提交2至4块：至少一个具体action、一个绑定真实人物的dialogue；对白payload.parenthetical写简短可演提示。对白或动作自然包含字面短语“编号 <A&B>”，用于程序验证格式转义。保持钥匙交接剧情，中文简短即可。使用真实修订和指纹。不要生成HTML、Fountain或任何排版标记，程序将在提交后编译。完成后简述。"""


def seed(ops):
    fixture = BASE_SEED(ops)
    context = ops.read_scene_table_context(fixture["segment_id"])
    table = ops.submit_scene_table(
        fixture["segment_id"], [{"plan": {"title": "钥匙交接", "summary": "周川把铜钥匙交给林岚。", "location_text": "旧车站门口", "time_label": "夜"}}],
        context["expected_outline_revision"], context["span_fingerprint"], context["context_fingerprint"], reason="编译验收单场fixture")
    fixture["unit_id"] = table["units"][0]["id"]
    fixture["before_plan"] = table["units"][0]["plan"]
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    unit = ops.read_script_segment(fixture["segment_id"])["units"][0]
    blocks = unit["blocks"]
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    from narrative.tool_api import TOOLS
    attempts = [s for s in audit["steps"] if s["tool"] in TOOLS and TOOLS[s["tool"]].writes]
    writes = [s for s in attempts if s.get("ok")]
    before = ops.read_screenplay(fixture["screenplay_id"])
    compiled = {fmt: ops.compile_screenplay(fixture["screenplay_id"], format=fmt) for fmt in ("html", "text", "fountain")}
    directory = Path(report["audit_directory"]).resolve()
    # Keep standalone exports beside the task evidence, never in source Library.
    binary = {fmt: ops.compile_screenplay(fixture["screenplay_id"], format=fmt, path=str(directory / ("compiled-scene." + fmt))) for fmt in ("pdf", "docx")}
    from docx import Document
    from pypdf import PdfReader
    docx_text = "\n".join(p.text for p in Document(directory / "compiled-scene.docx").paragraphs)
    pdf = PdfReader(directory / "compiled-scene.pdf")
    pdf_text = "\n".join(page.extract_text() for page in pdf.pages)
    pdf_pages = len(pdf.pages)
    report["compile_evidence"] = {"outputs": compiled, "binary_outputs": binary, "docx_text": docx_text, "pdf_text": pdf_text, "pdf_pages": pdf_pages, "body": blocks, "attempted_writes": attempts, "scope": "Only this small generated scene and deterministic content/layout/escaping; not full screenplay quality."}
    # API normalization is finalized once the compiler contract is stable.
    output = {fmt: result.get("content", result.get("text", result.get("html", ""))) for fmt, result in compiled.items()}
    payload_texts = [b["payload"]["text"] for b in blocks if b["payload"].get("text")]
    parentheticals = [b["payload"].get("parenthetical") for b in blocks if b["type"] == "dialogue" and b["payload"].get("parenthetical")]
    return {
        "one_target_submit": len(writes) == 1 and writes[0]["tool"] == "submit_script_unit" and writes[0]["args"]["unit_id"] == fixture["unit_id"],
        "action_dialogue_parenthetical": {"action", "dialogue"}.issubset({b["type"] for b in blocks}) and bool(parentheticals),
        "escape_probe_in_actual_model_body": any("<A&B>" in text for text in payload_texts),
        "plain_content_preserved": all(text in output["text"] and text in output["fountain"] for text in payload_texts + parentheticals),
        "html_content_escaped": all(html.escape(text) in output["html"] for text in payload_texts + parentheticals) and "<A&B>" not in output["html"],
        "html_structured": "<" in output["html"] and "dialogue" in output["html"],
        "docx_content_preserved": all(text in docx_text for text in payload_texts + parentheticals),
        "pdf_content_preserved": all("".join(text.split()) in "".join(pdf_text.split()) for text in payload_texts + parentheticals),
        "compiler_readonly": before == ops.read_screenplay(fixture["screenplay_id"]),
        "plan_unchanged": unit["plan"] == fixture["before_plan"],
        "static_ok": ops.check_screenplay(fixture["screenplay_id"])["ok"],
    }


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = "screenplay-compile-formatting/1"
    qa.FIXTURE_ORIGIN = "Controlled Operations source/adaptation/script plus one scene plan; only body is real model output."
    return qa.main(validate=validate, required_tools=["read_script_unit_context", "submit_script_unit"],
                   target_factory=lambda f: {"kind": "unit", "ids": [f["unit_id"]], "screenplay_id": f["screenplay_id"]},
                   prompt_factory=lambda f: TASK + "\nunit_id=" + f["unit_id"])


if __name__ == "__main__":
    raise SystemExit(main())
