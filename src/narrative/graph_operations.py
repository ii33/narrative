"""Public v5 project boundary for UI, CLI and model agents.

This class is a composition root: every method
delegates to a narrow facade in :mod:`graph_facades`. New code should depend on a
facade (`ops.project`, `ops.review`, `ops.changes`, ...) instead of this class.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .change_service import ChangeService
from .graph_analysis_ops import AnalysisService
from .graph_facades import (
    GraphAnalysisFacade,
    GraphChangeFacade,
    GraphProfileFacade,
    GraphProjectFacade,
    GraphPropertyFacade,
    GraphQueryFacade,
    GraphRagFacade,
    GraphReviewFacade,
)
from .graph_profile_ops import ProfileService
from .graph_read_ops import GraphReadService
from .graph_service import GraphService
from .graph_store import GraphStore
from .graph_timeline_ops import TimelineService
from .graphrag_ops import GraphRagService
from .order_ids import order_key
from .property_store import PropertyStore
from .provider_store import ProviderStore


@dataclass(frozen=True)
class _ProjectPath:
    path: str


class GraphOperations:
    def __init__(self, path: str | Path, *, create: bool = False):
        self._store = GraphStore(path, create=create)
        self._service = GraphService(self._store)
        self._properties = PropertyStore(self._store)
        self._changes = ChangeService(self._service, self._properties)
        self._provider_store = ProviderStore()
        self._project_id = self._store.info()["project_id"]

        self._timeline = TimelineService(self._store, self._service, self._properties)
        self._read = GraphReadService(self._store, self._properties, self._service)
        self._analysis = AnalysisService(self, self._store)
        self._profile = ProfileService(self._store, self._project_id, self._provider_store)
        self._graphrag = GraphRagService(self._store, self._service)

        self.project = GraphProjectFacade(self._store, self._service, self._read)
        self.review = GraphReviewFacade(self._service)
        self.properties = GraphPropertyFacade(self._properties)
        self.changes = GraphChangeFacade(self._changes)
        self.query = GraphQueryFacade(self._read, self._timeline)
        self.profiles = GraphProfileFacade(self._profile)
        self.analysis = GraphAnalysisFacade(self._analysis)
        self.graphrag = GraphRagFacade(self._graphrag)

    # -- project ---------------------------------------------------------
    def graph_project_info(self) -> dict:
        return self.project.info()

    def project_info(self) -> _ProjectPath:
        return _ProjectPath(self.project.path())

    def run_dir(self) -> Path:
        return self.analysis.run_dir()

    def import_source(self, title: str, text: str) -> str:
        return self.project.import_source(title, text)

    def sources(self) -> list[dict]:
        return self.project.sources()

    def source(self, source_id: str) -> dict:
        return self.project.source(source_id)

    def locate(self, source_id: str, quote: str) -> list[dict]:
        return self.project.locate(source_id, quote)

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

    def graph_snapshot(self, as_of_revision: int | None = None) -> dict:
        return self._store.snapshot(as_of_revision)

    def graph_precheck(self, objects: list[dict], expected_revision: int) -> list[dict]:
        return self.review.precheck(objects, expected_revision)

    def graph_propose(
        self,
        objects: list[dict],
        expected_revision: int,
        *,
        source: str = "manual",
        note: str = "",
        run_id: str | None = None,
        parent_candidate_id: str | None = None,
    ) -> str:
        return self.review.propose(
            objects, expected_revision, source=source, note=note,
            run_id=run_id, parent_candidate_id=parent_candidate_id,
        )

    def graph_candidates(self, status: str | None = None) -> list[dict]:
        return self.review.candidates(status)

    def graph_candidate(self, candidate_id: str) -> dict:
        return self.review.candidate(candidate_id)

    def graph_display_map(
        self, candidate_id: str | None = None, as_of_revision: int | None = None
    ) -> dict:
        return self.query.display_map(
            candidate_id=candidate_id, as_of_revision=as_of_revision
        )

    def graph_candidate_preview(self, candidate_id: str) -> dict:
        return self.query.candidate_preview(candidate_id)

    def graph_edit_candidate(self, candidate_id: str, objects: list[dict], version: int) -> dict:
        return self.review.edit(candidate_id, objects, version)

    def graph_select_operations(
        self, candidate_id: str, keep_ids: list[str], version: int
    ) -> dict:
        return self.review.select_operations(candidate_id, keep_ids, version)

    def graph_accept(self, candidate_id: str, version: int) -> int:
        return self.review.accept(candidate_id, version)

    def graph_reject(self, candidate_id: str, version: int) -> None:
        self.review.reject(candidate_id, version)

    # -- story-time projections ------------------------------------------
    def graph_timeline(
        self,
        candidate_id: str | None = None,
        context_id: str = "canon",
        story_cut: dict | None = None,
        disclosure_cut: dict | None = None,
        as_of_revision: int | None = None,
        limit: int = 2000,
    ) -> dict:
        return self.query.timeline_view(
            candidate_id=candidate_id, context_id=context_id, story_cut=story_cut,
            disclosure_cut=disclosure_cut, as_of_revision=as_of_revision, limit=limit,
        )

    def graph_event_compare(
        self,
        left_id: str,
        right_id: str,
        context_id: str = "canon",
        disclosure_cut: dict | None = None,
        as_of_revision: int | None = None,
        axis: str = "disclosure",
    ) -> dict:
        return self.query.event_compare(
            left_id, right_id, context_id=context_id,
            disclosure_cut=disclosure_cut, as_of_revision=as_of_revision, axis=axis,
        )

    def graph_event_lines(
        self,
        context_id: str = "canon",
        disclosure_cut: dict | None = None,
        as_of_revision: int | None = None,
    ) -> dict:
        return self.query.event_lines(
            context_id=context_id, disclosure_cut=disclosure_cut,
            as_of_revision=as_of_revision,
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
        as_of_revision: int | None = None,
        limit: int | None = 200,
    ) -> dict:
        return self.query.query(
            node_id=node_id, query=query, node_ids=node_ids,
            kinds=kinds, context_id=context_id,
            disclosure_cut=disclosure_cut,
            as_of_revision=as_of_revision, limit=limit,
        )

    def property_precheck(self, records: list[dict], expected_revision: int) -> list[dict]:
        return self.properties.precheck(records, expected_revision)

    def property_propose(
        self,
        records: list[dict],
        expected_revision: int,
        source: str = "manual",
        note: str = "",
        *,
        run_id: str | None = None,
    ) -> str:
        return self.properties.propose(
            records, expected_revision, source=source, note=note, run_id=run_id
        )

    def property_candidates(self, status: str | None = None) -> list[dict]:
        return self.properties.candidates(status)

    def property_records(
        self, as_of_revision: int | None = None, *, include_retracted: bool = False
    ) -> list[dict]:
        """Accepted property records; retracted ones are hidden unless asked for."""
        return self.properties.records(
            as_of_revision, include_retracted=include_retracted
        )

    def property_candidate(self, candidate_id: str) -> dict:
        return self.properties.candidate(candidate_id)

    def property_accept(self, candidate_id: str, version: int) -> int:
        return self.properties.accept(candidate_id, version)

    def property_reject(self, candidate_id: str, version: int) -> None:
        self.properties.reject(candidate_id, version)

    def property_state(
        self,
        node_id: str,
        property_id: str,
        at=None,
        as_of_revision: int | None = None,
        *,
        scope: str = "world",
        holder_id: str | None = None,
        disclosure_cut: dict | None = None,
        include_diagnostics: bool = False,
    ) -> dict:
        return self.properties.state(
            node_id, property_id, at, as_of_revision=as_of_revision,
            scope=scope, holder_id=holder_id, disclosure_cut=disclosure_cut,
            include_diagnostics=include_diagnostics,
        )

    def property_event_state(
        self,
        node_id: str,
        property_id: str,
        event_id: str,
        *,
        scope: str = "world",
        holder_id: str | None = None,
        axis: str = "disclosure",
    ) -> dict:
        """Value of a property at an event, read from its value sequence.

        Each entry is (value, checkpoint). Which entries apply depends on the
        axis, and the two must not be confused:

        ``disclosure`` (default) — what the reader knows after reaching this
        event. The applicable entry is the last one disclosed at or before that
        point. The order is linear by construction, so no inference is needed.

        ``story`` — what holds at this story event. Only entries carrying an
        explicit ``applies_to`` link are eligible; the disclosure anchor is not
        reused here, because "the reader learns it here" is not "it holds there".
        Records without that link answer ``unlinked`` rather than guessing.
        """
        records = [
            item for item in self.property_records()
            if item["kind"] == "property_value"
            and item.get("node_id") == node_id
            and item.get("property_id") == property_id
            and item.get("scope", "world") == scope
            and (scope == "world" or item.get("holder_id") == holder_id)
        ]
        if not records:
            return {"kind": "unrecorded", "reason": "该属性没有值记录"}

        if axis == "story":
            # Only records explicitly linked to a story event can answer this. The
            # disclosure anchor is a reading position and must not stand in for it:
            # a value learned in a flashback is disclosed now, not then.
            ancestors = self._event_ancestors(event_id)
            linked = [
                item for item in records
                if isinstance(item.get("applies_to"), str)
                and item["applies_to"] in ancestors
            ]
            if not linked:
                return {
                    "kind": "unlinked",
                    "reason": "尚未建立属性与逻辑事件的适用关系，无法据此求故事状态",
                    "disclosure_available": True,
                }
            latest = max(linked, key=self._record_position)
            conflicting = [
                item for item in linked
                if item.get("value") != latest.get("value")
                and not self._record_position(item) < self._record_position(latest)
            ]
            if conflicting:
                return {
                    "kind": "divergent",
                    "reason": "多条分支各自记录了不同的值，未确定哪一条生效",
                    "values": sorted({str(item.get("value")) for item in linked}),
                    "record_ids": [item.get("id") for item in linked],
                }
            return {
                "kind": "known",
                "value": latest.get("value"),
                "event_id": latest.get("applies_to"),
                "record_id": latest.get("id"),
                "axis": "story",
            }

        cut = self._event_position(event_id)
        if cut is None:
            return {"kind": "unrecorded", "reason": "该事件没有披露位置，无法比较"}
        applicable = [
            item for item in records
            if self._record_position(item) is not None
            and self._record_position(item) <= cut
        ]
        if not applicable:
            return {"kind": "unrecorded", "reason": "读到该事件时这个属性还没有披露"}
        best = max(applicable, key=self._record_position)
        return {
            "kind": "known",
            "value": best.get("value"),
            "event_id": best.get("event_id"),
            "record_id": best.get("id"),
            "axis": "disclosure",
        }

    def _event_position(self, event_id: str) -> tuple[int, ...] | None:
        """Where an event sits on the disclosure chain: its own number."""
        return order_key(event_id)

    def _record_position(self, record: dict) -> tuple[int, ...]:
        """Where a value becomes visible: its anchor event's number."""
        anchor = record.get("event_id")
        key = order_key(anchor) if isinstance(anchor, str) else None
        return key or (999,)

    def _event_ancestors(self, event_id: str) -> set[str]:
        """The event itself plus everything reachable through predecessors."""
        nodes = {item["id"]: item for item in self.graph_snapshot()["nodes"]}
        reached = {event_id}
        stack = [event_id]
        while stack:
            current = stack.pop()
            node = nodes.get(current) or {}
            for parent in node.get("predecessors") or []:
                if parent not in reached:
                    reached.add(parent)
                    stack.append(parent)
        return reached

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

        return browse_view(self.graph_snapshot(), **spec)

    def property_node_summary(
        self, node_id: str, cut: object = None, *, include_invalid: bool = False
    ) -> dict:
        """Read-only: every property of one node, with its disclosure sequence.

        ``cut`` is an event id: the projection then answers "what was known when
        the reader reached that event" instead of "what is known now".
        ``include_invalid`` switches the answer from "the state at that point" to
        "every disclosure up to it" — the union of what was said over time, with
        each record marked valid or invalid.
        """
        return self.properties.node_properties(
            node_id, cut=cut, include_retracted=include_invalid
        )

    def property_categories(self) -> dict[str, list[str]]:
        """Display categories per node type; loose groupings, editable at will."""
        return self.properties.categories()

    def set_property_categories(self, config: object) -> dict:
        """Replace the vocabulary; renames migrate the records they renamed."""
        return self.properties.set_categories(config)

    def property_categorize(
        self, targets: list[str], category: str, note: str = ""
    ) -> dict:
        """Move disclosures to a category. Appends a record; never rewrites one."""
        return self.properties.categorize(targets, category, note)

    def property_merge(self, alias_id: str, target_id: str, note: str = "") -> dict:
        """Read one property name as another. A separate act from moving a category."""
        return self.properties.merge(alias_id, target_id, note)

    def property_rename(self, names: object, to_id: str, note: str = "") -> dict:
        """Give an aspect a new name.

        Editorial, not a disclosure: records keep the name they were captured
        under and only the reading changes, so retracting the rename restores it.
        ``names`` may be the whole row, when one aspect was spelled two ways.
        """
        return self.properties.rename(names, to_id, note)

    def property_aliases(self) -> dict[str, str]:
        """Alias → current name, so a caller can speak the names in use now."""
        return self.properties.aliases()

    def property_retract(
        self, targets: list[str], reason: str, *, event_id: str | None = None
    ) -> dict:
        """Invalidate records: they stay for audit, readers stop seeing them.

        Without ``event_id`` the record was misread and never counted; with it the
        record stops counting from that disclosure on (superseded).
        """
        return self.properties.retract(targets, reason, event_id=event_id)

    def property_restore(
        self, targets: list[str], reason: str, *, event_id: str | None = None
    ) -> dict:
        """Take a retraction back, counting again from this disclosure on.

        ``targets`` are retraction records. The restoration has its own position,
        so what was already read stays as it was read.
        """
        return self.properties.restore(targets, reason, event_id=event_id)

    def property_supersede(self, pairs: list[dict], note: str = "") -> dict:
        """Mark older values invalid from the disclosure that replaced them."""
        return self.properties.supersede(pairs, note)

    def node_editor(self, node_id: str, cut: object = None) -> dict:
        """Everything the node editor needs: the node, its properties, the events.

        The editor is about the present: only what still stands at the reading
        position is listed, and an aspect with no standing value is not listed at
        all — there is nothing left to correct, and a row that can only be looked
        at invites edits that go nowhere. Invalidated disclosures are not lost:
        the browsing pane's 「全部」 answers what was said over time and why a
        record stopped counting.

        With a reading cut the properties are what the reader had met by then.
        That view is a window, not a lock: an edit made while looking at the past
        is written at the disclosure the reader is standing on.
        """
        from .browse_view import steps

        snapshot = self.graph_snapshot()
        node = next(
            (item for item in snapshot.get("nodes", []) if item["id"] == node_id), None
        )
        if node is None:
            raise ValueError("节点不存在")
        summary = self.properties.node_properties(
            node_id, cut=cut, include_retracted=False
        )
        properties = [item for item in summary["properties"] if not item.get("retracted")]
        names = {item["id"]: item.get("name") or item["id"] for item in snapshot["nodes"]}
        return {
            "node_id": node_id,
            "node": node,
            "properties": properties,
            "events": steps(snapshot, "event"),
            "revision": summary["revision"],
            "cut": summary.get("cut"),
            "cut_name": names.get(str(summary.get("cut") or ""), ""),
            "later_count": summary.get("later_count", 0),
            # Not read-only: an edit made while looking at the past is written at
            # the disclosure the reader is standing on. The flag only says the
            # history is a window, so the pane can say so.
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

    def property_world_state(self, at=None, as_of_revision: int | None = None) -> dict:
        return self.properties.world_state(at, as_of_revision=as_of_revision)

    # -- unified change candidates ---------------------------------------
    def change_precheck(
        self,
        graph_objects: list[dict] | None,
        property_records: list[dict] | None,
        expected_revision: int,
    ) -> list[dict]:
        return self.changes.precheck(graph_objects, property_records, expected_revision)

    def change_propose(
        self,
        graph_objects: list[dict] | None,
        property_records: list[dict] | None,
        expected_revision: int,
        *,
        source: str = "manual",
        note: str = "",
        run_id: str | None = None,
    ) -> str:
        return self.changes.propose(
            graph_objects, property_records, expected_revision,
            source=source, note=note, run_id=run_id,
        )

    def change_candidates(self, status: str | None = None) -> list[dict]:
        return self.changes.candidates(status)

    def change_candidate(self, candidate_id: str) -> dict:
        return self.changes.candidate(candidate_id)

    def change_accept(self, candidate_id: str, version: int) -> int:
        return self.changes.accept(candidate_id, version)

    def change_reject(self, candidate_id: str, version: int) -> None:
        self.changes.reject(candidate_id, version)

    # -- model profiles --------------------------------------------------
    def harness_settings(self, provider: str | None = None) -> dict:
        return self.profiles.settings(provider)

    def provider_profile(self) -> dict:
        return self.profiles.settings()

    def provider_catalog(self) -> list[dict]:
        return self.profiles.catalog()

    def save_harness_settings(self, settings: dict) -> dict:
        return self.profiles.save(settings)

    def save_provider_profile(self, settings: dict) -> dict:
        return self.profiles.save(settings)

    # -- analysis runs ---------------------------------------------------
    def start_graph_analysis(
        self,
        source_ids: list[str],
        *,
        task: str | None = None,
        feedback: str | None = None,
        feedback_origin: str = "user_input",
        seed_candidate_id: str | None = None,
        budget: dict | None = None,
        api_key: str | None = None,
    ) -> str:
        return self.analysis.start(
            source_ids, task=task, feedback=feedback,
            feedback_origin=feedback_origin, seed_candidate_id=seed_candidate_id,
            budget=budget, api_key=api_key,
        )

    def graph_analysis_status(self, run_id: str) -> dict:
        return self.analysis.status(run_id)

    def graph_analysis_runs(self) -> list[dict]:
        return self.analysis.runs()

    def graph_analysis_trace(self, run_id: str) -> dict:
        return self.analysis.trace(run_id)

    def graph_analysis_response(self, run_id: str, round_number: int) -> dict:
        return self.analysis.response(run_id, round_number)

    def graph_analysis_control(self, run_id: str) -> dict:
        return self.analysis.control(run_id)

    def take_graph_instructions(self, run_id: str) -> list[dict]:
        return self.analysis.take_instructions(run_id)

    def pause_graph_analysis(self, run_id: str) -> None:
        self.analysis.pause(run_id)

    def resume_graph_analysis(self, run_id: str) -> None:
        self.analysis.resume(run_id)

    def instruct_graph_analysis(self, run_id: str, text: str) -> str:
        return self.analysis.instruct(run_id, text)

    def cancel_graph_analysis(self, run_id: str) -> None:
        self.analysis.cancel(run_id)
