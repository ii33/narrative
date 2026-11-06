"""Read-only fixed-unit entry acceptance; GUI branches are tested separately."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_screenplay_compile import seed

TASK = "只读取当前目标场次上下文，回复场次名称和是否已有正文。不要写入、生成、修改或额外检查。"


def validate(ops, fixture, screenplay, calls, report):
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    from narrative.tool_api import TOOLS
    writes = [s for s in audit["steps"] if s["tool"] in TOOLS and TOOLS[s["tool"]].writes]
    reads = [s for s in audit["steps"] if s["tool"] == "read_script_unit_context" and s.get("ok")]
    unit = ops.read_script_segment(fixture["segment_id"])["units"][0]
    report["unit_entry_read_evidence"] = {"reads": reads, "write_attempts": writes, "unit_title": unit["plan"]["title"], "body_presence": unit["body_presence"], "scope": "Existing fixed-unit conversation read only. Empty/present GUI branches and frozen selection are independently verified by QML tests."}
    return {"zero_write_attempts": not writes,
            "only_target_context_read": bool(reads) and all(s["args"]["unit_id"] == fixture["unit_id"] for s in reads),
            "plan_unchanged": unit["plan"] == fixture["before_plan"],
            "body_remains_empty": not unit["blocks"]}


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = "unified-unit-entry-read/1"
    qa.FIXTURE_ORIGIN = "Consistent key handoff scene at night station, empty body; real model reads only."
    return qa.main(validate=validate, required_tools=["read_script_unit_context"],
                   target_factory=lambda f: {"kind": "unit", "ids": [f["unit_id"]], "screenplay_id": f["screenplay_id"]},
                   prompt_factory=lambda _: TASK)


if __name__ == "__main__":
    raise SystemExit(main())
