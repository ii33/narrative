"""Test a synthetic no-thinking request against the pinned local Ollama model."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from graphrag_configure import LOCAL_OLLAMA_BASE, verify_local_completion


def probe(root: Path) -> dict:
    verify_local_completion()
    root.mkdir(parents=True, exist_ok=False)
    body = json.dumps({
        "model": "qwen3.8:iq3-mtp3",
        "messages": [{"role": "user", "content": "Synthetic only. Return JSON with ok=true."}],
        "stream": False,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "none",
        "max_tokens": 300,
        "temperature": 0,
    }).encode("utf-8")
    request = Request(
        f"{LOCAL_OLLAMA_BASE}/chat/completions",
        data=body,
        headers={"Authorization": "Bearer ollama", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with build_opener(ProxyHandler({})).open(request, timeout=300) as response:
        raw = response.read()
        status = response.status
    (root / "response.json").write_bytes(raw)
    parsed = json.loads(raw)
    choice = parsed["choices"][0]
    message = choice["message"]
    report = {
        "model": parsed.get("model"),
        "http_status": status,
        "finish_reason": choice.get("finish_reason"),
        "usage": parsed.get("usage"),
        "content_is_valid_json": isinstance(json.loads(message.get("content") or "null"), dict),
        "reasoning_chars": len(message.get("reasoning") or ""),
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("new_root", type=Path)
    print(json.dumps(probe(parser.parse_args().new_root), indent=2))
