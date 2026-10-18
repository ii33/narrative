import ast
import json
from pathlib import Path

import pytest

from narrative.operations import OperationError, Operations


def test_operations_support_full_workflow_without_ui(tmp_path):
    operations = Operations.open_project(tmp_path, "book")
    source_id = operations.import_bytes("章一", "甲说门锁着。\r\n门锁着。".encode())
    entity_id = operations.propose_entity("甲", "person", ["阿甲"])
    review = operations.review_candidate(entity_id)
    assert review.differences[0]["before"] is None
    operations.accept_candidate(entity_id, review.version)
    person = operations.entities()[0]
    evidence = operations.locate(source_id, "甲说门锁着。")[0]
    excerpt = operations.evidence_excerpt(evidence)
    assert excerpt.quote == "甲说门锁着。" and excerpt.after == "\r\n门锁着。"
    change_id = operations.propose_assertion(
        subject_id=person["id"],
        predicate="门状态",
        value="锁着",
        scope="claim",
        holder_id=person["id"],
        evidence=evidence,
    )
    review = operations.review_candidate(change_id)
    operations.accept_candidate(change_id, review.version)
    correction_id = operations.propose_correction(operations.assertions()[0].id)
    review = operations.review_candidate(correction_id)
    content = review.change.model_dump()
    content["operations"][0]["value"] = "不确定"
    operations.edit_candidate(correction_id, json.dumps(content), review.version)
    with pytest.raises(OperationError, match="候选已改变"):
        operations.accept_candidate(correction_id, review.version)
    operations.accept_candidate(correction_id, operations.review_candidate(correction_id).version)
    assert operations.revisions()[-1]["data"][0]["before"]["value"] == "锁着"
    assert operations.assertions()[0].scope == "claim"

    run_id = operations.create_run(source_id)
    pack = operations.export_run(run_id)
    chunk_id = pack["chunks"][0]["chunk_id"]
    assert operations.ingest_result(chunk_id, "{}") is None
    assert operations.chunks(run_id)[0]["status"] == "processed"
    assert operations.runs()[0]["status"] == "complete"
    download = operations.backup_download()
    assert download.filename.endswith(".sqlite3") and download.content
    operations.restore_upload(download.content, "reopened")
    reopened = Operations.open_project(tmp_path, "reopened")
    assert reopened.status() == operations.status()
    assert reopened.assertions()[0].value == "不确定"


def test_operations_rebase_and_reject_guard_reviewed_version(tmp_path):
    operations = Operations(tmp_path / "book.sqlite3")
    first = operations.propose_entity("甲", "person", [])
    second = operations.propose_entity("乙", "person", [])
    operations.accept_candidate(first, 1)
    stale = operations.review_candidate(second)
    with pytest.raises(OperationError, match="旧修订"):
        operations.accept_candidate(second, stale.version)
    operations.import_text("新章", "新资料")
    with pytest.raises(OperationError, match="再次改变"):
        operations.rebase_candidate(second, stale.version, stale.current_revision)
    latest = operations.review_candidate(second)
    operations.rebase_candidate(second, latest.version, latest.current_revision)
    with pytest.raises(OperationError, match="候选已改变"):
        operations.reject_candidate(second, latest.version)
    operations.reject_candidate(second, operations.review_candidate(second).version)
    assert len(operations.entities()) == 1
    assert not operations.candidates(status="pending")


def test_operations_own_validation_and_file_errors(tmp_path):
    with pytest.raises(OperationError, match="项目名"):
        Operations.open_project(tmp_path, "../outside")
    operations = Operations.open_project(tmp_path, "book")
    with pytest.raises(OperationError):
        operations.import_bytes("章", b"\xff")
    with pytest.raises(OperationError):
        operations.import_file(tmp_path / "missing.txt")
    with pytest.raises(OperationError):
        operations.restore_upload(b"not-a-database", "restored")
    assert not list((tmp_path / "backups").glob("upload-*"))
    assert not (tmp_path / "restored.sqlite3").exists()
    change_id = operations.propose_entity("甲", "person", [])
    review = operations.review_candidate(change_id)
    content = review.change.model_dump()
    content["id"] = "other-id"
    with pytest.raises(OperationError, match="不能改变"):
        operations.edit_candidate(change_id, json.dumps(content), review.version)


def test_entry_points_only_import_operations_and_do_not_reach_internals():
    package = Path(__file__).resolve().parents[1] / "src" / "narrative"
    for name in ("ui.py", "cli.py"):
        tree = ast.parse((package / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level or module.startswith("narrative."):
                    assert module in {"operations", "narrative.operations"}, (name, module)
            if isinstance(node, ast.Attribute):
                assert node.attr not in {
                    "store",
                    "_store",
                    "_service",
                    "connect",
                    "rows",
                    "objects",
                }, (name, node.attr)
    for path in package.glob("*.py"):
        if path.name == "ui.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("streamlit") for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {"streamlit", "narrative.ui", "ui"}
