"""Mark each source's first and last event, so the reading chain keeps its chapters.

Chapter boundaries are just marks on event nodes: the disclosure chain is not cut
and property values do not reset. Rebuilding a project drops them, so this runs
after a rebuild.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from narrative.graph_operations import GraphOperations

PROJECT = Path(__file__).resolve().parents[1] / "data" / "graph-projects" / "example.sqlite3"


def main() -> int:
    ops = GraphOperations(PROJECT)
    snapshot = ops.graph_snapshot()
    titles = {source["id"]: source["title"] for source in ops.sources()}

    by_source: dict[str, list[dict]] = {}
    for node in snapshot["nodes"]:
        disclosure = node.get("disclosure")
        if node["node_type"] == "event" and isinstance(disclosure, dict):
            by_source.setdefault(disclosure["source_id"], []).append(node)

    objects = []
    for source_id, group in by_source.items():
        # 章节的首尾按原文位置定；编号各类型一套，跨类型比不出先后。
        group.sort(key=lambda node: node["disclosure"]["start"])
        for node, role in ((group[0], "start"), (group[-1], "end")):
            updated = dict(node)
            marks = [
                mark for mark in (updated.get("source_marks") or [])
                if not (mark.get("source_id") == source_id and mark.get("role") == role)
            ]
            marks.append({"source_id": source_id, "role": role})
            updated["source_marks"] = marks
            objects.append(updated)
        print(f"{titles[source_id][-8:]:<10} {group[0]['id']}(start) / {group[-1]['id']}(end)")

    revision = ops.graph_project_info()["revision"]
    diagnostics = ops.change_precheck(objects, [], revision)
    errors = [item for item in diagnostics if item.get("severity") == "error"]
    if errors:
        print("预检失败:", json.dumps(errors[:2], ensure_ascii=False), file=sys.stderr)
        return 1
    candidate = ops.change_propose(objects, [], revision, source="correction", note="章节标记：每章首尾事件")
    print(f"修订 {revision} → {ops.change_accept(candidate, 1)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
