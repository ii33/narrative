"""One bounded local Ollama compatibility probe for GraphRAG community JSON schema.

Uses only a synthetic sentence. Run once in a fresh directory after the model
route and request budget have been reported to the user.
"""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path

from graphrag_bounded_run import BudgetGate
from graphrag_configure import verify_local_completion


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("new_probe_root", type=Path)
    parser.add_argument("--gate-no-thinking", action="store_true")
    args = parser.parse_args()
    verify_local_completion()
    args.new_probe_root.mkdir(parents=True, exist_ok=False)
    import os
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    from graphrag.index.operations.summarize_communities.community_reports_extractor import (
        CommunityReportResponse,
    )
    from graphrag_llm.completion.completion_factory import create_completion
    from graphrag_llm.config.model_config import ModelConfig

    gate = BudgetGate(args.new_probe_root, max_requests=1, no_thinking_max_tokens=6000 if args.gate_no_thinking else None)
    server = gate.serve()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    failure = None
    structured = False
    try:
        model = create_completion(ModelConfig(
            model_provider="openai",
            model="qwen3.8:iq3-mtp3",
            api_base=f"http://127.0.0.1:{server.server_port}/v1",
            api_key=gate.local_token,
            retry=None,
            call_args={"max_tokens": 6000 if args.gate_no_thinking else 500, "num_retries": 0, "temperature": 0},
        ))
        response = model.completion(
            messages="Synthetic test only: Alice gave Bob a copper key. Summarize this in the required report schema with one finding and rating 1.",
            response_format=CommunityReportResponse,
        )
        structured = isinstance(response.formatted_response, CommunityReportResponse)
    except Exception as exc:  # noqa: BLE001 - preserve first real compatibility failure
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    report = {
        "schema": "microsoft-graphrag-schema-probe/1",
        "input": "synthetic only",
        "provider": "local Ollama",
        "model": "qwen3.8:iq3-mtp3",
        "requests": len(gate.records),
        "accounted_tokens": gate.used_tokens,
        "structured_response_valid": structured,
        "failure": failure,
        "attempts": gate.records,
    }
    if gate.records:
        raw = json.loads((args.new_probe_root / "narrative-graphrag-raw" / gate.records[0]["raw_response_file"]).read_text(encoding="utf-8"))
        report["reasoning_chars"] = len(raw["choices"][0]["message"].get("reasoning") or "")
    (args.new_probe_root / "probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
