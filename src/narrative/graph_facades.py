"""Cohesive facades over the graph services.

Each facade exposes one narrow capability group and depends only on the service
that implements it, so a new adapter can import one facade instead of the whole
project surface. ``GraphOperations`` keeps the historical method names as thin
delegations to these facades.
"""

from __future__ import annotations

from .change_service import ChangeService
from .graph_analysis_ops import AnalysisService
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

    def locate(self, source_id: str, quote: str) -> list[dict]:
        return self.service.locate(source_id, quote)

    def schema(self) -> dict:
        return self.read.schema()


class GraphReviewFacade:
    """Graph object review: precheck, propose, edit, accept, reject."""

    def __init__(self, service: GraphService):
        self.service = service

    def precheck(self, objects: list[dict], expected_revision: int) -> list[dict]:
        return self.service.precheck(objects, expected_revision)

    def propose(self, objects: list[dict], expected_revision: int, **kwargs) -> str:
        return self.service.propose(objects, expected_revision, **kwargs)

    def candidates(self, status: str | None = None) -> list[dict]:
        return self.service.candidates(status)

    def candidate(self, candidate_id: str) -> dict:
        return self.service.candidate(candidate_id)

    def edit(self, candidate_id: str, objects: list[dict], version: int) -> dict:
        return self.service.edit_candidate(candidate_id, objects, version)

    def select_operations(self, candidate_id: str, keep_ids: list[str], version: int) -> dict:
        return self.service.select_operations(candidate_id, keep_ids, version)

    def accept(self, candidate_id: str, version: int) -> int:
        return self.service.accept(candidate_id, version)

    def reject(self, candidate_id: str, version: int) -> None:
        self.service.reject(candidate_id, version)


class GraphPropertyFacade:
    """Property records: review plus story-time and disclosure projections."""

    def __init__(self, properties: PropertyStore):
        self.properties = properties

    def precheck(self, records: list[dict], expected_revision: int) -> list[dict]:
        return self.properties.precheck(records, expected_revision)

    def propose(self, records: list[dict], expected_revision: int, **kwargs) -> str:
        return self.properties.propose(records, expected_revision, **kwargs)

    def candidates(self, status: str | None = None) -> list[dict]:
        return self.properties.candidates(status)

    def candidate(self, candidate_id: str) -> dict:
        return self.properties.candidate(candidate_id)

    def records(
        self, as_of_revision: int | None = None, *, include_retracted: bool = False
    ) -> list[dict]:
        return self.properties.accepted_records(
            as_of_revision, include_retracted=include_retracted
        )

    def accept(self, candidate_id: str, version: int) -> int:
        return self.properties.accept(candidate_id, version)

    def reject(self, candidate_id: str, version: int) -> None:
        self.properties.reject(candidate_id, version)

    def state(self, node_id: str, property_id: str, at=None, **kwargs) -> dict:
        return self.properties.state(node_id, property_id, at, **kwargs)

    def disclosure_state(self, node_id: str, property_id: str, cut: dict, **kwargs) -> dict:
        return self.properties.disclosure_state(node_id, property_id, cut, **kwargs)

    def history(self, node_id: str, property_id: str, **kwargs) -> list[dict]:
        return self.properties.history(node_id, property_id, **kwargs)

    def node_properties(self, node_id: str, as_of_revision: int | None = None) -> dict:
        return self.properties.node_properties(node_id, as_of_revision)

    def audit(
        self, node_ids: set[str] | None = None, as_of_revision: int | None = None
    ) -> dict:
        return self.properties.audit(node_ids, as_of_revision)

    def world_state(self, at=None, as_of_revision: int | None = None) -> dict:
        return self.properties.world_state(at, as_of_revision=as_of_revision)

    def categories(self) -> dict[str, list[str]]:
        return self.properties.categories()

    def set_categories(self, config: object) -> dict:
        return self.properties.set_categories(config)

    def categorize(self, targets: list[str], category: str, note: str = "") -> dict:
        return self.properties.categorize(targets, category, note)


class GraphChangeFacade:
    """Unified mixed candidate: graph objects and property records together."""

    def __init__(self, changes: ChangeService):
        self.changes = changes

    def precheck(self, graph_objects, property_records, expected_revision: int) -> list[dict]:
        return self.changes.precheck(graph_objects, property_records, expected_revision)

    def propose(self, graph_objects, property_records, expected_revision: int, **kwargs) -> str:
        return self.changes.propose(
            graph_objects, property_records, expected_revision, **kwargs
        )

    def candidates(self, status: str | None = None) -> list[dict]:
        return self.changes.candidates(status)

    def candidate(self, candidate_id: str) -> dict:
        return self.changes.candidate(candidate_id)

    def accept(self, candidate_id: str, version: int) -> int:
        return self.changes.accept(candidate_id, version)

    def reject(self, candidate_id: str, version: int) -> None:
        self.changes.reject(candidate_id, version)


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

    def candidate_preview(self, candidate_id: str) -> dict:
        return self.read.candidate_preview(candidate_id)

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


class GraphAnalysisFacade:
    """Graph analysis runs: start, inspect, pause, instruct, cancel."""

    def __init__(self, analysis: AnalysisService):
        self.analysis = analysis

    def run_dir(self):
        return self.analysis.run_dir()

    def start(self, source_ids: list[str], **kwargs) -> str:
        return self.analysis.start(source_ids, **kwargs)

    def status(self, run_id: str) -> dict:
        return self.analysis.status(run_id)

    def runs(self) -> list[dict]:
        return self.analysis.runs()

    def trace(self, run_id: str) -> dict:
        return self.analysis.trace(run_id)

    def response(self, run_id: str, round_number: int) -> dict:
        return self.analysis.response(run_id, round_number)

    def control(self, run_id: str) -> dict:
        return self.analysis.control(run_id)

    def take_instructions(self, run_id: str) -> list[dict]:
        return self.analysis.take_instructions(run_id)

    def pause(self, run_id: str) -> None:
        self.analysis.pause(run_id)

    def resume(self, run_id: str) -> None:
        self.analysis.resume(run_id)

    def instruct(self, run_id: str, text: str) -> str:
        return self.analysis.instruct(run_id, text)

    def cancel(self, run_id: str) -> None:
        self.analysis.cancel(run_id)


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
