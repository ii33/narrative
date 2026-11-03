"""Validate adaptation/assets over an existing isolated real-model acceptance graph.

Does not call a model or modify extraction records. Run after a successful live
minimal extraction; all following modifications are intentional adaptation edits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from narrative.operations import Operations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    ops = Operations.open(args.project)
    before = ops.graph_snapshot()
    properties = ops.property_records()
    assert before["nodes"], "Requires actual successfully imported model results"
    result = ops.sync_adaptation()
    assert result["status"] == "accepted", result
    events = [n for n in result["nodes"] if n["node_type"] == "event"]
    people = [n for n in result["nodes"] if n["node_type"] == "person"]
    locations = [n for n in result["nodes"] if n["node_type"] == "location"]
    assert len(events) >= 2 and people and locations
    first, second = events[:2]
    changed = ops.edit_adaptation_node(first["id"], {"name": "改编到访"})
    assert changed["status"] == "accepted", changed
    changed = ops.edit_adaptation_node(changes={"name": "改编新角色", "node_type": "person", "point_id": second["id"]}, after_id=second["id"])
    assert changed["status"] == "accepted", changed
    added = next(n for n in changed["nodes"] if n["name"] == "改编新角色")
    changed = ops.set_adaptation_property(added["id"], "服装", "蓝衣", event_id=first["id"])
    assert changed["status"] == "accepted", changed
    row = next(p for p in changed["properties"] if p.get("node_id") == added["id"] and p.get("property_id") == "服装")
    changed = ops.set_adaptation_property(added["id"], "服装", "灰衣", event_id=second["id"], record_id=row["id"])
    assert changed["status"] == "accepted", changed
    changed = ops.copy_adaptation_node(second["id"])
    assert changed["status"] == "accepted", changed
    copied = next(n for n in changed["nodes"] if n["id"].startswith("AD-") and n["node_type"] == "event")
    assert ops.suppress_adaptation_node(copied["id"])["status"] == "accepted"
    assert ops.move_adaptation_node(second["id"], after_id=first["id"])["status"] == "accepted"
    assert ops.sync_adaptation()["status"] == "accepted"
    assert next(n for n in ops.adaptation_snapshot()["nodes"] if n["id"] == first["id"])["name"] == "改编到访"
    cards = []
    for category, node_id in [("appearance", people[0]["id"]), ("location", locations[0]["id"]), ("material", "")]:
        cards.append(ops.save_asset({"category": category, "node_id": node_id, "title": "改编设定", "detail": "独立视觉设定", "prompt": "水墨风格"}))
    assert ops.check_adaptation()["ok"]
    assert ops.graph_snapshot() == before and ops.property_records() == properties
    report = {"status": "passed", "model_requests_added": 0,
              "scope": "deterministic adaptation/assets over real automatically imported extraction",
              "source_revision": before["revision"], "extraction_unchanged": True,
              "adaptation": ops.adaptation_snapshot(), "assets": cards,
              "static_check": ops.check_adaptation(),
              "implementation_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path("src/narrative/adaptation_operations.py"), Path("src/narrative/asset_operations.py")]}}
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "extraction_unchanged": True}))


if __name__ == "__main__":
    main()
