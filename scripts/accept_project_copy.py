"""One real write in a copied project, proving the source stays independent."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

from narrative.operations import Operations
from narrative.project_package import layout

_BASE_SEED = qa.seed
TASK = """这是工程创建副本后的独立写入验收。请先read_script_unit_context读取指定unit，按plan和本地有效范围，提交恰好一个短中文action正文块。新块ID让程序分配；使用刚读修订及短依据编号，不计算hash，不传task_id/manifest_ref。随后再读本场核对已保存。只允许submit_script_unit写指定场次，不改原文、改编或其他对象，不重启任务、不重试服务。"""


def seed(ops):
    fixture = _BASE_SEED(ops)
    context = ops.read_scene_table_context(fixture["segment_id"])
    table = ops.submit_scene_table(fixture["segment_id"], [{"plan": {"summary": "林岚握着铜钥匙，停在东仓门口听里面脚步。"}}],
        context["expected_outline_revision"], context["span_fingerprint"], context["context_fingerprint"], reason="复制验收确定性场表")
    fixture["unit_id"] = table["units"][0]["id"]
    fixture["original_entry"] = str(layout(ops.project_info().path).entry)
    fixture["original_project_id"] = ops.graph_project_info()["project_id"]
    fixture["original_screenplay"] = ops.read_screenplay(fixture["screenplay_id"])
    fixture["original_graph"] = ops.graph_snapshot()
    return fixture


def resolve_ops(ops, fixture):
    destination = Path(fixture["original_entry"]).with_name("工程副本.nproj")
    duplicate = Operations.duplicate_graph_project(fixture["original_entry"], destination)
    fixture["duplicate_entry"] = str(destination)
    fixture["duplicate_project_id"] = duplicate.graph_project_info()["project_id"]
    return duplicate


def validate(ops, fixture, screenplay, calls, report):
    original = Operations.open(fixture["original_entry"])
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    unit = screenplay["segments"][0]["units"][0]
    succeeded = [step for step in audit["steps"] if step["tool"] == "submit_script_unit" and step.get("ok")]
    report["copy_evidence"] = {"source_entry": fixture["original_entry"], "duplicate_entry": fixture["duplicate_entry"],
        "source_unit": original.read_script_unit_context(fixture["unit_id"])["unit"], "duplicate_unit": unit,
        "successful_tool_steps": succeeded}
    return {"independent_project_identity": fixture["original_project_id"] != fixture["duplicate_project_id"],
        "opened_duplicate": layout(ops.project_info().path).entry == Path(fixture["duplicate_entry"]),
        "one_actual_body_commit": len(succeeded) == 1 and succeeded[0]["args"]["unit_id"] == fixture["unit_id"],
        "one_real_action_body": len(unit["blocks"]) == 1 and unit["blocks"][0]["type"] == "action",
        "copied_stable_unit_identity": unit["id"] == fixture["unit_id"],
        "source_screenplay_unchanged": original.read_screenplay(fixture["screenplay_id"]) == fixture["original_screenplay"],
        "source_graph_unchanged": original.graph_snapshot() == fixture["original_graph"],
        "duplicate_static_ok": ops.check_screenplay(fixture["screenplay_id"])["ok"]}


def main():
    qa.seed = seed
    qa.TASK = TASK
    qa.PROMPT_VERSION = "project-copy-independent-write/1"
    qa.FIXTURE_ORIGIN = "Source/extraction/adaptation/scene prepared via Operations; full independent copy via Operations; only copied unit action body is model-generated"
    return qa.main(validate=validate, required_tools=["read_script_unit_context", "submit_script_unit"], ops_resolver=resolve_ops,
        target_factory=lambda f: {"kind": "unit", "ids": [f["unit_id"]], "screenplay_id": f["screenplay_id"]})


if __name__ == "__main__":
    raise SystemExit(main())
