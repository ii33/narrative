"""Cohesive facades over the graph services.

Each facade exposes one narrow capability group and depends only on the service
that implements it, so a new adapter can import one facade instead of the whole
project surface. Operations exposes these capabilities to every adapter.
"""

from __future__ import annotations

from .graph_profile_ops import ProfileService
from .graph_read_ops import GraphReadService
from .graph_service import GraphService
from .graph_store import GraphStore
from .graph_timeline_ops import TimelineService
from .graphrag_ops import GraphRagService
from .property_store import PropertyStore


class GraphProjectFacade:
    """Project identity, sources and evidence location."""

    def __init__(self, store: GraphStore, service: GraphService, read: GraphReadService):
        self.store = store
        self.service = service
        self.read = read

    def info(self) -> dict:
        return self.store.info()

    def path(self) -> str:
        return str(self.store.path)

    def sources(self) -> list[dict]:
        return self.store.sources()

    def source(self, source_id: str) -> dict:
        return self.store.source(source_id)

    def import_source(self, title: str, text: str) -> str:
        return self.service.import_source(title, text)

    def locate(self, source_id: str, quotes: list[str], *, limit_per_quote: int = 20) -> list[dict]:
        return self.service.locate(source_id, quotes, limit_per_quote=limit_per_quote)

    def schema(self) -> dict:
        return self.read.schema()




class GraphPropertyFacade:
    """Current property intervals and disclosure projections."""

    def __init__(self, properties: PropertyStore):
        self.properties = properties

    def precheck(self, records: list[dict], expected_revision: int) -> list[dict]:
        return self.properties.precheck(records, expected_revision)




    def records(self) -> list[dict]:
        return self.properties.accepted_records()



    def state(self, node_id: str, property_id: str, at=None, **kwargs) -> dict:
        return self.properties.state(node_id, property_id, at, **kwargs)

    def disclosure_state(self, node_id: str, property_id: str, cut: dict, **kwargs) -> dict:
        return self.properties.disclosure_state(node_id, property_id, cut, **kwargs)

    def history(self, node_id: str, property_id: str, **kwargs) -> list[dict]:
        return self.properties.history(node_id, property_id, **kwargs)

    def node_properties(
        self,
        node_id: str,
        cut: object = None,
        include_derived: bool = False,
    ) -> dict:
        return self.properties.node_properties(
            node_id, cut, include_derived=include_derived
        )

    def audit(
        self, node_ids: set[str] | None = None
    ) -> dict:
        return self.properties.audit(node_ids)

    def world_state(self, at=None) -> dict:
        return self.properties.world_state(at, )

    def categories(self) -> dict[str, list[str]]:
        return self.properties.categories()

    def set_categories(self, config: object) -> dict:
        return self.properties.set_categories(config)

    def categorize(self, targets: list[str], category: str, note: str = "") -> dict:
        return self.properties.categorize(targets, category, note)









class GraphQueryFacade:
    """Validated views, queries and story-time projections."""

    def __init__(self, read: GraphReadService, timeline: TimelineService):
        self.read = read
        self.timeline = timeline

    def query(self, **kwargs) -> dict:
        return self.read.query(**kwargs)

    def view(self, spec: dict) -> dict:
        return self.read.view(spec)

    def display_map(self, **kwargs) -> dict:
        return self.read.display_map(**kwargs)

    def timeline_view(self, **kwargs) -> dict:
        return self.timeline.timeline(**kwargs)

    def event_compare(self, left_id: str, right_id: str, **kwargs) -> dict:
        return self.timeline.event_compare(left_id, right_id, **kwargs)

    def event_lines(self, **kwargs) -> dict:
        return self.timeline.event_lines(**kwargs)


class GraphProfileFacade:
    """Model provider settings kept outside the graph database."""

    def __init__(self, profile: ProfileService):
        self.profile = profile

    def settings(self, provider: str | None = None) -> dict:
        return self.profile.settings(provider)

    def catalog(self) -> list[dict]:
        return self.profile.catalog()

    def save(self, settings: dict) -> dict:
        return self.profile.save(settings)

    def set_title(self, title: str) -> None:
        self.profile.set_title(title)


class GraphRagFacade:
    """GraphRAG sidecar preview and source staging (never writes reviewed state)."""

    def __init__(self, graphrag: GraphRagService):
        self.graphrag = graphrag

    def preview(self, export_path, source_map) -> dict:
        return self.graphrag.preview(export_path, source_map)

    def stage_sources(self, run_root, source_ids) -> dict:
        return self.graphrag.stage_sources(run_root, source_ids)

    def preview_staged(self, run_root, export_path) -> dict:
        return self.graphrag.preview_staged(run_root, export_path)
