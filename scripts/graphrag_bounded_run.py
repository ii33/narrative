"""Run official GraphRAG standard behind a local Ollama budget gate.

Use only after the model route and token ceiling have been approved. The gate
records every raw model response and blocks calls after a failure or budget
limit. It never saves the upstream key or the request prompt itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from graphrag_configure import LOCAL_OLLAMA_BASE, verify_local_completion, verify_local_embedding

MAX_REQUESTS = 11
MAX_TOTAL_TOKENS = 250_000
MAX_OUTPUT_TOKENS = 12_000
UPSTREAM_URL = f"{LOCAL_OLLAMA_BASE}/chat/completions"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class BudgetGate:
    def __init__(
        self,
        run_root: Path,
        upstream_key: str = "ollama",
        max_requests: int | None = MAX_REQUESTS,
        max_total_tokens: int | None = MAX_TOTAL_TOKENS,
        initial_used_tokens: int = 0,
        attempt_offset: int = 0,
        no_thinking_max_tokens: int | None = None,
    ):
        self.root = run_root
        self.key = upstream_key
        self.local_token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.records: list[dict] = []
        self.used_tokens = initial_used_tokens
        self.failed = False
        self.max_requests = max_requests
        self.max_total_tokens = max_total_tokens
        self.attempt_offset = attempt_offset
        self.last_model_activity_utc: str | None = None
        self.no_thinking_max_tokens = no_thinking_max_tokens

    def serve(self) -> ThreadingHTTPServer:
        gate = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def respond(self, status: int, body: bytes, content_type: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                if self.path != "/v1/chat/completions":
                    self.respond(404, b'{"error":"unknown route"}')
                    return
                if not secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {gate.local_token}"):
                    self.respond(401, b'{"error":"unauthorized"}')
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 16 * 1024 * 1024:
                        raise ValueError("invalid body length")
                    raw_request = self.rfile.read(length)
                    payload = json.loads(raw_request)
                    if not isinstance(payload, dict) or payload.get("stream"):
                        raise ValueError("non-streaming JSON completion required")
                    requested_output = int(payload.get("max_tokens", payload.get("max_completion_tokens", MAX_OUTPUT_TOKENS)))
                    if requested_output < 1 or requested_output > MAX_OUTPUT_TOKENS:
                        raise ValueError("completion limit exceeded")
                    if payload.get("model") != "qwen3.8:iq3-mtp3":
                        raise ValueError("Only the pinned local Qwen model is permitted")
                    forwarded_request = raw_request
                    if gate.no_thinking_max_tokens == requested_output:
                        payload["reasoning_effort"] = "none"
                        forwarded_request = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    # UTF-8 bytes plus protocol allowance conservatively reserve
                    # prompt tokens; actual provider usage replaces the reserve.
                    reserved = len(raw_request) + requested_output + 1024
                    with gate.lock:
                        if (
                            gate.failed
                            or (gate.max_requests is not None and len(gate.records) >= gate.max_requests)
                            or (gate.max_total_tokens is not None and gate.used_tokens + reserved > gate.max_total_tokens)
                        ):
                            gate.failed = True
                            self.respond(429, b'{"error":"GraphRAG budget gate closed"}')
                            return
                        attempt = gate.attempt_offset + len(gate.records) + 1
                        gate.used_tokens += reserved
                        record = {
                            "attempt": attempt,
                            "started_utc": utc_now(),
                            "request_sha256": hashlib.sha256(raw_request).hexdigest(),
                            "reserved_tokens": reserved,
                            "requested_output_tokens": requested_output,
                            "model": payload.get("model"),
                            "forwarded_request_sha256": hashlib.sha256(forwarded_request).hexdigest(),
                            "reasoning_effort_injected": forwarded_request != raw_request,
                        }
                        gate.records.append(record)
                        gate.last_model_activity_utc = record["started_utc"]
                    request = Request(
                        UPSTREAM_URL,
                        data=forwarded_request,
                        headers={
                            "Authorization": f"Bearer {gate.key}",
                            "Content-Type": "application/json",
                        },
                        method="POST",
                    )
                    try:
                        with urlopen(request, timeout=1800) as response:
                            status = response.status
                            raw_response = response.read()
                            content_type = response.headers.get("Content-Type", "application/json")
                    except HTTPError as exc:
                        status = exc.code
                        raw_response = exc.read()
                        content_type = exc.headers.get("Content-Type", "application/json")
                    except (OSError, URLError) as exc:
                        status = 502
                        raw_response = json.dumps({"error": str(exc)}).encode("utf-8")
                        content_type = "application/json"
                    response_path = gate.root / "narrative-graphrag-raw" / f"response-{attempt:03}.json"
                    response_path.parent.mkdir(parents=True, exist_ok=True)
                    response_path.write_bytes(raw_response)
                    try:
                        response_json = json.loads(raw_response)
                    except (ValueError, UnicodeError):
                        response_json = {}
                    usage = response_json.get("usage") if isinstance(response_json, dict) else None
                    reported_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
                    choices = response_json.get("choices", []) if isinstance(response_json, dict) else []
                    finish_reason = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None
                    with gate.lock:
                        if isinstance(reported_tokens, int) and reported_tokens >= 0:
                            gate.used_tokens += reported_tokens - reserved
                        if status >= 400 or not isinstance(usage, dict) or finish_reason != "stop":
                            gate.failed = True
                        record.update({
                            "ended_utc": utc_now(),
                            "http_status": status,
                            "raw_response_file": response_path.name,
                            "raw_response_sha256": hashlib.sha256(raw_response).hexdigest(),
                            "usage": usage,
                            "finish_reason": finish_reason,
                        })
                        gate.last_model_activity_utc = record["ended_utc"]
                    self.respond(status, raw_response, content_type)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    gate.failed = True
                    self.respond(400, json.dumps({"error": str(exc)}).encode("utf-8"))

        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def preflight(root: Path, *, allow_resume: bool = False) -> tuple[dict, list[dict]]:
    verify_local_embedding()
    verify_local_completion()
    settings = (root / "settings.yaml").read_text(encoding="utf-8")
    if "${GRAPHRAG_API_BASE}" not in settings or "${GRAPHRAG_API_KEY}" not in settings:
        raise ValueError("Run settings must route through the budget gate")
    if not allow_resume and ((root / "output").exists() or (root / "narrative-graphrag-run.json").exists()):
        raise FileExistsError("Use a new isolated index root; do not resume or overwrite")
    config = json.loads((root / "narrative-graphrag-config.json").read_text(encoding="utf-8"))
    if config["method"] != "standard" or config["embedding_blob_sha256"] != (
        "daec91ffb5dd0c27411bd71f29932917c49cf529a641d0168496c3a501e3062c"
    ):
        raise ValueError("Unapproved GraphRAG method or embedding version")
    if config.get("completion_provider") != "local Ollama OpenAI-compatible endpoint" or config.get("completion_blob_sha256") != (
        "c0b7c3038681ed2e3040456c1dd45f9858b6c2290bed172c70388a94874f3eee"
    ):
        raise ValueError("Unapproved local completion model")
    for name, expected_hash in config["prompt_sha256"].items():
        if hashlib.sha256((root / "prompts" / name).read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"GraphRAG prompt changed after configuration: {name}")
    sources = json.loads((root / "narrative-source-map.json").read_text(encoding="utf-8"))["sources"]
    for source in sources:
        if hashlib.sha256((root / "input" / source["input_file"]).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("Staged GraphRAG input changed after source verification")
    return config, sources


def run(
    root: Path,
    max_requests: int | None,
    max_total_tokens: int | None = MAX_TOTAL_TOKENS,
) -> dict:
    if max_requests is not None and not 1 <= max_requests <= MAX_REQUESTS:
        raise ValueError("Request allowance must be between 1 and 11, or None for unbounded")
    config, sources = preflight(root)
    gate = BudgetGate(root, max_requests=max_requests, max_total_tokens=max_total_tokens)
    server = gate.serve()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    env = os.environ.copy()
    env["GRAPHRAG_API_BASE"] = f"http://127.0.0.1:{server.server_port}/v1"
    env["GRAPHRAG_API_KEY"] = gate.local_token
    env["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    executable = Path(sys.executable).with_name("graphrag.exe")
    log_path = root / "narrative-graphrag-index.log"
    try:
        with log_path.open("wb") as log:
            process = subprocess.run(
                [str(executable), "index", "--root", str(root), "--method", "standard"],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    required_outputs = ("documents", "text_units", "entities", "relationships", "communities", "community_reports")
    output_complete = all((root / "output" / f"{name}.parquet").is_file() for name in required_outputs)
    report = {
        "schema": "microsoft-graphrag-bounded-run/1",
        "method": "standard",
        "provider": "local Ollama",
        "model": config["completion_model"],
        "prompt_version": config["prompt_version"],
        "prompt_sha256": config["prompt_sha256"],
        "source_sha256": {row["source_id"]: row["sha256"] for row in sources},
        "embedding_blob_sha256": config["embedding_blob_sha256"],
        "max_requests": max_requests,
        "max_total_tokens": max_total_tokens,
        "requests": len(gate.records),
        "accounted_tokens": gate.used_tokens,
        "failed": gate.failed,
        "required_outputs_present": output_complete,
        "index_exit_code": process.returncode,
        "real_acceptance": "complete" if process.returncode == 0 and not gate.failed and gate.records and output_complete else "incomplete",
        "attempts": gate.records,
    }
    (root / "narrative-graphrag-run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--preflight", action="store_true", help="Verify local inputs/config without any network call")
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    parser.add_argument(
        "--unbounded",
        action="store_true",
        help="No request or token ceiling; failed calls still stop the run",
    )
    args = parser.parse_args()
    if args.preflight:
        config, sources = preflight(args.run_root)
        print(json.dumps({"status": "ready", "method": config["method"], "sources": len(sources), "prompt_files": len(config["prompt_sha256"])}))
        return
    max_requests = None if args.unbounded else args.max_requests
    max_total_tokens = None if args.unbounded else MAX_TOTAL_TOKENS
    print(json.dumps(run(args.run_root, max_requests, max_total_tokens), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
