"""Deterministic source-location/automatic-submit benchmark; no model service calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
from statistics import median
from time import perf_counter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ["NARRATIVE_PROVIDER_DB"] = str(root / "providers.sqlite3")
    from narrative.operations import Operations
    from narrative.property_core import _evidence_error, validate_records
    from narrative.source_location import locate_text

    lines = [f"第{i:06d}段：人物在房间查看纸页，并记录本次内容。\n" for i in range(40000)]
    text = "".join(lines)
    quotes = [lines[i].strip() for i in range(0, 40000, 40)]
    def measure(function, count=5):
        values = []
        for _ in range(count):
            start = perf_counter()
            result = function()
            values.append((perf_counter() - start) * 1000)
        return {"median_ms": round(median(values), 3), "samples_ms": [round(value, 3) for value in values]}, result
    measurements = {}
    for name, batch in (("unique_1000", quotes), ("same_quote_1000", [quotes[-1]] * 1000),
                        ("missing_1000", [f"不存在{i:05d}的原文句子" for i in range(1000)])):
        metrics, results = measure(lambda batch=batch: locate_text(text, batch))
        assert len(results) == 1000
        metrics["quotes"] = len(batch)
        measurements[name] = metrics
    evidence = {"source_id": "S", "quote": quotes[-1]}
    records = [{"kind": "property_definition", "id": f"定义{i}", "value_type": "string", "cardinality": "one", "evidence": evidence} for i in range(1000)]
    sources = {"S": {"id": "S", "text": text}}
    uncached, errors = measure(lambda: [_evidence_error(record, sources) for record in records])
    assert not any(errors)
    cached, errors = measure(lambda: validate_records(records, {"sources": list(sources.values())}, []))
    assert not errors
    measurements["property_evidence_1000_uncached_reference"] = uncached
    measurements["property_validation_1000_cached_complete"] = cached
    ops = Operations.create_graph_project(root / "automatic-submit.nproj")
    sid = ops.import_source("大原文定位基准", text)
    selected = quotes[::25]
    nodes = [{"type": "person", "name": "人物", "quotes": [selected[0]]},
             {"type": "scene", "name": "查阅记录", "quotes": [selected[0]]}]
    nodes += [{"type": "event", "name": f"记录{i}", "scene": "查阅记录", "participants": ["人物"], "quotes": [quote]} for i, quote in enumerate(selected)]
    properties = [{"node": "人物", "property": "当前记录", "value": f"第{i}次", "value_type": "string", "at_event": f"记录{i}", "quotes": [quote]} for i, quote in enumerate(selected)]
    start = perf_counter()
    result = ops.submit_extraction(sid, {"nodes": nodes, "properties": properties})
    duration = (perf_counter() - start) * 1000
    assert result["status"] == "accepted", result
    measurements["automatic_submit"] = {"elapsed_ms": round(duration, 3), "nodes": len(nodes), "properties": len(properties), "result": result,
        "explicit_locate_calls": 0, "explicit_preview_calls": 0}
    report = {"kind": "deterministic-performance-benchmark", "external_model_requests": 0,
        "python": platform.python_version(), "source_unicode_characters": len(text),
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(), "measurements": measurements,
        "implementation_sha256": {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in (
            "src/narrative/source_location.py", "src/narrative/property_core.py", "src/narrative/extraction.py", "src/narrative/extraction_draft.py")}}
    (root / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"source_characters": len(text), "measurements": measurements}, ensure_ascii=False))


if __name__ == "__main__":
    main()
