"""Archive exactly one truncated claim cache entry before a focused rerun."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def archive(root: Path) -> dict:
    root = root.resolve()
    cache = (root / "cache" / "extract_claims").resolve()
    if not cache.is_relative_to(root):
        raise ValueError("Claim cache escaped the isolated index root")
    report_path = root / "narrative-graphrag-claim-repair.json"
    archive_dir = root / "cache-archive" / "truncated-claim-before-resume-4"
    if report_path.exists() or archive_dir.exists():
        raise FileExistsError("Claim cache repair already recorded")
    entries = [item for item in cache.iterdir() if item.is_file()]
    if len(entries) != 15:
        raise ValueError("Expected all 15 claim cache entries")
    truncated = []
    for item in entries:
        payload = json.loads(item.read_text(encoding="utf-8"))
        finish = payload["result"]["response"]["choices"][0]["finish_reason"]
        if finish != "stop":
            truncated.append((item, finish))
    if len(truncated) != 1 or truncated[0][1] != "length":
        raise ValueError(f"Expected exactly one truncated claim cache entry, got {len(truncated)}")
    source = truncated[0][0]
    archive_dir.mkdir(parents=True, exist_ok=False)
    target = (archive_dir / source.name).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Archive target escaped the isolated index root")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source.rename(target)
    report = {
        "schema": "microsoft-graphrag-claim-cache-repair/1",
        "original_relative_path": str(source.relative_to(root)),
        "archived_relative_path": str(target.relative_to(root)),
        "sha256": digest,
        "finish_reason": "length",
        "remaining_claim_cache_entries": 14,
        "action": "archived one truncated generated response; no source, graph, community or vector data deleted",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    print(json.dumps(archive(parser.parse_args().root), ensure_ascii=False, indent=2))
