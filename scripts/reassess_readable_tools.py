"""Read-only reevaluation of existing real evidence; never starts a model task.

Preserves the original report and all raw output. A rejected tool attempt and a
successful same-task parameter correction are distinguished using task audit.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from accept_readable_tools import validate

from narrative.operations import Operations


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    folder = args.input.resolve()
    original_path = folder / "report.json"
    original_hash = digest(original_path)
    original = json.loads(original_path.read_text(encoding="utf-8"))
    audit_path = Path(original["audit_directory"]) / "task.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    report = copy.deepcopy(original)
    ops = Operations.open(folder / "剧本闭环验收.nproj")
    database = ops._store.path
    database_before = digest(database)
    fixture = original["fixture"]
    calls = json.loads((folder / "tool-calls.json").read_text(encoding="utf-8"))
    checks = validate(ops, fixture, ops.read_screenplay(fixture["screenplay_id"]), calls, report)
    product_code = {name: value for name, value in original["implementation_sha256"].items() if name.startswith("src")}
    checks.update(task_completed=audit["status"] == "completed", no_errors=not original["errors"],
        extraction_unchanged=ops.graph_snapshot() == json.loads((folder / "fixture-source.json").read_text(encoding="utf-8")),
        adaptation_unchanged=ops.adaptation_snapshot() == json.loads((folder / "fixture-adaptation.json").read_text(encoding="utf-8")),
        all_required_tools_called=all(original["required_tool_presence"].values()),
        usage_within_original_budget=original["actual_total_tokens"] <= original["external_usage_budget_tokens"],
        product_code_unchanged=all(digest(Path(name)) == value for name, value in product_code.items()),
        evidence_database_unchanged=digest(database) == database_before,
        original_report_unchanged=digest(original_path) == original_hash)
    report.update(status="passed" if all(checks.values()) else "not_passed", checks=checks,
        reevaluation={"reason": "Collector counted rejected format attempt as successful submission; use immutable task steps to count executed success while retaining raw attempts.",
            "original_status": original["status"], "original_report_sha256": original_hash,
            "audit_sha256": digest(audit_path), "new_model_requests": 0, "database_writes": False,
            "validator_sha256": digest(Path(__file__).with_name("accept_readable_tools.py")),
            "reevaluator_sha256": digest(Path(__file__))})
    output = folder / "reassessment.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"report": str(output), "status": report["status"], "checks": checks}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
