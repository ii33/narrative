"""Evidence collection separates failed attempts from real successful writes."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from test_screenplay_writing import project as project
from test_screenplay_writing import table

from narrative import tool_api
from narrative.project_package import layout
from narrative.tool_execution_context import bind_tool_execution_metadata


def evidence(project, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    validator = importlib.import_module("accept_readable_tools")
    ops, screenplay_id, segment_id = project
    unit = table(ops, segment_id, [{"plan": {"summary": "甲等待乙。"}}])["units"][0]
    ops.material_add_card("P1", "合成资料")
    ops.save_asset({"category": "material", "title": "合成材质"})
    entry = layout(ops.project_info().path).entry
    context = tool_api.call("read_script_unit_context", {"unit_id": unit["id"]}, project=entry)
    args = {key: context[key] for key in ("expected_plan_revision", "expected_body_revision", "span_fingerprint", "context_fingerprint", "plan_fingerprint")}
    args.update(unit_id=unit["id"], blocks=[{"type": "action", "payload": {"text": "甲等待。"}}], reason="合成工具证据")
    malformed = args | {"blocks": [{"type": "action", "text": "甲等待。"}]}
    with pytest.raises(tool_api.ToolError):
        tool_api.call("submit_script_unit", malformed, project=entry)
    audit = tmp_path / "synthetic-task"
    audit.mkdir()
    with bind_tool_execution_metadata({"task_id": audit.name, "manifest_ref": str(audit / "writing-task.json")}):
        result = tool_api.call("submit_script_unit", args, project=entry)
    calls = [{"name": "submit_script_unit", "arguments": json.dumps(value)} for value in (malformed, args)]
    steps = [{"tool": "submit_script_unit", "args": malformed, "ok": False, "error": "payload required"},
             {"tool": "submit_script_unit", "args": args, "ok": True, "result": result}]
    fixture = {"person_id": "P1", "unit_id": unit["id"], "screenplay_id": screenplay_id}
    return validator, ops, fixture, calls, steps, audit


def test_same_task_rejected_format_attempt_does_not_count_as_second_commit(project, tmp_path, monkeypatch):
    validator, ops, fixture, calls, steps, audit = evidence(project, tmp_path, monkeypatch)
    (audit / "task.json").write_text(json.dumps({"steps": steps}), encoding="utf-8")
    report = {"audit_directory": str(audit)}
    checks = validator.validate(ops, fixture, ops.read_screenplay(fixture["screenplay_id"]), calls, report)
    assert all(checks.values()), checks
    assert len(report["readable_id_evidence"]["raw_submit_attempts"]) == 2
    assert len(report["readable_id_evidence"]["successful_submit_arguments"]) == 1
    assert len(report["readable_id_evidence"]["rejected_submit_attempts"]) == 1


def test_final_body_alone_cannot_replace_successful_tool_evidence(project, tmp_path, monkeypatch):
    validator, ops, fixture, calls, steps, audit = evidence(project, tmp_path, monkeypatch)
    (audit / "task.json").write_text(json.dumps({"steps": steps[:1]}), encoding="utf-8")
    checks = validator.validate(ops, fixture, ops.read_screenplay(fixture["screenplay_id"]), calls, {"audit_directory": str(audit)})
    assert not checks["exact_target_submit"]
    assert not checks["submitted_body_matches_saved"]
    assert not checks["provenance_internal"]
