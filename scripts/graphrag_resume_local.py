"""Continue the isolated three-chapter standard index with its existing cache.

Historic and current usage remain visible in the report. Local model calls
have no request-count or token ceiling; failed calls still stop the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from graphrag_bounded_run import BudgetGate, preflight, utc_now

HISTORIC_USED_TOKENS = 1_043_833


def checked_prior(root: Path, round_number: int = 1) -> tuple[int, int, dict, int]:
    if round_number not in (1, 2, 3, 4):
        raise ValueError("Only recorded continuation rounds 1 through 4 are supported")
    config, sources = preflight(root, allow_resume=True)
    old = json.loads((root / "narrative-graphrag-run.json").read_text(encoding="utf-8"))
    probe = json.loads((root.parent / "local-schema-probe-2" / "probe.json").read_text(encoding="utf-8"))
    if old.get("real_acceptance") != "incomplete" or old.get("requests") != 10:
        raise ValueError("Unexpected previous standard run")
    if old.get("model") != config["completion_model"] or old.get("method") != "standard":
        raise ValueError("Previous model/method differs")
    if probe.get("requests") != 1 or not probe.get("structured_response_valid"):
        raise ValueError("Synthetic probe accounting changed")
    if len(old.get("attempts", [])) != 10:
        raise ValueError("Previous request journal incomplete")
    if sum(item["usage"]["total_tokens"] for item in old["attempts"]) != old["accounted_tokens"]:
        raise ValueError("Previous response usage does not balance")
    raw_root = root / "narrative-graphrag-raw"
    for item in old["attempts"]:
        raw = raw_root / item["raw_response_file"]
        if hashlib.sha256(raw.read_bytes()).hexdigest() != item["raw_response_sha256"]:
            raise ValueError(f"Previous raw response changed: {raw.name}")
    cache_count = sum(1 for item in (root / "cache" / "extract_graph").iterdir() if item.is_file())
    expected_cache = 9 if round_number == 1 else 15
    if cache_count != expected_cache:
        raise ValueError(f"Expected {expected_cache} preserved graph extraction cache entries; found {cache_count}")
    if len(sources) != 3:
        raise ValueError("Expected exactly three staged sources")
    previous = HISTORIC_USED_TOKENS + probe["accounted_tokens"] + old["accounted_tokens"]
    offset = 10
    if round_number >= 2:
        first = json.loads((root / "narrative-graphrag-resume-1.json").read_text(encoding="utf-8"))
        if first.get("index_exit_code") != 1 or first.get("attempts", [])[-1].get("finish_reason") != "length":
            raise ValueError("Expected the recorded truncated first continuation")
        if len(first["attempts"]) != 54 or sum(a["usage"]["total_tokens"] for a in first["attempts"]) != first["new_tokens"]:
            raise ValueError("First continuation usage does not balance")
        for item in first["attempts"]:
            raw = raw_root / item["raw_response_file"]
            if hashlib.sha256(raw.read_bytes()).hexdigest() != item["raw_response_sha256"]:
                raise ValueError(f"First continuation response changed: {raw.name}")
        direct_probe = json.loads((root.parent / "local-no-thinking-probe" / "report.json").read_text(encoding="utf-8"))
        lite_probe = json.loads((root.parent / "local-schema-no-thinking-probe" / "probe.json").read_text(encoding="utf-8"))
        gate_probe = json.loads((root.parent / "local-gate-no-thinking-probe" / "probe.json").read_text(encoding="utf-8"))
        if direct_probe["usage"]["total_tokens"] != 28 or lite_probe["accounted_tokens"] != 313 or gate_probe["accounted_tokens"] != 200:
            raise ValueError("Recovery probes' usage changed")
        if not gate_probe["structured_response_valid"] or gate_probe["reasoning_chars"] != 0:
            raise ValueError("No-thinking gate probe did not validate")
        recovery = json.loads((root / "narrative-graphrag-recovery-2.json").read_text(encoding="utf-8"))
        if hashlib.sha256((root / "settings.yaml").read_bytes()).hexdigest() != recovery["settings_after_sha256"]:
            raise ValueError("Recovery settings changed")
        previous += first["new_tokens"] + 28 + 313 + 200
        offset = 64
        if previous != 1_201_424:
            raise ValueError("Prior usage accounting changed")
        if round_number >= 3:
            second = json.loads((root / "narrative-graphrag-resume-2.json").read_text(encoding="utf-8"))
            if second.get("index_exit_code") != 1 or second.get("gate_failed") or second.get("new_requests") != 31:
                raise ValueError("Expected the recorded LanceDB temp-path failure")
            if sum(a["usage"]["total_tokens"] for a in second["attempts"]) != second["new_tokens"]:
                raise ValueError("Second continuation usage does not balance")
            for item in second["attempts"]:
                raw = raw_root / item["raw_response_file"]
                if hashlib.sha256(raw.read_bytes()).hexdigest() != item["raw_response_sha256"]:
                    raise ValueError(f"Second continuation response changed: {raw.name}")
            if sum(1 for item in (root / "cache" / "community_reporting").iterdir() if item.is_file()) != 23:
                raise ValueError("Expected 23 preserved community report cache entries")
            previous += second["new_tokens"]
            offset = 95
            if previous != 1_326_619:
                raise ValueError("Second continuation usage accounting changed")
            if round_number == 4:
                third = json.loads((root / "narrative-graphrag-resume-3.json").read_text(encoding="utf-8"))
                if third.get("real_acceptance") != "complete" or third.get("new_requests") != 2:
                    raise ValueError("Expected the recorded complete but claim-truncated index")
                if sum(a["usage"]["total_tokens"] for a in third["attempts"]) != third["new_tokens"]:
                    raise ValueError("Third continuation usage does not balance")
                for item in third["attempts"]:
                    raw = raw_root / item["raw_response_file"]
                    if hashlib.sha256(raw.read_bytes()).hexdigest() != item["raw_response_sha256"]:
                        raise ValueError(f"Third continuation response changed: {raw.name}")
                repair = json.loads((root / "narrative-graphrag-claim-repair.json").read_text(encoding="utf-8"))
                archived = root / repair["archived_relative_path"]
                if hashlib.sha256(archived.read_bytes()).hexdigest() != repair["sha256"]:
                    raise ValueError("Archived truncated claim response changed")
                if sum(1 for item in (root / "cache" / "extract_claims").iterdir() if item.is_file()) != 14:
                    raise ValueError("Expected exactly one archived claim cache entry")
                previous += third["new_tokens"]
                offset = 97
                if previous != 1_326_705:
                    raise ValueError("Third continuation usage accounting changed")
    elif previous != 1_090_863:
        raise ValueError("Prior usage accounting changed")
    if (raw_root / f"response-{offset + 1:03}.json").exists():
        raise FileExistsError("Continuation response already exists")
    return previous, cache_count, config, offset


def run(root: Path, round_number: int = 1) -> dict:
    previous, cache_count, config, offset = checked_prior(root, round_number)
    report_path = root / f"narrative-graphrag-resume-{round_number}.json"
    log_path = root / f"narrative-graphrag-resume-{round_number}.log"
    if report_path.exists() or log_path.exists():
        raise FileExistsError("Continuation already recorded; inspect before running again")
    gate = BudgetGate(
        root,
        max_requests=None,
        max_total_tokens=None,
        initial_used_tokens=previous,
        attempt_offset=offset,
        no_thinking_max_tokens=6000 if round_number >= 2 else None,
    )
    server = gate.serve()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    env = os.environ.copy()
    env["GRAPHRAG_API_BASE"] = f"http://127.0.0.1:{server.server_port}/v1"
    env["GRAPHRAG_API_KEY"] = gate.local_token
    env["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    local_tmp = (root / "local-tmp").resolve()
    local_tmp.mkdir(parents=True, exist_ok=True)
    env["TMP"] = str(local_tmp)
    env["TEMP"] = str(local_tmp)
    env["TMPDIR"] = str(local_tmp)
    executable = Path(sys.executable).with_name("graphrag.exe")
    started = utc_now()
    process: subprocess.Popen[bytes] | None = None
    interrupted = False
    try:
        with log_path.open("xb") as log:
            process = subprocess.Popen(
                [str(executable), "index", "--root", str(root), "--method", "standard", "--cache"],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            last_notice = 0.0
            while process.poll() is None:
                now = time.monotonic()
                if now - last_notice >= 30:
                    with gate.lock:
                        count = len(gate.records)
                        used = gate.used_tokens
                        last_model_activity = gate.last_model_activity_utc
                    print(json.dumps({
                        "utc": utc_now(),
                        "new_requests_started": count,
                        "historic_plus_local_tokens_reserved_or_used": used,
                        "last_model_activity_utc": last_model_activity,
                        "index_log_bytes": log_path.stat().st_size,
                    }), flush=True)
                    last_notice = now
                time.sleep(2)
    except KeyboardInterrupt:
        interrupted = True
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    output = root / "output"
    required = ("documents", "text_units", "entities", "relationships", "communities", "community_reports")
    outputs_present = {name: (output / f"{name}.parquet").is_file() for name in required}
    new_tokens = gate.used_tokens - previous
    report = {
        "schema": "microsoft-graphrag-local-resume/1",
        "round": round_number,
        "started_utc": started,
        "ended_utc": utc_now(),
        "method": "standard",
        "provider": "local Ollama only",
        "model": config["completion_model"],
        "model_blob_sha256": config["completion_blob_sha256"],
        "embedding_blob_sha256": config["embedding_blob_sha256"],
        "cache_entries_before": cache_count,
        "cache_entries_after": sum(1 for item in (root / "cache" / "extract_graph").iterdir() if item.is_file()),
        "historic_tokens": HISTORIC_USED_TOKENS,
        "prior_probe_tokens": previous - HISTORIC_USED_TOKENS - 46_717,
        "prior_index_tokens": 46_717,
        "prior_all_runs_and_probes_tokens": previous,
        "local_request_limit": None,
        "local_token_limit": None,
        "local_temp_dir": str(local_tmp),
        "new_requests": len(gate.records),
        "new_tokens": new_tokens,
        "global_tokens_used": gate.used_tokens,
        "last_model_activity_utc": gate.last_model_activity_utc,
        "gate_failed": gate.failed,
        "interrupted": interrupted,
        "index_exit_code": process.returncode if process else None,
        "outputs_present": outputs_present,
        "real_acceptance": "complete" if process and process.returncode == 0 and not gate.failed and all(outputs_present.values()) else "incomplete",
        "attempts": gate.records,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--round", type=int, default=1)
    args = parser.parse_args()
    if args.preflight:
        previous, cached, config, offset = checked_prior(args.root, args.round)
        print(json.dumps({"ready": True, "model": config["completion_model"], "cached_graph_calls": cached, "prior_raw_responses": offset, "historic_plus_local_tokens": previous, "local_request_limit": None, "local_token_limit": None}))
    else:
        result = run(args.root, args.round)
        print(json.dumps({key: value for key, value in result.items() if key != "attempts"}, ensure_ascii=False, indent=2))
