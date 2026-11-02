"""Read-side projections: schema, validated views, query and display maps."""

from __future__ import annotations

from .graph_service import GraphService
from .graph_store import GraphStore
from .property_store import PropertyStore


class GraphReadService:
    """Pure read paths over current graph state; never stages or writes a change."""

    def __init__(
        self,
        store: GraphStore,
        properties: PropertyStore,
        service: GraphService,
    ) -> None:
        self.store = store
        self.properties = properties
        self.service = service

    def schema(self) -> dict:
        from .graph_views import QUERY_SCHEMA_VERSION, GraphViewSpec

        return self.service.schema() | {
            "query_schema_version": QUERY_SCHEMA_VERSION,
            "query_schema": GraphViewSpec.model_json_schema(),
            "query_examples": [
                {
                    "view": "graph", "focus_id": "event-id", "depth": 2,
                    "direction": "both", "node_types": ["person", "event"],
                },
                {"view": "table", "group_by": "node_type", "limit": 200},
            ],
        }

    def view(self, spec: dict) -> dict:
        """Execute a validated structural query against one reviewed snapshot."""
        from .graph_views import graph_view, normalize_spec

        query = normalize_spec(spec)
        snapshot = self.store.snapshot()
        result = graph_view(snapshot, query)
        result["pending_excluded"] = True
        return result

    def query(
        self,
        *,
        node_id: str | None = None,
        query: str = "",
        node_ids: list[str] | None = None,
        kinds: list[str] | None = None,
        context_id: str = "canon",
        disclosure_cut: dict | None = None,
        limit: int | None = 200,
    ) -> dict:
        return run_query(
            self.store, self.properties, node_id=node_id, query=query,
            node_ids=node_ids, kinds=kinds,
            context_id=context_id,
            disclosure_cut=disclosure_cut, limit=limit,
        )

    def display_map(
        self
    ) -> dict:
        from .graph_display import display_map

        return display_map(self.store.snapshot())

def run_query(
    store: GraphStore,
    properties: PropertyStore,
    *,
    node_id: str | None = None,
    query: str = "",
    node_ids: list[str] | None = None,
    kinds: list[str] | None = None,
    context_id: str = "canon",
    disclosure_cut: dict | None = None,
    limit: int | None = 200,
) -> dict:
    """Query current objects with context and disclosure filters."""
    from .graph_query import query_graph

    snapshot = store.snapshot()
    return query_graph(
        snapshot, node_id=node_id, query=query,
        node_ids=node_ids, kinds=kinds,
        context_id=context_id, disclosure_cut=disclosure_cut,
        limit=limit,
    )
