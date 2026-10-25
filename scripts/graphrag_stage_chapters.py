"""Stage a verified chapter slice through Operations for a new GraphRAG run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from narrative.graph_operations import GraphOperations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_txt", type=Path)
    parser.add_argument("source_manifest_json", type=Path)
    parser.add_argument("new_project_db", type=Path)
    parser.add_argument("new_index_root", type=Path)
    args = parser.parse_args()
    if args.new_project_db.exists() or args.new_index_root.exists():
        raise FileExistsError("Use a new project database and GraphRAG index root")
    source_bytes = args.source_txt.read_bytes()
    source = source_bytes.decode("utf-8")
    manifest = json.loads(args.source_manifest_json.read_text(encoding="utf-8"))
    if hashlib.sha256(source_bytes).hexdigest() != manifest["source_sha256_utf8"]:
        raise ValueError("Three-chapter source hash mismatch")
    chapters = manifest["chapters"]
    if len(chapters) != 3 or manifest.get("scope") != "first_three_complete_chapters":
        raise ValueError("Expected exactly the verified first three chapters")
    pieces = []
    for chapter in chapters:
        piece = source[chapter["start"] : chapter["end"]]
        if hashlib.sha256(piece.encode("utf-8")).hexdigest() != chapter["sha256_utf8"]:
            raise ValueError(f"Chapter {chapter['number']} hash mismatch")
        pieces.append((chapter["title"], piece))
    args.new_project_db.parent.mkdir(parents=True, exist_ok=True)
    ops = GraphOperations(args.new_project_db, create=True)
    source_ids = [ops.import_source(title, piece) for title, piece in pieces]
    staged = ops.graphrag_stage_sources(args.new_index_root, source_ids)
    print(json.dumps({
        "project_db": str(args.new_project_db),
        "index_root": str(args.new_index_root),
        "source_sha256": manifest["source_sha256_utf8"],
        "staged": staged,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
