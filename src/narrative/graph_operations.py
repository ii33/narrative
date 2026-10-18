"""Public v4 project boundary for UI, CLI and model agents."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .graph_service import GraphService
from .graph_store import GraphStore
from .harness import HarnessSettings


@dataclass(frozen=True)
class _ProjectPath:
    path: str


class GraphOperations:
    def __init__(self, path: str | Path, *, create: bool = False):
        self._store = GraphStore(path, create=create)
        self._service = GraphService(self._store)

    def graph_project_info(self) -> dict:
        return self._store.info()

    def project_info(self) -> _ProjectPath:
        return _ProjectPath(str(self._store.path))

    def run_dir(self) -> Path:
        return self._store.path.with_name(self._store.path.stem + "_analysis_runs")

    def import_source(self, title: str, text: str) -> str:
        return self._service.import_source(title, text)

    def sources(self) -> list[dict]:
        return self._store.sources()

    def source(self, source_id: str) -> dict:
        return self._store.source(source_id)

    def locate(self, source_id: str, quote: str) -> list[dict]:
        return self._service.locate(source_id, quote)

    def graph_schema(self) -> dict:
        from .graph_views import QUERY_SCHEMA_VERSION, GraphViewSpec

        return self._service.schema() | {
            "query_schema_version": QUERY_SCHEMA_VERSION,
            "query_schema": GraphViewSpec.model_json_schema(),
            "query_examples": [{"view": "graph", "focus_id": "event-id", "depth": 2,
                                "direction": "both", "node_types": ["person", "event"]},
                               {"view": "table", "group_by": "node_type", "limit": 200}],
        }

    def graph_view(self, spec: dict) -> dict:
        """Execute a validated structural query against one reviewed snapshot."""
        from .graph_hierarchy import preview_snapshot
        from .graph_views import graph_view, normalize_spec

        query = normalize_spec(spec)
        snapshot = self._store.snapshot(query["as_of_revision"])
        if query["candidate_id"] is not None:
            candidate = self.graph_candidate(query["candidate_id"])
            if candidate["status"] == "accepted":
                if query["as_of_revision"] is not None and query["as_of_revision"] != candidate["result_revision"]:
                    raise ValueError("已接受候选与查询修订不一致")
                snapshot = self._store.snapshot(candidate["result_revision"])
            elif candidate["status"] == "pending":
                if snapshot["revision"] != self._store.revision:
                    raise ValueError("待审候选仅可与当前正式版本合并预览")
                diagnostics = self.graph_precheck(candidate["objects"], candidate["expected_revision"])
                if any(item["severity"] == "error" for item in diagnostics):
                    raise ValueError(f"候选预检未通过：{diagnostics}")
                snapshot = preview_snapshot(snapshot, candidate)
            else:
                raise ValueError("已拒绝候选不可作为结构查询预览")
        result = graph_view(snapshot, query)
        result["pending_excluded"] = query["candidate_id"] is None
        result["candidate_id"] = query["candidate_id"]
        return result

    def graph_snapshot(self, as_of_revision: int | None = None) -> dict:
        return self._store.snapshot(as_of_revision)

    def graph_precheck(self, objects: list[dict], expected_revision: int) -> list[dict]:
        return self._service.precheck(objects, expected_revision)

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
        return self._service.propose(
            objects, expected_revision, source=source, note=note,
            run_id=run_id, parent_candidate_id=parent_candidate_id,
        )

    def graph_candidates(self, status: str | None = None) -> list[dict]:
        return self._service.candidates(status)

    def graph_candidate(self, candidate_id: str) -> dict:
        return self._service.candidate(candidate_id)

    def graph_display_map(self, candidate_id: str | None = None, as_of_revision: int | None = None) -> dict:
        from .graph_display import display_map

        candidate = self.graph_candidate(candidate_id) if candidate_id else None
        return display_map(self._store.snapshot(as_of_revision), candidate)

    def graph_candidate_preview(self, candidate_id: str) -> dict:
        from .graph_display import candidate_preview

        candidate = self.graph_candidate(candidate_id)
        preview = candidate_preview(self._store.snapshot(), candidate)
        try:
            tree = self.graph_hierarchy(candidate_id=candidate_id)
        except ValueError as exc:
            tree = {
                "revision": self._store.revision, "candidate_id": candidate_id,
                "pending_excluded": True, "roots": [], "nodes": [], "edges": [],
                "truncated": False, "uncertainties": [],
                "status": "unavailable",
                "diagnostics": [{"code": "HIERARCHY_UNAVAILABLE", "severity": "error", "message": str(exc)}],
            }
        return preview | {"hierarchy": tree}

    def graph_hierarchy(
        self,
        candidate_id: str | None = None,
        context_id: str = "canon",
        story_cut: dict | None = None,
        disclosure_cut: dict | None = None,
        as_of_revision: int | None = None,
        limit: int = 2000,
        focus_id: str | None = None,
    ) -> dict:
        from .graph_hierarchy import hierarchy, preview_snapshot

        snapshot = self._store.snapshot(as_of_revision)
        if candidate_id is not None:
            candidate = self.graph_candidate(candidate_id)
            if candidate["status"] == "accepted":
                snapshot = self._store.snapshot(candidate["result_revision"])
            elif candidate["status"] == "pending":
                if snapshot["revision"] != self._store.revision:
                    raise ValueError("待审候选仅可与当前正式版本合并预览")
                diagnostics = self.graph_precheck(candidate["objects"], candidate["expected_revision"])
                if any(item["severity"] == "error" for item in diagnostics):
                    raise ValueError(f"候选预检未通过：{diagnostics}")
                snapshot = preview_snapshot(snapshot, candidate)
            else:
                raise ValueError("已拒绝候选不可作为层级预览")
        return hierarchy(snapshot, candidate_id=candidate_id, context_id=context_id,
                         story_cut=story_cut, disclosure_cut=disclosure_cut, limit=limit, focus_id=focus_id)

    def graph_event_index(
        self, context_id: str = "canon", disclosure_cut: dict | None = None,
        as_of_revision: int | None = None, focus_id: str | None = None,
        limit: int = 2000,
    ) -> dict:
        """Compact reviewed identities and organization for incremental extraction."""
        tree = self.graph_hierarchy(context_id=context_id, disclosure_cut=disclosure_cut,
                                    as_of_revision=as_of_revision, focus_id=focus_id, limit=limit)
        edges = {edge["id"]: edge for edge in tree["edges"]}
        visible = self.graph_query(context_id=context_id, disclosure_cut=disclosure_cut,
                                   as_of_revision=as_of_revision, limit=None)
        snapshot = self.graph_snapshot(as_of_revision)
        events = []
        for node in tree["nodes"]:
            if node["node_type"] != "event":
                continue
            edge = edges.get(node["parent_edge_id"], {})
            history = [{key: a.get(key) for key in ("id", "subject_id", "supersedes_id", "disclosure", "assessment", "derived_status", "organization_status")}
                       for a in visible["assertions"]
                       if a["semantic"] == "containment" and a.get("object_id") == node["id"]]
            replaced = {a["supersedes_id"] for a in history if a.get("supersedes_id")}
            tips = [a["id"] for a in history if a["id"] not in replaced]
            events.append({key: node[key] for key in
                           ("id", "name", "parent_id", "parent_edge_id", "child_ids", "depth", "root_id")}
                          | {"relative_depth": node.get("relative_depth"),
                             "temporal_links": [
                                 {key: a.get(key) for key in ("id", "subject_id", "object_id", "temporal_relation", "temporal_left", "temporal_right", "scope", "polarity", "modality", "assessment", "derived_status")}
                                 | {"assertion_id": a["id"], "direction": "out" if a["subject_id"] == node["id"] else "in"}
                                 for a in visible["assertions"] if a["semantic"] == "temporal"
                                 and node["id"] in {a["subject_id"], a.get("object_id")}],
                             "active_parent_edge_id": node["parent_edge_id"],
                             "chain_tip_id": tips[0] if len(tips) == 1 else None,
                             "history": history,
                             "disclosure": edge.get("disclosure"), "origin": edge.get("origin"),
                             "derived_from": edge.get("derived_from", [])})
        return {"revision": tree["revision"], "context_id": context_id,
                "disclosure_cut": disclosure_cut, "focus_id": focus_id,
                "events": events, "truncated": tree["truncated"], "pending_excluded": True,
                "roots": tree["roots"],
                "containment_predicates": [{key: p[key] for key in ("id", "subject_types", "object_types")}
                                           for p in snapshot["predicates"] if p["semantic"] == "containment"],
                "source_orders": [r for r in snapshot["source_orders"]
                                  if r["context_id"] in visible["context_lineage"]],
                "uncertainties": tree["uncertainties"]}

    def graph_timeline(
        self,
        candidate_id: str | None = None,
        context_id: str = "canon",
        story_cut: dict | None = None,
        disclosure_cut: dict | None = None,
        as_of_revision: int | None = None,
        limit: int = 2000,
    ) -> dict:
        from .graph_hierarchy import preview_snapshot
        from .graph_timeline import timeline

        snapshot = self._store.snapshot(as_of_revision)
        if candidate_id is not None:
            candidate = self.graph_candidate(candidate_id)
            if candidate["status"] == "accepted":
                snapshot = self._store.snapshot(candidate["result_revision"])
            elif candidate["status"] == "pending":
                if snapshot["revision"] != self._store.revision:
                    raise ValueError("待审候选仅可与当前正式版本合并预览")
                diagnostics = self.graph_precheck(candidate["objects"], candidate["expected_revision"])
                if any(item["severity"] == "error" for item in diagnostics):
                    raise ValueError(f"候选预检未通过：{diagnostics}")
                snapshot = preview_snapshot(snapshot, candidate)
            else:
                raise ValueError("已拒绝候选不可作为层级预览")
        return timeline(snapshot, candidate_id=candidate_id, context_id=context_id,
                         story_cut=story_cut, disclosure_cut=disclosure_cut, limit=limit)

    def graph_edit_candidate(self, candidate_id: str, objects: list[dict], version: int) -> dict:
        return self._service.edit_candidate(candidate_id, objects, version)

    def graph_select_operations(self, candidate_id: str, keep_ids: list[str], version: int) -> dict:
        return self._service.select_operations(candidate_id, keep_ids, version)

    def graph_accept(self, candidate_id: str, version: int) -> int:
        return self._service.accept(candidate_id, version)

    def graph_reject(self, candidate_id: str, version: int) -> None:
        self._service.reject(candidate_id, version)

    def graph_query(
        self,
        *,
        node_id: str | None = None,
        query: str = "",
        node_ids: list[str] | None = None,
        predicate_ids: list[str] | None = None,
        kinds: list[str] | None = None,
        context_id: str = "canon",
        story_cut: dict | None = None,
        disclosure_cut: dict | None = None,
        as_of_revision: int | None = None,
        limit: int | None = 200,
    ) -> dict:
        from .graph_query import query_graph

        return query_graph(
            self._store.snapshot(as_of_revision), node_id=node_id, query=query,
            node_ids=node_ids, predicate_ids=predicate_ids, kinds=kinds,
            context_id=context_id, story_cut=story_cut, disclosure_cut=disclosure_cut,
            limit=limit,
        )

    def graph_neighborhood(
        self,
        center_id: str,
        *,
        depth: int = 2,
        node_types: list[str] | None = None,
        context_id: str = "canon",
        story_cut: dict | None = None,
        disclosure_cut: dict | None = None,
        as_of_revision: int | None = None,
        limit: int = 2000,
    ) -> dict:
        from .graph_query import neighborhood

        return neighborhood(
            self._store.snapshot(as_of_revision), center_id=center_id,
            depth=depth, node_types=node_types, context_id=context_id,
            story_cut=story_cut, disclosure_cut=disclosure_cut, limit=limit,
        )

    def harness_settings(self, provider: str | None = None) -> dict:
        from . import providers

        with self._store.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='harness_settings'").fetchone()
            saved_profiles = db.execute("SELECT value FROM meta WHERE key='harness_profiles'").fetchone()
        active = HarnessSettings.model_validate_json(row[0]).model_dump() if row else HarnessSettings().model_dump()
        if provider is None or provider == active["provider"]:
            return active
        profiles = json.loads(saved_profiles[0]) if saved_profiles else {}
        if provider in profiles:
            return HarnessSettings.model_validate(profiles[provider]).model_dump()
        if not providers.preset(provider)["model"]:
            # An unconfigured provider can be displayed before an exact model ID
            # is entered. Saving/starting still goes through strict validation.
            return HarnessSettings(provider=provider, model="__unconfigured__").model_dump() | {"model": ""}
        return HarnessSettings(provider=provider).model_dump()

    def provider_profile(self) -> dict:
        return self.harness_settings()

    def provider_catalog(self) -> list[dict]:
        from . import providers

        return providers.catalog()

    def save_harness_settings(self, settings: dict) -> dict:
        config = HarnessSettings.model_validate(settings)
        with self._store.connect(write=True) as db:
            previous = db.execute("SELECT value FROM meta WHERE key='harness_settings'").fetchone()
            old = HarnessSettings.model_validate_json(previous[0]) if previous else HarnessSettings()
            profiles_row = db.execute("SELECT value FROM meta WHERE key='harness_profiles'").fetchone()
            profiles = json.loads(profiles_row[0]) if profiles_row else {}
            profiles.setdefault(old.provider, old.model_dump())
            profiles[config.provider] = config.model_dump()
            db.execute(
                "INSERT INTO meta(key,value) VALUES ('harness_profiles',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(profiles, ensure_ascii=False),),
            )
            db.execute(
                "INSERT INTO meta(key,value) VALUES ('harness_settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (config.model_dump_json(),),
            )
        return config.model_dump()

    def save_provider_profile(self, settings: dict) -> dict:
        return self.save_harness_settings(settings)

    def set_graph_title(self, title: str) -> None:
        if not title.strip():
            raise ValueError("项目标题不能为空")
        with self._store.connect(write=True) as db:
            db.execute("UPDATE meta SET value=? WHERE key='title'", (title.strip(),))

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
        from . import graph_agent

        return graph_agent.start(
            self, source_ids, task=task, feedback=feedback,
            feedback_origin=feedback_origin, seed_candidate_id=seed_candidate_id,
            budget=budget, api_key=api_key,
        )

    def graph_analysis_status(self, run_id: str) -> dict:
        from . import graph_agent

        return graph_agent.status(self, run_id)

    def graph_analysis_runs(self) -> list[dict]:
        """List persisted graph runs without exposing journals or raw model output."""
        root = self.run_dir()
        if not root.is_dir():
            return []
        rows = []
        for path in root.iterdir():
            if not path.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", path.name):
                continue
            try:
                state = self.graph_analysis_status(path.name)
            except (ValueError, OSError):
                continue
            rows.append({
                "run_id": path.name, "status": state.get("status"),
                "phase": state.get("phase"), "request_state": state.get("request_state"),
                "source_ids": state.get("source_ids", []), "candidate_ids": state.get("candidate_ids", []),
                "provider": state.get("provider"), "model": state.get("model"),
                "rounds": state.get("rounds", 0), "observer_state": state.get("observer_state"),
                "modified_at": path.stat().st_mtime,
            })
        return sorted(rows, key=lambda item: item["modified_at"], reverse=True)

    def graph_analysis_trace(self, run_id: str) -> dict:
        from . import graph_agent

        return graph_agent.trace(self, run_id)

    def graph_analysis_response(self, run_id: str, round_number: int) -> dict:
        from . import graph_agent

        return graph_agent.response(self, run_id, round_number)

    def graph_analysis_control(self, run_id: str) -> dict:
        from . import analysis_control

        return analysis_control.read(self, run_id)

    def take_graph_instructions(self, run_id: str) -> list[dict]:
        from . import analysis_control

        return analysis_control.take_instructions(self, run_id)

    def pause_graph_analysis(self, run_id: str) -> None:
        from . import analysis_control

        analysis_control.set_paused(self, run_id, True)

    def resume_graph_analysis(self, run_id: str) -> None:
        from . import analysis_control

        analysis_control.set_paused(self, run_id, False)

    def instruct_graph_analysis(self, run_id: str, text: str) -> str:
        from . import analysis_control

        return analysis_control.send_instruction(self, run_id, text)["id"]

    def cancel_graph_analysis(self, run_id: str) -> None:
        from . import analysis_control

        analysis_control.mark_cancel_requested(self, run_id)
