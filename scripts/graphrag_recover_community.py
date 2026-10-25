"""Keep completed graph caches while changing claims/reports to no-thinking output."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import yaml
from graphrag_bounded_run import preflight


def recover(root: Path) -> dict:
    preflight(root, allow_resume=True)
    previous = json.loads((root / "narrative-graphrag-resume-1.json").read_text(encoding="utf-8"))
    if previous.get("index_exit_code") != 1 or previous.get("cache_entries_after") != 15:
        raise ValueError("Unexpected incomplete predecessor")
    last = previous["attempts"][-1]
    if last.get("attempt") != 64 or last.get("finish_reason") != "length":
        raise ValueError("Expected recorded response-064 truncation")
    settings_path = root / "settings.yaml"
    snapshot = root / "narrative-graphrag-settings-before-resume-2.yaml"
    record_path = root / "narrative-graphrag-recovery-2.json"
    if snapshot.exists() or record_path.exists():
        raise FileExistsError("Recovery configuration already applied")
    original = settings_path.read_bytes()
    settings = yaml.safe_load(original)
    if settings["extract_claims"]["completion_model_id"] != "default_completion_model":
        raise ValueError("Claims model already changed")
    if settings["community_reports"]["completion_model_id"] != "default_completion_model":
        raise ValueError("Community model already changed")
    model = deepcopy(settings["completion_models"]["default_completion_model"])
    model["call_args"]["reasoning_effort"] = "none"
    model["call_args"]["max_tokens"] = 6000
    settings["completion_models"]["no_thinking_completion_model"] = model
    settings["extract_claims"]["completion_model_id"] = "no_thinking_completion_model"
    settings["community_reports"]["completion_model_id"] = "no_thinking_completion_model"
    updated = yaml.safe_dump(settings, allow_unicode=True, sort_keys=False).encode("utf-8")
    snapshot.write_bytes(original)
    settings_path.write_bytes(updated)
    record = {
        "schema": "microsoft-graphrag-local-recovery/1",
        "reason": "response-064 reached 5000 completion tokens, including reasoning; subsequent community requests were denied by the failure gate",
        "local_probe": "local-no-thinking-probe/report.json: 28 tokens, valid JSON, zero reasoning characters; GraphRAG/LiteLLM probe dropped reasoning_effort, so the localhost gate injects it for 6000-token stage calls",
        "settings_before_sha256": hashlib.sha256(original).hexdigest(),
        "settings_after_sha256": hashlib.sha256(updated).hexdigest(),
        "changed_stages": ["extract_claims", "community_reports"],
        "unchanged_graph_cache_entries": 15,
        "model": "qwen3.8:iq3-mtp3",
        "call_args": model["call_args"],
    }
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    print(json.dumps(recover(parser.parse_args().root), ensure_ascii=False, indent=2))
