"""Story-time projections and explicit temporal staging.

Everything here reads reviewed state or produces a pending candidate; nothing
accepts. Temporal relations are only written when the caller states them for a
quote that matches the source exactly once.
"""

from __future__ import annotations

from .graph_service import GraphService
from .graph_store import GraphStore
from .property_store import PropertyStore

TEMPORAL_RELATIONS = ("before", "after", "same", "overlaps", "contains")
TEMPORAL_BOUNDARIES = ("start", "end")
TEMPORAL_RELATION_LABELS = {
    "before": "先于", "after": "晚于", "same": "同时",
    "overlaps": "重叠", "contains": "包含时段",
}


class TimelineService:
    def __init__(
        self,
        store: GraphStore,
        service: GraphService,
        properties: PropertyStore,
    ) -> None:
        self.store = store
        self.service = service
        self.properties = properties

    def _snapshot_with_candidate(
        self, candidate_id: str | None, as_of_revision: int | None, rejected_message: str
    ) -> dict:
        snapshot = self.store.snapshot(as_of_revision)
        if candidate_id is None:
            return snapshot
        candidate = self.service.candidate(candidate_id)
        if candidate["status"] == "accepted":
            return self.store.snapshot(candidate["result_revision"])
        if candidate["status"] == "pending":
            if snapshot["revision"] != self.store.revision:
                raise ValueError("待审候选仅可与当前正式版本合并预览")
            diagnostics = self.service.precheck(
                candidate["objects"], candidate["expected_revision"]
            )
            if any(item["severity"] == "error" for item in diagnostics):
                raise ValueError(f"候选预检未通过：{diagnostics}")
            return preview_snapshot(snapshot, candidate)
        raise ValueError(rejected_message)

    # -- projections -----------------------------------------------------
    def timeline(
        self,
        candidate_id: str | None = None,
        context_id: str = "canon",
        story_cut: dict | None = None,
        disclosure_cut: dict | None = None,
        as_of_revision: int | None = None,
        limit: int = 2000,
    ) -> dict:
        from .graph_timeline import timeline

        snapshot = self._snapshot_with_candidate(
            candidate_id, as_of_revision, "已拒绝候选不可作为层级预览"
        )
        return timeline(
            snapshot, context_id=context_id,
            disclosure_cut=disclosure_cut, limit=limit,
        )

    def event_compare(
        self,
        left_id: str,
        right_id: str,
        context_id: str = "canon",
        disclosure_cut: dict | None = None,
        as_of_revision: int | None = None,
        axis: str = "disclosure",
    ) -> dict:
        """Compare two events on one axis: reading order or story order."""
        from .graph_event_order import compare_events

        return compare_events(
            self.store.snapshot(as_of_revision), left_id, right_id,
            context_id=context_id, disclosure_cut=disclosure_cut, axis=axis,
        )

    def event_lines(
        self,
        context_id: str = "canon",
        disclosure_cut: dict | None = None,
        as_of_revision: int | None = None,
    ) -> dict:
        """Project reviewed temporal assertions into a visual event-line view."""
        from .graph_event_order import event_lines

        return event_lines(
            self.store.snapshot(as_of_revision),
            context_id=context_id, disclosure_cut=disclosure_cut,
        )

    # -- staging ---------------------------------------------------------


def preview_snapshot(snapshot: dict, candidate: dict) -> dict:
    """Overlay a pending candidate's objects on the accepted snapshot for preview.

    Nothing is written: the merged dict is a throwaway view used by read-only
    projections.
    """
    objects = {item["id"]: item for item in snapshot.get("objects", [])}
    for obj in candidate.get("objects") or []:
        objects[obj["id"]] = obj
    merged = dict(snapshot)
    merged["objects"] = list(objects.values())
    groups: dict[str, list[dict]] = {key: [] for key in ("nodes", "contexts", "timelines", "source_orders")}
    for obj in merged["objects"]:
        groups.setdefault(obj["kind"] + "s", []).append(obj)
    merged.update(groups)
    return merged
