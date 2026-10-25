"""Read-only GraphRAG sidecar bridge: preview only, never writes reviewed state."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .graph_service import GraphService
from .graph_store import GraphStore


class GraphRagService:
    def __init__(self, store: GraphStore, service: GraphService) -> None:
        self.store = store
        self.service = service

    def preview(self, export_path: str | Path, source_map: dict[str, str]) -> dict:
        """Read Microsoft GraphRAG sidecar hints without changing reviewed state."""
        from .ms_graphrag_adapter import preview_export

        return preview_export(export_path, source_map, self.service.locate)

    def stage_sources(self, run_root: str | Path, source_ids: list[str]) -> dict:
        """Stage selected sources in a new, isolated standard-index root."""
        if not source_ids or len(source_ids) != len(set(source_ids)):
            raise ValueError("Select one or more distinct source ids")
        sources = [self.store.source(source_id) for source_id in source_ids]
        root = Path(run_root)
        root.mkdir(parents=True, exist_ok=False)
        input_dir = root / "input"
        input_dir.mkdir()
        manifest = []
        for source in sources:
            text = source["text"]
            filename = f"{source['id']}.txt"
            (input_dir / filename).write_bytes(text.encode("utf-8"))
            manifest.append({
                "source_id": source["id"],
                "title": source["title"],
                "input_file": filename,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "characters": len(text),
            })
        payload = {"schema": "microsoft-graphrag-staging/1", "sources": manifest}
        (root / "narrative-source-map.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return payload

    def preview_staged(self, run_root: str | Path, export_path: str | Path) -> dict:
        """Preview an export against sources staged from this project."""
        manifest = json.loads(
            (Path(run_root) / "narrative-source-map.json").read_text(encoding="utf-8")
        )
        if manifest.get("schema") != "microsoft-graphrag-staging/1":
            raise ValueError("Unsupported GraphRAG staging manifest")
        source_map = {}
        for row in manifest["sources"]:
            source_id = row["source_id"]
            if self.store.source(source_id)["hash"] != row["sha256"]:
                raise ValueError("Staged source hash no longer matches this project")
            source_map[source_id] = source_id
            source_map[row["input_file"]] = source_id
        return self.preview(export_path, source_map)
