"""One real, full-size fine extraction of two chapters, with bounded QA only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import accept_screenplay as qa
from qa_live_observer import LiveObserver, observe_real_calls

SOURCE_PATH = Path("C:/a/tmp/江湖夜雨十年灯.txt")
TASK = """请在当前空工程中处理《江湖夜雨十年灯》前两章的完整原文，按工程fine精细粒度提取，不缩短章节、不只做开头示例，不处理第三章及之后。
请自主读取标题小样，创建前两章的章节切片；有规律标题用pattern工具一次建立所需两章，不必手算边界。逐章完整读取并提取，允许根据内容分批提交，但每批使用对应的实际原文范围和逐字证据。覆盖有叙事作用的主要及次要/无名人物、具体地点、关系、心理认知、世界事实、事件及失效，不只列名词或梗概。核对人物身份和代词、叙事时空、关系方向、物品持有者及换手区间，事件归属场景。默认不提取动作，不为章节尚未结束的较大篇章创建临时主要故事。
正常提取流程自主完成；报告各章实际完成范围、入库结果与存疑。若未完成不能声称两章全部完成。只处理指定两章，不修改其他业务对象，不另开任务或重试服务。"""
OBSERVER = LiveObserver()


def load_input():
    from narrative.source_documents import read_source_file
    text = read_source_file(SOURCE_PATH)
    headings = list(re.finditer(r"(?m)^第【\d+】段：第\d+章[^\n]*", text))
    assert len(text) == 408795 and len(headings) == 20
    assert [match.start() for match in headings[:3]] == [82, 24171, 48153]
    assert qa.digest(text[82:48153].encode()) == "b96f9fa7b700cf8ac235274e70f30d21c1edc6b13fb8add65d751a821eef9c56"
    return text, headings


def seed(ops):
    text, headings = load_input()
    ops.save_project_settings(ops.load_project_settings() | {"extraction_granularity": "fine"})
    source = ops.import_source(SOURCE_PATH.stem, text)
    script = ops.create_screenplay("验收采集空容器", reason="共享证据采集器的确定性空容器")["screenplay"]
    return {"source_id": source, "screenplay_id": script["id"], "source_chars": len(text),
        "source_file_sha256": qa.digest(SOURCE_PATH.read_bytes()), "source_text_sha256": qa.digest(text.encode()),
        "target_chars": 48071, "target_text_sha256": qa.digest(text[82:48153].encode()),
        "chapters": [{"index": index + 1, "title": headings[index].group().strip(), "start": headings[index].start(), "end": headings[index + 1].start()} for index in range(2)]}


def validate(ops, fixture, screenplay, calls, report):
    text, _ = load_input()
    graph, records = ops.graph_snapshot(), ops.property_records()
    audit = json.loads((Path(report["audit_directory"]) / "task.json").read_text(encoding="utf-8"))
    steps = audit.get("steps", [])
    submissions = [step for step in steps if step["tool"] == "submit_extraction"]
    accepted = [step for step in submissions if step.get("ok") and step.get("result", {}).get("status") == "accepted"]
    spans = [span for node in graph["nodes"] for span in node.get("evidence_spans", [])]
    events = [node for node in graph["nodes"] if node["node_type"] == "event"]
    slices = ops.source_slices(fixture["source_id"])
    if isinstance(slices, dict):
        slices = slices.get("slices", [])
    coverage = []
    for chapter in fixture["chapters"]:
        members = [node for node in events if chapter["start"] <= (node.get("disclosure") or {}).get("start", -1) < chapter["end"]]
        evidence = [span for span in spans if chapter["start"] <= span["start"] < chapter["end"]]
        bins = [sum(chapter["start"] + (chapter["end"] - chapter["start"]) * index / 8 <= node["disclosure"]["start"] < chapter["start"] + (chapter["end"] - chapter["start"]) * (index + 1) / 8 for node in members) for index in range(8)]
        coverage.append(chapter | {"event_count": len(members), "first_event_start": min((node["disclosure"]["start"] for node in members), default=None),
            "last_event_start": max((node["disclosure"]["start"] for node in members), default=None),
            "last_evidence_end": max((span["end"] for span in evidence), default=None), "event_counts_by_eighth": bins})
    declared = sorted((max(82, step["args"].get("start", 0)), min(48153, step["args"].get("end") or len(text))) for step in accepted)
    cursor = 82
    for start, end in declared:
        if start <= cursor:
            cursor = max(cursor, end)
    check = ops.check_project()
    report["large_sample_evidence"] = {"chapter_coverage": coverage, "created_slices": slices,
        "accepted_batch_count": len(accepted), "submission_attempt_count": len(submissions),
        "accepted_declared_ranges": declared, "locate_calls": [step for step in steps if step["tool"] == "locate_source"],
        "preview_calls": [step for step in steps if step["tool"] == "preview_extraction"], "source_graph": graph,
        "property_records": records, "static_check": check, "live_observation": OBSERVER.snapshot(),
        "coverage_limit": "Submitted range declarations and reading the whole book are not proof of exhaustive extraction. Actual evidence coordinates and event distribution are reported; complete semantic coverage requires reading the resulting facts against both chapters. No event-count quota is imposed."}
    report["semantic_review_status"] = "pending_independent_source_comparison"
    report["validation_scope"] = "Automated structural/observed-range checks only; status passed would not alone certify exhaustive fine semantic extraction."
    return {"both_chapters_created_by_model": all(any(row["start"] == chapter["start"] and row["end"] == chapter["end"] for row in slices) for chapter in fixture["chapters"]),
        "model_submitted_accepted_batches": bool(accepted), "actual_events_in_both_chapters": all(row["event_count"] > 0 for row in coverage),
        "accepted_declared_ranges_cover_two_chapters": cursor >= 48153,
        "all_node_quote_coordinates_exact": bool(spans) and all(span.get("source_id") == fixture["source_id"] and text[span["start"]:span["end"]] == span["quote"] for span in spans),
        "no_evidence_from_later_chapters": all(82 <= span["start"] < span["end"] <= 48153 for span in spans),
        "no_default_actions": not any(node["node_type"] == "action" for node in graph["nodes"]),
        "static_check_no_errors": check["counts"].get("error", 0) == 0,
        "whole_original_source_unchanged": qa.digest(ops.source(fixture["source_id"])["text"].encode()) == fixture["source_text_sha256"]}


def main():
    text, _ = load_input()
    qa.seed, qa.TEXT, qa.TASK = seed, text, TASK
    qa.PROMPT_VERSION = "real-two-chapters-fine-scale/1"
    qa.FIXTURE_ORIGIN = "Original complete UTF8/CRLF book imported through Operations; no host-authored slices or extraction. Real model creates and extracts the full first two chapters."
    with observe_real_calls(OBSERVER):
        return qa.main(validate=validate, required_tools=["submit_extraction"],
            prompt_factory=lambda fixture: TASK + "\nsource_id=" + fixture["source_id"], use_writing_target=False,
            allow_extraction_change=True, on_started=OBSERVER.started, on_progress=OBSERVER.progress)


if __name__ == "__main__":
    raise SystemExit(main())
