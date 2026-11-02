"""Story-time projections over current graph objects.

Everything here reads current state; nothing
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

    # -- projections -----------------------------------------------------
    def timeline(
        self,
        context_id: str = "canon",
        story_cut: dict | None = None,
        disclosure_cut: dict | None = None,
        limit: int = 2000,
    ) -> dict:
        from .graph_timeline import timeline

        snapshot = self.store.snapshot()
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
        axis: str = "disclosure",
    ) -> dict:
        """Compare two events on one axis: reading order or story order."""
        from .graph_event_order import compare_events

        return compare_events(
            self.store.snapshot(), left_id, right_id,
            context_id=context_id, disclosure_cut=disclosure_cut, axis=axis,
        )

    def event_lines(
        self,
        context_id: str = "canon",
        disclosure_cut: dict | None = None,
    ) -> dict:
        """Project reviewed temporal assertions into a visual event-line view."""
        from .graph_event_order import event_lines

        return event_lines(
            self.store.snapshot(),
            context_id=context_id, disclosure_cut=disclosure_cut,
        )
