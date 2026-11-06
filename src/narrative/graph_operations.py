"""Public schema-10 project boundary for UI, CLI and model agents.

This class is a composition root: every method
delegates to a narrow facade in :mod:`graph_facades`. New code should depend on a
facade (`ops.project`, `ops.properties`, `ops.query`, ...) instead of this class.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .adaptation_operations import AdaptationOperationsMixin
from .adaptation_views import AdaptationViewsMixin
from .asset_operations import AssetOperationsMixin
from .assistant_access import AssistantAccessOperationsMixin
from .assistant_host import HostOperationsMixin
from .chat_attachments import ChatAttachmentOperationsMixin
from .compile_settings import CompileSettingsOperationsMixin
from .content_operations import install_content_operations
from .graph_facades import (
    GraphProfileFacade,
    GraphProjectFacade,
    GraphPropertyFacade,
    GraphQueryFacade,
    GraphRagFacade,
)
from .graph_profile_ops import ProfileService
from .graph_read_ops import GraphReadService
from .graph_service import GraphService
from .graph_store import GraphStore
from .graph_timeline_ops import TimelineService
from .graphrag_ops import GraphRagService
from .node_material import MaterialOperationsMixin
from .project_style import ProjectStyleOperationsMixin
from .prompt_settings import PromptSettingsOperationsMixin
from .property_store import PropertyStore
from .provider_store import ProviderStore
from .screenplay_operations import ScreenplayOperationsMixin
from .screenplay_timeline import ScreenplayTimelineOperationsMixin
from .screenplay_writing import WritingOperationsMixin
from .storyboard_operations import StoryboardOperationsMixin
from .task_write_summary import TaskWriteSummaryOperationsMixin
from .usage_statistics import UsageStatisticsOperationsMixin


@dataclass(frozen=True)
class _ProjectPath:
    path: str


class GraphOperations(ChatAttachmentOperationsMixin, AssistantAccessOperationsMixin, HostOperationsMixin, StoryboardOperationsMixin, TaskWriteSummaryOperationsMixin, UsageStatisticsOperationsMixin, CompileSettingsOperationsMixin, ProjectStyleOperationsMixin, PromptSettingsOperationsMixin, AdaptationOperationsMixin, AssetOperationsMixin, AdaptationViewsMixin,
                      ScreenplayOperationsMixin, ScreenplayTimelineOperationsMixin, WritingOperationsMixin,
                      MaterialOperationsMixin):
    _copy_report: dict

    def __init__(self, path: str | Path, *, create: bool = False):
        self._store = GraphStore(path, create=create)
        self._service = GraphService(self._store)
        self._properties = PropertyStore(self._store)
        self._provider_store = ProviderStore()
        self._project_id = self._store.info()["project_id"]

        self._timeline = TimelineService(self._store, self._service, self._properties)
        self._read = GraphReadService(self._store, self._properties, self._service)
        self._profile = ProfileService(self._store, self._project_id, self._provider_store)
        self._graphrag = GraphRagService(self._store, self._service)

        self.project = GraphProjectFacade(self._store, self._service, self._read)
        self.properties = GraphPropertyFacade(self._properties)
        self.query = GraphQueryFacade(self._read, self._timeline)
        self.profiles = GraphProfileFacade(self._profile)
        self.graphrag = GraphRagFacade(self._graphrag)

    # -- project ---------------------------------------------------------
    def check_project(self) -> dict:
        from .project_check import check

        return check(self)

    def record_property_change(self, node_id, property_id, event_id, op, *, expected_revision, reason, **fields):
        from .property_operations import record_change
        return record_change(self, node_id, property_id, event_id, op, expected_revision=expected_revision, reason=reason, **fields)

    def correct_property(self, record_id, changes, reason, *, expected_revision):
        from .temporal_state import correct
        if not isinstance(changes, dict) or not changes:
            raise ValueError('请提供明确更正字段')
        return correct(self, record_id, changes, reason, expected_revision=expected_revision)

    def delete_property_records(self, ids: list[str], reason: str, *, expected_revision: int) -> dict:
        from .temporal_state import delete_records
        return delete_records(self, ids, reason, expected_revision=expected_revision)

    def rename_property(self, from_id: str, to_id: str, reason: str, expected_revision: int) -> dict:
        from .temporal_state import rename
        return rename(self, from_id, to_id, reason, expected_revision)

    def merge_properties(self, source_id: str, target_id: str, reason: str, expected_revision: int) -> dict:
        from .temporal_state import rename
        return rename(self, source_id, target_id, reason, expected_revision, merge=True)

    def rollback_preview(self, source_id: str, start: int, expected_revision: int) -> dict:
        from .temporal_state import rollback_plan
        return rollback_plan(self, source_id, start, expected_revision)[2]

    def rollback_to(self, source_id: str, start: int, expected_revision: int, reason: str) -> dict:
        from .temporal_state import rollback
        return rollback(self, source_id, start, expected_revision, reason)

    def capture_changes(self):
        from .batch_write import capture
        return capture(self._store)

    def prepare_changes(self, graph_objects: list[dict], property_records: list[dict],
                        expected_revision: int, *, snapshot):
        from .batch_write import prepare
        if snapshot.store.path != self._store.path:
            raise ValueError("批次快照不属于当前工程")
        return prepare(snapshot, graph_objects, property_records, expected_revision)

    def commit_changes(self, prepared, reason: str, *, _transaction_callback=None) -> dict:
        from .batch_write import commit
        return commit(self._store, prepared, reason, transaction_callback=_transaction_callback)

    def graph_project_info(self) -> dict:
        return self.project.info()

    def project_info(self) -> _ProjectPath:
        return _ProjectPath(self.project.path())

    def import_source(self, title: str, text: str) -> str:
        return self.project.import_source(title, text)

    def import_source_file(self, file_path: str | Path, title: str = "") -> str:
        from .source_documents import read_source_file

        return self.import_source(title or Path(file_path).stem, read_source_file(file_path))

    def source_slices(self, source_id: str) -> list[dict]:
        from .source_slices import SourceSlices

        return SourceSlices(self._store).list(source_id)

    def create_source_slices(self, source_id: str, slices: list[dict]) -> list[dict]:
        from .source_slices import SourceSlices

        return SourceSlices(self._store).create(source_id, slices)

    def preview_pattern_slices(self, source_id: str, pattern: str, start_index: int = 0,
                               limit: int | None = None) -> dict:
        from .source_slices import SourceSlices

        return SourceSlices(self._store).preview_pattern(source_id, pattern, start_index, limit)

    def create_pattern_slices(self, source_id: str, pattern: str, start_index: int = 0,
                              limit: int | None = None) -> dict:
        from .source_slices import SourceSlices
        return SourceSlices(self._store).create_pattern(source_id, pattern, start_index, limit)

    def source_slice(self, source_id: str, slice_id: str) -> dict:
        from .source_slices import SourceSlices

        return SourceSlices(self._store).get(source_id, slice_id)

    def preview_event_merge(self, source_event_id: str, target_event_id: str, expected_revision: int,
                            evidence: list[dict], reason: str) -> dict:
        from .event_merge import preview

        return preview(self, source_event_id, target_event_id, expected_revision, evidence, reason)

    def merge_events(self, source_event_id: str, target_event_id: str, expected_revision: int,
                     evidence: list[dict], reason: str) -> dict:
        from .event_merge import merge

        return merge(self, source_event_id, target_event_id, expected_revision, evidence, reason)

    def content_submission_contract(self):
        from .content_operations import catalog_view
        return catalog_view()

    def list_content_submissions(self, operation=None, target=None, limit=20, after=0):
        from .business_staging import list_pending
        return list_pending(self, operation, target, limit, after)

    def read_content_submission(self, submission_id, paths=None):
        from .business_staging import read
        return read(self, submission_id, paths)

    def preview_content_patch(self, submission_id, expected_revision, patches):
        from .business_staging import preview_patch
        return preview_patch(self, submission_id, expected_revision, patches)

    def patch_content_submission(self, submission_id, expected_revision, patches, reason):
        from .business_staging import patch
        return patch(self, submission_id, expected_revision, patches, reason)

    def list_extraction_submissions(self, source_id=None, limit=20, after=0):
        from .extraction_staging import list_pending
        return list_pending(self, source_id, limit=limit, after=after)

    def read_extraction_submission(self, submission_id, section=None, indices=None):
        from .extraction_staging import read
        return read(self, submission_id, section=section, indices=indices)

    def patch_extraction_submission(self, submission_id, expected_revision, patches, reason):
        from .extraction_staging import patch
        return patch(self, submission_id, expected_revision, patches, reason)

    def preview_extraction(self, source_id: str, result: dict, start: int = 0,
                           end: int | None = None) -> dict:
        from .extraction_draft import preview

        return preview(self, source_id, result, start, end)

    def submit_extraction(self, source_id: str, result: dict, start: int = 0,
                          end: int | None = None) -> dict:
        from .extraction_draft import submit

        return submit(self, source_id, result, start, end)




    def sources(self) -> list[dict]:
        return self.project.sources()

    def source(self, source_id: str) -> dict:
        return self.project.source(source_id)

    def locate(self, source_id: str, quotes: list[str], *, limit_per_quote: int = 20) -> list[dict]:
        return self.project.locate(source_id, quotes, limit_per_quote=limit_per_quote)

    def set_graph_title(self, title: str) -> None:
        self.profiles.set_title(title)

    # -- GraphRAG sidecar ------------------------------------------------
    def graphrag_preview(self, export_path: str | Path, source_map: dict[str, str]) -> dict:
        return self.graphrag.preview(export_path, source_map)

    def graphrag_stage_sources(self, run_root: str | Path, source_ids: list[str]) -> dict:
        return self.graphrag.stage_sources(run_root, source_ids)

    def graphrag_preview_staged(self, run_root: str | Path, export_path: str | Path) -> dict:
        return self.graphrag.preview_staged(run_root, export_path)

    # -- graph review ----------------------------------------------------
    def graph_schema(self) -> dict:
        return self.project.schema()

    def graph_view(self, spec: dict) -> dict:
        return self.query.view(spec)

    def graph_snapshot(self) -> dict:
        return self._store.snapshot()

    def graph_precheck(self, objects: list[dict], expected_revision: int) -> list[dict]:
        return self.prepare_changes(objects, [], expected_revision, snapshot=self.capture_changes()).diagnostics




    def graph_display_map(
        self
    ) -> dict:
        return self.query.display_map(

        )



    # -- story-time projections ------------------------------------------
    def graph_timeline(
        self,
        context_id: str = "canon",
        story_cut: dict | None = None,
        disclosure_cut: dict | None = None,
        limit: int = 2000,
    ) -> dict:
        return self.query.timeline_view(
            context_id=context_id, story_cut=story_cut,
            disclosure_cut=disclosure_cut, limit=limit,
        )

    def graph_event_compare(
        self,
        left_id: str,
        right_id: str,
        context_id: str = "canon",
        disclosure_cut: dict | None = None,
        axis: str = "disclosure",
    ) -> dict:
        return self.query.event_compare(
            left_id, right_id, context_id=context_id,
            disclosure_cut=disclosure_cut, axis=axis,
        )

    def graph_event_lines(
        self,
        context_id: str = "canon",
        disclosure_cut: dict | None = None,
    ) -> dict:
        return self.query.event_lines(
            context_id=context_id, disclosure_cut=disclosure_cut,

        )

    def graph_query(
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
        return self.query.query(
            node_id=node_id, query=query, node_ids=node_ids,
            kinds=kinds, context_id=context_id,
            disclosure_cut=disclosure_cut,
            limit=limit,
        )

    def property_precheck(self, records: list[dict], expected_revision: int) -> list[dict]:
        return self.prepare_changes([], records, expected_revision, snapshot=self.capture_changes()).diagnostics



    def property_records(self) -> list[dict]:
        return self.properties.records()




    def property_state(
        self,
        node_id: str,
        property_id: str,
        at=None,
        *,
        scope: str = "world",
        holder_id: str | None = None,
        disclosure_cut: dict | None = None,
        include_diagnostics: bool = False,
    ) -> dict:
        return self.properties.state(
            node_id, property_id, at,
            scope=scope, holder_id=holder_id, disclosure_cut=disclosure_cut,
            include_diagnostics=include_diagnostics,
        )

    def property_event_state(self, node_id, property_id, event_id, *, scope='world', holder_id=None):
        return self.property_state(node_id, property_id, at=event_id, scope=scope, holder_id=holder_id)

    def property_disclosure_state(
        self,
        node_id: str,
        property_id: str,
        disclosure_cut: dict,
        *,
        scope: str = "world",
        holder_id: str | None = None,
    ) -> dict:
        return self.properties.disclosure_state(
            node_id, property_id, disclosure_cut, scope=scope, holder_id=holder_id
        )

    def property_audit(self, node_ids: set[str] | None = None) -> dict:
        """Read-only audit: overlaps, contradictions, stale values, gaps."""
        return self.properties.audit(node_ids)

    def browse_view(self, **spec) -> dict:
        """One answer for the browsing page: nodes, counts, steps and the cut.

        Filtering, counting, the step list and the related-node rule all live in
        the projection, so the GUI only renders what it is given.
        """
        from .browse_view import browse_view

        # 记录要一起给：相关节点靠它算出「这一场里谁拿着钥匙、谁和谁是仇敌」。
        spec.setdefault("records", self.property_records())
        return browse_view(self.graph_snapshot(), **spec)

    @property
    def path(self) -> Path:
        """工程文件路径。

        资料卡这种「放在图旁边」的数据不在库里，按工程文件定位；CLI、MCP 和界面
        都从这里拿，免得各自去猜目录。
        """
        return self._store.path

    def property_node_summary(self, node_id: str, cut: object = None) -> dict:
        """Read-only: every property of one node, with its disclosure sequence.

        ``cut`` is an event id: the projection then answers "what was known when
        the reader reached that event" instead of "what is known now".

        失效的记录不出现——撤回不删行，但它不再是这个节点的一个说法。

        大事件多一行「大事件进展」：这句话不是它的记录，是它下面最后一个读过的
        场景说的（记录挂在场景上）。所以 `derived` 标出来，`from_scene` 说明出处——
        读者停在哪，这一行就说到哪。
        """
        summary = self.properties.node_properties(node_id, cut=cut, include_derived=True)
        names = {
            node["id"]: node.get("name") or node["id"]
            for node in self.graph_snapshot().get("nodes", [])
        }
        for item in summary.get("properties", []):
            # 只看最后一条：模型有时把同一句话直接写在大事件上，那一条才是当下值，
            # 这时把它标成「借来的」就是撒谎。
            records = item.get("records") or []
            derived = records[-1] if records and records[-1].get("derived") else None
            if derived is None:
                continue
            item["derived"] = True
            item["from_scene"] = {
                "id": derived.get("derived_from") or "",
                "name": names.get(derived.get("derived_from") or "", ""),
            }
        return summary

    def property_categories(self) -> dict[str, list[str]]:
        """Display categories per node type; loose groupings, editable at will."""
        return self.properties.categories()

    def set_property_categories(self, config: object) -> dict:
        """Replace the vocabulary; renames migrate the records they renamed."""
        from .content_errors import content_validation
        with content_validation():
            return self.properties.set_categories(config)

    def property_categorize(
        self, targets: list[str], category: str, note: str = ""
    ) -> dict:
        """Move disclosures to a category. Appends a record; never rewrites one."""
        from .content_errors import content_validation
        with content_validation():
            return self.properties.categorize(targets, category, note)






    def node_editor(self, node_id: str, cut: object = None) -> dict:
        """Current derived states and editable event-change history at a reading cut.

        Corrections edit a named mistaken record and preserve its sequence. New
        narrative changes append at an explicitly selected event/action; a cut
        never silently chooses a different writing event.
        """
        from .browse_view import _reading_key, steps

        snapshot = self.graph_snapshot()
        node = next(
            (item for item in snapshot.get("nodes", []) if item["id"] == node_id), None
        )
        if node is None:
            raise ValueError("节点不存在")
        summary = self.properties.node_properties(node_id, cut=cut)
        anchor_nodes = {item["id"]: item for item in snapshot["nodes"]}
        properties = summary["properties"]
        names = {item["id"]: item.get("name") or item["id"] for item in snapshot["nodes"]}
        return {
            "node_id": node_id,
            "node": node,
            "properties": properties,
            "events": steps(snapshot, "event"),
            "anchor_steps": sorted(steps(snapshot, "event") + steps(snapshot, "action"),
                key=lambda row: _reading_key(snapshot)(anchor_nodes[row["id"]])),
            "revision": summary["revision"],
            "cut": summary.get("cut"),
            "cut_name": names.get(str(summary.get("cut") or ""), ""),
            "later_count": summary.get("later_count", 0),
            # The reading cut filters derived state; writes always name their event.
            "at_cut": bool(cut),
        }

    def property_history(
        self,
        node_id: str,
        property_id: str,
        *,
        scope: str = "world",
        holder_id: str | None = None,
    ) -> list[dict]:
        return self.properties.history(
            node_id, property_id, scope=scope, holder_id=holder_id
        )

    def property_world_state(self, at=None) -> dict:
        return self.properties.world_state(at, )

    def get_schema(self) -> dict:
        from .project_changes import schema

        return schema(self)

    def read_project_state(self) -> dict:
        from .project_changes import state

        return state(self)

    def preview_changes(self, graph_objects: list[dict], property_records: list[dict], expected_revision: int) -> dict:
        from .project_changes import preview

        return preview(self, graph_objects, property_records, expected_revision)

    def apply_changes(self, graph_objects: list[dict], property_records: list[dict],
                      expected_revision: int, reason: str) -> dict:
        from .project_changes import apply

        return apply(self, graph_objects, property_records, expected_revision, reason)

    # -- static batch preview --------------------------------------------
    def change_precheck(
        self,
        graph_objects: list[dict] | None,
        property_records: list[dict] | None,
        expected_revision: int,
    ) -> list[dict]:
        return self.prepare_changes(graph_objects or [], property_records or [], expected_revision,
                                    snapshot=self.capture_changes()).diagnostics







    # -- model profiles --------------------------------------------------
    def harness_settings(self, provider: str | None = None) -> dict:
        return self.profiles.settings(provider)

    def load_project_settings(self) -> dict:
        from .project_settings import load
        return load(self)

    def save_project_settings(self, settings: dict, api_key=None, clear_api_key=False) -> dict:
        from .project_settings import save
        return save(self, settings, api_key=api_key, clear_api_key=clear_api_key)

    def provider_profile(self) -> dict:
        return self.profiles.settings()

    def provider_catalog(self) -> list[dict]:
        return self.profiles.catalog()

    def save_harness_settings(self, settings: dict) -> dict:
        return self.profiles.save(settings)

    def save_provider_profile(self, settings: dict, api_key=None, clear_api_key=False) -> dict:
        return self.profiles.save(settings, api_key=api_key, clear_api_key=clear_api_key)

    def provider_api_key_status(self, profile=None):
        return self._profile.credential_status(profile)

    def _saved_provider_api_key(self, profile):
        return self._profile.saved_api_key(profile)

    def check_model_credential(self, profile=None, api_key=None, ignore_saved_key=False):
        from .credential_check import check_model_credential
        return check_model_credential(self, profile if profile is not None else self.provider_profile(),
                                      supplied_key=api_key, ignore_saved_key=ignore_saved_key)



install_content_operations(GraphOperations)
