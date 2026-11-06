"""Read-only real-source timing; temporary business DB is removed, text is never exported."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import tempfile
from collections import Counter
from pathlib import Path
from statistics import median
from time import perf_counter, process_time

SCREENSHOT_QUOTES = ["两人虽然离开大别山", "方丈", "独生爱女年方及笄", "虽有一面之交"]
PATTERN = r"^第【[0-9]+】段：[^\r\n]+"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    from narrative.operations import Operations
    from narrative.source_documents import read_source_file
    from narrative.source_location import locate_text

    source = args.source.resolve()
    before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    def measure(function, repeat=7):
        samples = []
        for _ in range(repeat):
            wall, cpu = perf_counter(), process_time()
            result = function()
            samples.append({"wall_ms": (perf_counter() - wall) * 1000, "cpu_ms": (process_time() - cpu) * 1000})
        wall_median = median(sample["wall_ms"] for sample in samples)
        metrics = {"median_wall_ms": round(wall_median, 3),
                   "median_cpu_ms": round(median(sample["cpu_ms"] for sample in samples), 3),
                   "samples": [{key: round(value, 3) for key, value in sample.items()} for sample in samples]}
        # Windows process CPU ticks are coarse for sub-millisecond calls. Probe
        # read-only operations in a batch rather than interpreting a zero tick.
        if repeat > 1:
            iterations = max(1, min(5000, int(250 / max(wall_median, 0.001))))
            wall, cpu = perf_counter(), process_time()
            for _ in range(iterations):
                function()
            metrics["cpu_probe"] = {"iterations": iterations, "total_wall_ms": round((perf_counter() - wall) * 1000, 3),
                "total_cpu_ms": round((process_time() - cpu) * 1000, 3)}
            metrics["cpu_probe"]["mean_cpu_per_call_ms"] = round(metrics["cpu_probe"]["total_cpu_ms"] / iterations, 4)
        else:
            metrics["cpu_note"] = "One atomic write measured once; CPU may be below the Windows scheduling tick, use elapsed wall time for this sample."
        return result, metrics
    text, read_metrics = measure(lambda: read_source_file(source))
    start, end = 82, 48153
    selected = text[start:end]
    assert len(selected) == 48071
    # Sampling is mechanical source substrings, not hand-authored extraction facts.
    offsets = [start + i * (len(selected) - 48) // 999 for i in range(1000)]
    samples = [text[pos:pos + 48] for pos in offsets]
    with tempfile.TemporaryDirectory(prefix="narrative-source-coordinate-") as temporary:
        os.environ["NARRATIVE_PROVIDER_DB"] = str(Path(temporary) / "providers.sqlite3")
        ops = Operations.create_graph_project(Path(temporary) / "benchmark.nproj")
        sid, import_metrics = measure(lambda: ops.import_source_file(source), repeat=1)
        stored = ops.source(sid)["text"]
        assert stored == text
        preview, pattern_metrics = measure(lambda: ops.preview_pattern_slices(sid, PATTERN, 0, 2))
        created, slice_metrics = measure(lambda: ops.create_pattern_slices(sid, PATTERN, 0, 2), repeat=1)
        assert len(created["slices"]) == 2
        slices = created["slices"]
        assert slices[0]["start"] == start and slices[-1]["end"] == end
        screenshot, screenshot_metrics = measure(lambda: ops.locate(sid, SCREENSHOT_QUOTES))
        sampled, sampled_metrics = measure(lambda: ops.locate(sid, samples))
        local, local_metrics = measure(lambda: locate_text(selected, samples))
        selected_screenshot, selected_screenshot_metrics = measure(lambda: locate_text(selected, SCREENSHOT_QUOTES))
        absent_quotes = ["不存在的校验引文__" + str(i) for i in range(1000)]
        absent, absent_metrics = measure(lambda: ops.locate(sid, absent_quotes))
        repeated, repeated_metrics = measure(lambda: ops.locate(sid, [SCREENSHOT_QUOTES[1]] * 1000))
        assert all(row["matches"] == repeated[0]["matches"] for row in repeated)
        for offset, quote, full, relative in zip(offsets, samples, sampled, local, strict=True):
            assert {"start": offset, "end": offset + len(quote)} in full["matches"]
            assert {"start": offset - start, "end": offset - start + len(quote)} in relative["matches"]
        # Do not export the source, samples, source-bearing DB, or chat attachments.
        sample_results = [{"index": index, "quote_sha256": digest(quote), "characters": len(quote),
            "sample_global_start": offset, "full_source_result": full,
            "selected_range_result": relative} for index, (offset, quote, full, relative) in enumerate(zip(offsets, samples, sampled, local, strict=True))]
        screenshot_results = [{"quote": quote, "input_kind": "verbatim_screenshot_transcription_no_correction", "quote_sha256": digest(quote),
            "full_source_result": full, "selected_range_result": relative} for quote, full, relative in zip(SCREENSHOT_QUOTES, screenshot, selected_screenshot, strict=True)]
        with source.open("r", encoding="utf-8-sig", newline=None) as file:
            normalized = file.read()
        import re
        normalized_heading_starts = [match.start() for match in re.finditer(PATTERN, normalized, re.MULTILINE)][:3]
        report = {"kind": "deterministic-real-source-coordinate-benchmark", "external_model_requests": 0,
            "python": platform.python_version(), "source": str(source), "file_sha256": before_hash,
            "source_characters": len(text), "source_utf8_sha256": digest(text),
            "selected_range": {"start": start, "end": end, "characters": len(selected), "sha256": digest(selected)},
            "newline": {"raw_crcrlf": text.count("\r\r\n"), "raw_crlf": text.count("\r\n"), "raw_lf": text.count("\n"),
                        "universal_newline_characters": len(normalized), "normalization_character_loss": len(text) - len(normalized),
                        "raw_first_three_heading_starts": [row["start"] for row in slices] + [end],
                        "universal_newline_first_three_heading_starts": normalized_heading_starts,
                        "coordinate_policy": "Application read_source_file preserves raw Unicode CR/LF; do not use universal-newline offsets"},
            "slices": slices, "matched_headings": preview["match_count"],
            "metrics": {"read_source_file": read_metrics, "import_source_file": import_metrics, "pattern_preview": pattern_metrics,
                "create_first_two_slices": slice_metrics, "screenshot_quotes_ops": screenshot_metrics,
                "sample_1000_ops_full_source": sampled_metrics, "sample_1000_kernel_selected_range": local_metrics,
                "screenshot_quotes_kernel_selected_range": selected_screenshot_metrics,
                "missing_1000_ops_full_source": absent_metrics, "repeated_1000_ops_full_source": repeated_metrics},
            "diagnostic_counts": {"screenshot_full": dict(Counter(row["diagnostic"] for row in screenshot)),
                "screenshot_selected": dict(Counter(row["diagnostic"] for row in selected_screenshot)),
                "sample_full": dict(Counter(row["diagnostic"] for row in sampled)),
                "sample_selected": dict(Counter(row["diagnostic"] for row in local)),
                "missing": dict(Counter(row["diagnostic"] for row in absent))},
            "screenshot_quotes": screenshot_results,
            "sample_selection": "1000 deterministic 48-Unicode-character windows evenly spaced only within first two chapters; no business extraction facts",
            "temporary_database_retained": False, "full_source_or_sample_text_exported": False,
            "implementation_sha256": {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in (
                "src/narrative/source_documents.py", "src/narrative/source_location.py", "src/narrative/pattern_slices.py")}}
        (output / "sample-coordinate-results.json").write_text(json.dumps(sample_results, ensure_ascii=False, indent=2), encoding="utf-8")
    report["source_file_unchanged"] = hashlib.sha256(source.read_bytes()).hexdigest() == before_hash
    assert report["source_file_unchanged"]
    (output / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"range": report["selected_range"], "metrics": report["metrics"], "diagnostic_counts": report["diagnostic_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
