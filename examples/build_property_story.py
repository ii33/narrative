"""Build a review-backed property-state example without overwriting prior data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from narrative.operations import Operations


def _accept_property(ops, records: list[dict], note: str) -> int:
    revision = ops.graph_project_info()["revision"]
    diagnostics = ops.property_precheck(records, revision)
    errors = [row for row in diagnostics if row.get("severity") == "error"]
    if errors:
        raise ValueError(f"{note} precheck failed: {errors}")
    candidate_id = ops.property_propose(records, revision, source="manual", note=note)
    candidate = ops.property_candidate(candidate_id)
    return ops.property_accept(candidate_id, candidate["version"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/graph-projects/property-story-demo.sqlite3"),
        help="New persistent demo project path; existing files are never overwritten",
    )
    args = parser.parse_args()
    target = args.output.resolve()
    manifest_path = target.with_name(target.stem + "-manifest.json")
    if target.exists() or manifest_path.exists():
        parser.error(f"demo database or manifest already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    spec = json.loads(Path(__file__).with_name("property_story.json").read_text(encoding="utf-8"))
    ops = Operations.create_graph_project(target)
    source_text = "\n".join(
        row["quote"] for row in spec["temporal_order"]
    )
    source_id = ops.import_source(spec["title"], source_text)

    graph_objects = [
        {"kind": "node", "id": "mira", "node_type": "person", "name": "Mira"},
        {"kind": "node", "id": "jon", "node_type": "person", "name": "Jon"},
        {"kind": "node", "id": spec["item"]["id"], "node_type": "item", "name": spec["item"]["name"]},
        *({"kind": "node", "id": event_id, "node_type": "event", "name": event_id}
          for event_id in spec["events"]),
    ]
    graph_candidate = ops.graph_propose(
        graph_objects, ops.graph_project_info()["revision"],
        source="manual", note="Synthetic property-state example nodes",
    )
    ops.graph_accept(graph_candidate, 1)

    temporal_candidates = []
    for link in spec["temporal_order"]:
        proposed = ops.graph_temporal_propose(
            source_id, link["quote"], link["before"], "end", "before", link["after"], "start"
        )
        ops.graph_accept(proposed["candidate_id"], proposed["version"])
        temporal_candidates.append(proposed["candidate_id"])

    records = spec["property_records"]
    _accept_property(ops, [records["baseline"][0]], "Define holder property")
    _accept_property(ops, [records["baseline"][1]], "Set the initial owner")
    _accept_property(ops, [records["flashback_observation"]], "Record the flashback observation")
    _accept_property(ops, [records["transfer"]], "Transfer the key to Jon")

    failed_revision = ops.graph_project_info()["revision"]
    failed_diagnostics = ops.property_precheck([records["failed_transfer"]], failed_revision)
    before_failed_attempt = ops.property_state("key", "holder")
    attempted_expected = records["failed_transfer"]["effects"][0]["expected"]
    if any(row.get("severity") == "error" for row in failed_diagnostics):
        failed_status = "precheck_rejected"
    elif before_failed_attempt["state"] != attempted_expected:
        # Defensively reject a stale attempt if an adapter ever omits the
        # precondition diagnostic; a pending proposal never mutates reviewed state.
        failed_id = ops.property_propose(
            [records["failed_transfer"]], failed_revision, source="manual",
            note="Rejected failed transfer: expected old holder does not match",
        )
        failed_candidate = ops.property_candidate(failed_id)
        ops.property_reject(failed_id, failed_candidate["version"])
        failed_diagnostics = [{"severity": "error", "code": "EXPECTED_STATE",
                               "message": "expected old state does not match the reviewed holder"}]
        failed_status = "rejected"
    else:
        raise AssertionError("the refused transfer unexpectedly has a matching expected state")
    after_failed_attempt = ops.property_state("key", "holder")

    _accept_property(ops, [records["branches"][0]], "Apply branch A update")
    _accept_property(ops, [records["branches"][1]], "Apply branch B update")
    branch_conflict = ops.property_state("key", "holder")
    _accept_property(ops, [records["retraction"]], "Retract branch B update")
    after_retraction = ops.property_state("key", "holder")
    flashback_state = ops.property_state(
        "key", "holder", {"timeline_id": "story", "form": "event_boundary",
                           "event_id": "flashback", "boundary": "start"}
    )

    if after_failed_attempt["state"].get("value") != "jon":
        raise AssertionError("failed transfer changed the holder projection")
    if branch_conflict["state"].get("kind") != "conflict":
        raise AssertionError("unordered branch updates did not produce a conflict")
    if after_retraction["state"].get("kind") != "known" or after_retraction["state"].get("value") != "mira":
        raise AssertionError("retraction did not rebuild the holder projection")
    if flashback_state["state"].get("value") != "mira":
        raise AssertionError("flashback observation did not remain visible at its anchor")

    manifest = {
        "project": str(target), "source_id": source_id,
        "revision": ops.graph_project_info()["revision"],
        "temporal_candidate_ids": temporal_candidates,
        "failed_transfer_diagnostics": failed_diagnostics,
        "failed_transfer_status": failed_status,
        "state_after_failed_transfer": after_failed_attempt,
        "state_before_retraction": branch_conflict,
        "state_after_retraction": after_retraction,
        "flashback_state": flashback_state,
        "observation_history": ops.property_history("key", "holder"),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"project": str(target), "manifest": str(manifest_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
