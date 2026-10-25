"""Build the synthetic branch/merge demo through reviewed Operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from narrative.operations import Operations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/graph-projects/branch-merge-demo.sqlite3"),
        help="New demo project path; existing files are never overwritten",
    )
    args = parser.parse_args()
    target = args.output.resolve()
    if target.exists():
        parser.error(f"demo already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    spec = json.loads(Path(__file__).with_name("branch_merge.json").read_text(encoding="utf-8"))
    ops = Operations.create_graph_project(target)
    text = "".join(quote for _, _, quote in spec["reviewed_before"])
    text += spec["pending_before"][2]
    source_id = ops.import_source(spec["title"], text)

    nodes = [{"kind": "node", "id": event_id, "node_type": "event", "name": event_id}
             for event_id in spec["events"]]
    candidate_id = ops.graph_propose(
        nodes, ops.graph_project_info()["revision"], source="manual", note="Synthetic demo events"
    )
    ops.graph_accept(candidate_id, 1)

    for left_id, right_id, quote in spec["reviewed_before"]:
        pending = ops.graph_temporal_propose(
            source_id, quote, left_id, "end", "before", right_id, "start"
        )
        ops.graph_accept(pending["candidate_id"], pending["version"])

    left_id, right_id, quote = spec["pending_before"]
    pending = ops.graph_temporal_propose(
        source_id, quote, left_id, "end", "before", right_id, "start"
    )
    manifest = {
        "project": str(target),
        "source_id": source_id,
        "revision": ops.graph_project_info()["revision"],
        "pending_cross_line_candidate": pending["candidate_id"],
        "cross_line_before_review": ops.graph_event_compare(left_id, right_id),
        "fork_to_merge": ops.graph_event_compare("E0", "E9"),
        "independent_line": ops.graph_event_compare("E9", "C1"),
    }
    manifest_path = target.with_name(target.stem + "-manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"project": str(target), "manifest": str(manifest_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
