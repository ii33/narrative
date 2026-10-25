"""Summarize audited local GraphRAG usage and the repaired index acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

STAGES = {
    "compatibility_checks": [1, 11, 65, 66, 96, 97, 98, 99],
    "extract_graph": [*range(2, 11), *range(12, 18)],
    "summarize_descriptions": list(range(18, 56)),
    "extract_claims": [*range(56, 65), *range(67, 73), 100],
    "community_reports": list(range(73, 96)),
}


def summarize(root: Path) -> dict:
    reports = [
        json.loads((root / name).read_text(encoding="utf-8"))
        for name in (
            "narrative-graphrag-run.json",
            "narrative-graphrag-resume-1.json",
            "narrative-graphrag-resume-2.json",
            "narrative-graphrag-resume-3.json",
            "narrative-graphrag-resume-4.json",
        )
    ]
    if reports[-1]["real_acceptance"] != "complete" or reports[-1]["index_exit_code"] != 0:
        raise ValueError("Repaired official standard index did not finish")
    attempts = {item["attempt"]: item for report in reports for item in report["attempts"]}
    if sorted(attempts) != list(range(1, 101)):
        raise ValueError("Raw completion attempt journal has gaps")
    for item in attempts.values():
        raw = (root / "narrative-graphrag-raw" / item["raw_response_file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item["raw_response_sha256"]:
            raise ValueError("Raw model response hash changed")
    stage_stats = {}
    for stage, numbers in STAGES.items():
        rows = [attempts[number] for number in numbers]
        prompt = sum(row["usage"]["prompt_tokens"] for row in rows)
        completion = sum(row["usage"]["completion_tokens"] for row in rows)
        seconds = sum((datetime.fromisoformat(row["ended_utc"]) - datetime.fromisoformat(row["started_utc"])).total_seconds() for row in rows)
        reasoning_chars = sum(
            len(json.loads((root / "narrative-graphrag-raw" / row["raw_response_file"]).read_text(encoding="utf-8"))["choices"][0]["message"].get("reasoning") or "")
            for row in rows
        )
        stage_stats[stage] = {
            "requests": len(rows),
            "prompt_tokens": prompt,
            "completion_tokens_including_reasoning": completion,
            "total_tokens": prompt + completion,
            "request_elapsed_seconds": round(seconds, 1),
            "reasoning_characters_observed": reasoning_chars,
            "reasoning_tokens": None,
            "non_stop_responses": sum(row["finish_reason"] != "stop" for row in rows),
        }
    cache_counts = {}
    for stage in ("extract_graph", "summarize_descriptions", "extract_claims", "community_reporting"):
        entries = list((root / "cache" / stage).iterdir())
        reasons = [json.loads(path.read_text(encoding="utf-8"))["result"]["response"]["choices"][0]["finish_reason"] for path in entries]
        if any(reason != "stop" for reason in reasons):
            raise ValueError(f"Non-stop response remains in {stage} cache")
        cache_counts[stage] = len(entries)
    source_text = "".join(path.read_text(encoding="utf-8") for path in (root / "input").glob("*.txt"))
    han = sum(0x4E00 <= ord(char) <= 0x9FFF for char in source_text)
    ops = json.loads((root / "narrative-graphrag-operations-acceptance-repaired.json").read_text(encoding="utf-8"))
    vectors = json.loads((root / "narrative-graphrag-vector-acceptance-repaired.json").read_text(encoding="utf-8"))
    provenance = json.loads((root / "narrative-graphrag-vector-provenance-repaired.json").read_text(encoding="utf-8"))
    chat_input_tokens = sum(stage["prompt_tokens"] for stage in stage_stats.values())
    chat_output_tokens = sum(stage["completion_tokens_including_reasoning"] for stage in stage_stats.values())
    chat_total_tokens = chat_input_tokens + chat_output_tokens
    if chat_total_tokens != sum(report.get("accounted_tokens", report.get("new_tokens")) for report in reports):
        raise ValueError("Completion usage does not balance")
    usable_stage_tokens = chat_total_tokens - stage_stats["compatibility_checks"]["total_tokens"] - attempts[64]["usage"]["total_tokens"]
    result = {
        "schema": "microsoft-graphrag-final-acceptance/2",
        "official_version": "3.3.0",
        "method": "standard",
        "model": "qwen3.8:iq3-mtp3",
        "embedding_model": "bge-m3",
        "local_only": True,
        "completed_final_run": True,
        "final_index_exit_code": reports[-1]["index_exit_code"],
        "source_chapters": 3,
        "staged_characters": len(source_text),
        "staged_han_characters": han,
        "cache_entries_all_stop": cache_counts,
        "table_counts": ops["table_counts"],
        "vector_table_rows": vectors["vector_table_rows"],
        "operations_claims_source_mapped": ops["claims_source_mapped"],
        "operations_claims_exact_quote_eligible": ops["claims_exact_quote_eligible"],
        "operations_formal_candidates_created": ops["formal_candidates_created"],
        "operations_revision_unchanged": ops["revision_before"] == ops["revision_after"],
        "retrieval_entity_ranks_top5": [row["entity_rank_top5"] for row in vectors["queries"]],
        "retrieval_source_unit_top5_overlaps": [row["retrieved_top5_source_unit_overlap"] for row in provenance["queries"]],
        "relationships_with_text_unit_provenance": provenance["relations_with_indexed_text_unit"],
        "chat_requests_including_recovery": len(attempts),
        "chat_input_tokens_including_recovery": chat_input_tokens,
        "chat_output_tokens_including_reasoning": chat_output_tokens,
        "chat_total_tokens_including_recovery": chat_total_tokens,
        "stage_usage": stage_stats,
        "usable_stage_tokens_excluding_health_checks_and_truncated_attempt": usable_stage_tokens,
        "reported_index_embedding_tokens": 51387,
        "embedding_usage_note": "GraphRAG's local bge-m3 metrics from the first successful vector-build run; cached reruns are not added again. Three final retrieval queries are separate.",
        "actual_chat_input_plus_output_tokens_per_million_han_linear_ratio": round(chat_total_tokens / han * 1_000_000),
        "usable_stage_input_plus_output_tokens_per_million_han_linear_ratio": round(usable_stage_tokens / han * 1_000_000),
        "ratio_limit": "Three short chapters only; community count, entity density, chunk boundaries and retries make full-book usage nonlinear.",
        "gpu_memory_observed_sample_mib": 15774,
        "gpu_memory_sample_note": "Highest manual nvidia-smi sample during generation, not a continuously measured peak; CPU/RAM peak unavailable.",
        "reasoning_usage_note": "Ollama usage combines visible completion and hidden reasoning tokens; separate reasoning-token counts were not returned. Reasoning character counts are diagnostic only.",
    }
    destination = root / "narrative-graphrag-final-acceptance-v2.json"
    if destination.exists():
        raise FileExistsError(destination)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    result = summarize(parser.parse_args().root)
    print(json.dumps({key: value for key, value in result.items() if key != "stage_usage"}, ensure_ascii=False, indent=2))
