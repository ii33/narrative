"""Read-side projections: schema, validated views, query and display maps."""

from __future__ import annotations

from .graph_service import GraphService
from .graph_store import GraphStore
from .property_store import PropertyStore


class GraphReadService:
    """Pure read paths over one reviewed snapshot.

    The service never writes. Candidate previews are merged in memory after the
    same precheck the review path uses, so a preview cannot bypass validation.
    """

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
        snapshot = self.store.snapshot(query["as_of_revision"])
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
        as_of_revision: int | None = None,
        limit: int | None = 200,
    ) -> dict:
        return run_query(
            self.store, self.properties, node_id=node_id, query=query,
            node_ids=node_ids, kinds=kinds,
            context_id=context_id,
            disclosure_cut=disclosure_cut, as_of_revision=as_of_revision, limit=limit,
        )

    def display_map(
        self, candidate_id: str | None = None, as_of_revision: int | None = None
    ) -> dict:
        from .graph_display import display_map

        candidate = self.service.candidate(candidate_id) if candidate_id else None
        return display_map(self.store.snapshot(as_of_revision), candidate)

    def candidate_preview(self, candidate_id: str) -> dict:
        from .graph_display import candidate_preview

        candidate = self.service.candidate(candidate_id)
        return candidate_preview(self.store.snapshot(), candidate)


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
    as_of_revision: int | None = None,
    limit: int | None = 200,
) -> dict:
    """Query a snapshot with cross-family dependency eligibility applied."""
    from .graph_query import query_graph

    snapshot = store.snapshot(as_of_revision)
    return query_graph(
        snapshot, node_id=node_id, query=query,
        node_ids=node_ids, kinds=kinds,
        context_id=context_id, disclosure_cut=disclosure_cut,
        limit=limit,
    )
