"""UI-independent application operations shared by every entry point.

Adapters call this module, never storage or feature implementations directly.
Keep operations explicit Python methods; no dispatch bus or UI runtime is required.
"""

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Literal

from . import (
    analysis_control,
    analysis_display,
    analysis_query,
    analysis_trace,
    builtin_texts,
    extraction,
    jobs,
    production,
    providers,
    review,
    state,
    timeline,
    usage,
)
from .checks import check, digest
from .demo import seed
from .harness import HarnessSettings, credential_status
from .models import (
    Action,
    Assertion,
    ChangeSet,
    Diagnostic,
    Entity,
    Event,
    Evidence,
    Operation,
    uid,
)
from .service import Service
from .store import Store


class OperationError(ValueError):
    """An expected application failure an adapter can present to its user."""


def operation[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    @wraps(fn)
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return fn(*args, **kwargs)
        except OperationError:
            raise
        except (ValueError, OSError, sqlite3.Error) as exc:
            raise OperationError(str(exc)) from exc

    return call


@dataclass(frozen=True)
class ProjectInfo:
    path: str
    revision: int
    has_sources: bool


@dataclass(frozen=True)
class CandidateReview:
    change: ChangeSet
    version: int
    current_revision: int
    differences: list[dict]
    diagnostics: list[Diagnostic]


@dataclass(frozen=True)
class EvidenceExcerpt:
    title: str
    source_hash: str
    start: int
    end: int
    before: str
    quote: str
    after: str


@dataclass(frozen=True)
class Download:
    filename: str
    content: bytes


class Operations:
    @staticmethod
    @operation
    def create_graph_project(path: str | Path):
        """Create a separate v4 project; never reuse or migrate an existing file."""
        from .graph_operations import GraphOperations

        return GraphOperations(path, create=True)

    @staticmethod
    @operation
    def open_graph_project(path: str | Path):
        from .graph_operations import GraphOperations

        return GraphOperations(path)

    @staticmethod
    @operation
    def list_projects(directory: str | Path) -> list[dict]:
        """List known project files without opening/migrating or changing them."""
        root = Path(directory).resolve()
        if not root.is_dir():
            return []
        projects = []
        for path in root.glob("*.sqlite3"):
            if not path.is_file():
                continue
            try:
                with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1) as db:
                    version = int(db.execute("PRAGMA user_version").fetchone()[0])
                    title_row = db.execute("SELECT value FROM meta WHERE key='title'").fetchone()
                    revision_row = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
                title = title_row[0] if title_row else path.stem
                revision = int(revision_row[0]) if revision_row else None
                status = "ready" if version == 4 else "legacy_unsupported"
            except (sqlite3.Error, ValueError, OSError):
                version, title, revision, status = None, path.stem, None, "unreadable"
            projects.append({
                "name": path.stem, "path": str(path), "title": title,
                "version": version, "revision": revision, "status": status,
                "last_opened": None, "modified_at": path.stat().st_mtime,
            })
        return sorted(projects, key=lambda item: item["modified_at"], reverse=True)

    @operation
    def __init__(self, project_path: str | Path):
        self._store = Store(project_path)
        self._service = Service(self._store)
        jobs.recover(self._store)

    @staticmethod
    def _project_path(directory: str | Path, name: str) -> Path:
        if not re.fullmatch(r"[\w-]{1,64}", name):
            raise OperationError("项目名只能包含字母、数字、中文、下划线或短横线")
        return Path(directory).resolve() / f"{name}.sqlite3"

    @classmethod
    @operation
    def open_project(cls, directory: str | Path, name: str) -> "Operations":
        return cls(cls._project_path(directory, name))

    @operation
    def project_info(self) -> ProjectInfo:
        return ProjectInfo(str(self._store.path), self._store.revision, bool(self.sources()))

    @operation
    def sources(self) -> list[dict]:
        return self._store.rows("sources")

    @operation
    def source(self, source_id: str) -> dict:
        return self._store.source(source_id)

    @operation
    def entities(self) -> list[dict]:
        return self._store.objects("entities")

    @operation
    def assertions(self) -> list[Assertion]:
        return [Assertion.model_validate(obj) for obj in self._store.objects("assertions")]

    @operation
    def events(self) -> list[dict]:
        return self._store.objects("events")

    @operation
    def records(self, kind: str | None = None) -> list[dict]:
        return [
            record
            for record in self._store.objects("records")
            if kind is None or record["kind"] == kind
        ]

    @operation
    def project_snapshot(self) -> dict:
        with self._store.connect() as db:
            db.execute("BEGIN")
            meta = {row[0]: row[1] for row in db.execute("SELECT key,value FROM meta")}
            snapshot = {
                table: [json.loads(row[0]) for row in db.execute(f"SELECT data FROM {table}")]
                for table in ("entities", "assertions", "events", "records")
            }
            sources = [dict(row) for row in db.execute("SELECT * FROM sources")]
        return {
            **snapshot,
            "schema_version": 3,
            "project_id": meta["project_id"],
            "revision": int(meta["revision"]),
            "sources": sources,
        }

    @operation
    def analysis_context(self, source_ids: list[str] | None = None) -> dict:
        return analysis_query.analysis_context(self.project_snapshot(), source_ids)

    @operation
    def character_profile(self, entity_id: str, source_ids: list[str] | None = None) -> dict:
        return analysis_query.character_profile(self.project_snapshot(), entity_id, source_ids)

    @operation
    def event_profile(self, event_id: str) -> dict:
        return analysis_query.event_profile(self.project_snapshot(), event_id)

    @operation
    def search_analysis(
        self,
        query: str = "",
        *,
        source_ids: list[str] | None = None,
        entity_ids: list[str] | None = None,
        kinds: list[str] | None = None,
        narrative_types: list[str] | None = None,
        categories: list[str] | None = None,
        statuses: list[str] | None = None,
        storyline_ids: list[str] | None = None,
        limit: int = 100,
    ) -> dict:
        return analysis_query.search_analysis(
            self.project_snapshot(), query, source_ids=source_ids, entity_ids=entity_ids,
            kinds=kinds, narrative_types=narrative_types, categories=categories, statuses=statuses,
            storyline_ids=storyline_ids, limit=limit,
        )

    @operation
    def classification_table(self, source_ids: list[str] | None = None) -> dict:
        return analysis_query.classification_table(self.project_snapshot(), source_ids)

    @operation
    def analysis_object_schema(self, kinds: list[str] | None = None) -> dict:
        return analysis_query.object_schema(kinds)

    @operation
    def analysis_display_map(self, candidate_id: str | None = None) -> dict:
        snapshot = self.project_snapshot()
        candidate_rows = self.candidates()
        summaries = {}
        proposed = None
        for row in candidate_rows:
            change, _ = self._service.candidate(row["id"])
            objects = [operation.model_dump(mode="json") for operation in change.operations]
            summaries[row["id"]] = objects
            if row["id"] == candidate_id:
                proposed = objects
        if candidate_id is not None and proposed is None:
            raise ValueError("候选不存在")
        return analysis_display.build_display_map(
            snapshot,
            proposed=proposed,
            candidate_id=candidate_id,
            candidates=candidate_rows,
            runs=self.runs(),
            candidate_summaries=summaries,
        )

    @operation
    def propose_analysis_objects(
        self,
        objects: list[dict],
        expected_revision: int,
        run_id: str | None = None,
        note: str = "",
    ) -> str:
        change = ChangeSet.model_validate({
            "expected_revision": expected_revision,
            "operations": objects,
            "source": "model",
            "run_id": run_id,
            "note": note,
        })
        return self._service.propose(change)

    @operation
    def precheck_analysis_objects(self, objects: list[dict], expected_revision: int) -> list[dict]:
        change = ChangeSet.model_validate({
            "expected_revision": expected_revision,
            "operations": objects,
            "source": "model",
        })
        return [item.model_dump(mode="json") for item in self._service.diagnostics(change)]

    @operation
    def start_narrative_analysis(
        self,
        source_ids: list[str],
        budget: dict | None = None,
        api_key: str | None = None,
        task: str | None = None,
        feedback: str | None = None,
        seed_candidate_id: str | None = None,
        feedback_origin: Literal["user_input", "review_agent_guided"] = "user_input",
    ) -> str:
        from . import narrative_agent

        return narrative_agent.start(
            self,
            source_ids,
            budget=budget,
            api_key=api_key,
            task=task,
            feedback=feedback,
            seed_candidate_id=seed_candidate_id,
            feedback_origin=feedback_origin,
        )

    @operation
    def start_narrative_from_extraction(
        self,
        extraction_run_id: str,
        *,
        task: str | None = None,
        budget: dict | None = None,
        api_key: str | None = None,
    ) -> str:
        from . import narrative_agent

        handoff = self.narrative_extraction_handoff(extraction_run_id)
        return narrative_agent.start(
            self,
            [handoff["source_id"]],
            budget=budget,
            api_key=api_key,
            task=task,
            extraction_handoff=handoff,
        )

    @operation
    def narrative_analysis_status(self, run_id: str) -> dict:
        from . import narrative_agent

        return narrative_agent.status(self, run_id)

    @operation
    def narrative_analysis_control(self, run_id: str) -> dict:
        return analysis_control.read(self, run_id)

    @operation
    def pause_narrative_analysis(self, run_id: str) -> dict:
        if self.narrative_analysis_status(run_id)["status"] not in {"queued", "running"}:
            raise ValueError("分析任务已结束，不能暂停")
        return analysis_control.set_paused(self, run_id, True)

    @operation
    def resume_narrative_analysis(self, run_id: str) -> dict:
        if self.narrative_analysis_status(run_id)["status"] not in {"queued", "running"}:
            raise ValueError("分析任务已结束，不能继续")
        return analysis_control.set_paused(self, run_id, False)

    @operation
    def send_narrative_instruction(self, run_id: str, instruction: str) -> dict:
        if self.narrative_analysis_status(run_id)["status"] not in {"queued", "running"}:
            raise ValueError("分析任务已结束，不能补充指令")
        return analysis_control.send_instruction(self, run_id, instruction)

    @operation
    def take_narrative_instructions(self, run_id: str) -> list[dict]:
        return analysis_control.take_instructions(self, run_id)

    @operation
    def narrative_analysis_trace(self, run_id: str) -> dict:
        state = self.narrative_analysis_status(run_id)
        parent_id = state.get("lineage", {}).get("parent_candidate_id")
        candidate_ids = state.get("candidate_ids") or []
        current_id = candidate_ids[-1] if candidate_ids else None
        display = self.analysis_display_map(current_id)
        if parent_id:
            parent = self.analysis_display_map(parent_id)
            display["items"] = {**parent["items"], **display["items"]}
        result = analysis_trace.project(
            state,
            self.narrative_analysis_control(run_id),
            display,
        )
        current = self.harness_settings()
        result["current_service"] = {
            "provider": current["provider"],
            "model": current["model"],
            "token_parameter": current["token_parameter"],
            "max_tokens": current["max_tokens"],
            "output_parameter_sent": current["max_tokens"] is not None,
        }
        result["capabilities"] = self.narrative_analysis_capabilities(run_id)
        return result

    @operation
    def narrative_analysis_capabilities(self, run_id: str | None = None) -> dict:
        from . import narrative_agent

        state = self.narrative_analysis_status(run_id) if run_id else {}
        return narrative_agent.capabilities(bool(state.get("extraction_handoff")))

    @operation
    def narrative_analysis_response(self, run_id: str, round_number: int) -> dict:
        if type(round_number) is not int or round_number < 1:
            raise ValueError("模型请求轮次无效")
        state = self.narrative_analysis_status(run_id)
        step = next(
            (item for item in state.get("steps", []) if item.get("round") == round_number),
            None,
        )
        if step is None:
            raise ValueError("模型请求轮次不存在")
        return {
            "run_id": run_id,
            "round": round_number,
            "phase": step.get("phase"),
            "raw": step.get("raw"),
            "finish_reason": step.get("finish_reason"),
            "usage": step.get("usage"),
            "service_error": step.get("service_error"),
            "prompt_version": step.get("prompt_version", state.get("prompt_version")),
            "provider": state.get("provider"),
            "model": state.get("model"),
        }

    @operation
    def cancel_narrative_analysis(self, run_id: str) -> None:
        from . import narrative_agent

        state = self.narrative_analysis_status(run_id)
        if state["status"] not in {"queued", "running"}:
            raise ValueError("分析任务已结束，不能取消")
        analysis_control.mark_cancel_requested(self, run_id)
        if state.get("worker_local") is False:
            return
        try:
            narrative_agent.cancel(self, run_id)
        except ValueError as exc:
            if str(exc) != "此工作台进程没有该活动分析任务":
                raise

    @operation
    def propose_objects(
        self,
        objects: list[dict],
        *,
        expected_revision: int | None = None,
        source: Literal["manual", "model", "correction"] = "manual",
        note: str = "",
        assumptions: list[str] | None = None,
        allow_locked_changes: bool = False,
    ) -> str:
        change = ChangeSet.model_validate(
            {
                "expected_revision": self._store.revision
                if expected_revision is None
                else expected_revision,
                "operations": objects,
                "source": source,
                "note": note,
                "assumptions": assumptions or [],
                "allow_locked_changes": allow_locked_changes,
            }
        )
        return self._service.propose(change)

    @operation
    def model_schema(self) -> dict:
        return {
            "schema_version": 3,
            "change_set": ChangeSet.model_json_schema(),
            "scene_package": production.ScenePackage.model_json_schema(),
            "builtin_properties": {
                "location": "人物所在地→地点；延续到改变",
                "held_by": "物品持有者→人物；延续到改变",
                "outfit": "人物穿戴→物品；须指定延续策略",
                "injury:<slot>": "人物部位伤势→文字；须指定延续策略",
            },
        }

    @operation
    def record_templates(self) -> dict:
        entities = self.entities()
        ref = next((e["id"] for e in entities if e["entity_type"] == "person"), "请填写人物ID")
        assertion = next((a.id for a in self.assertions()), "请填写命题ID")
        return {
            "property": {
                "kind": "property",
                "name": "新属性",
                "value_type": "string",
                "persistence": "unknown",
                "origin": "creative",
            },
            "state": {
                "kind": "state",
                "subject_id": ref,
                "property_id": "injury:default",
                "value": None,
                "validity": {"start": 0},
                "persistence": "unknown",
            },
            "relation": {
                "kind": "relation",
                "subject_id": ref,
                "predicate": "关系名称",
                "object_id": ref,
                "certainty": "unknown",
                "validity": {},
            },
            "knowledge": {
                "kind": "knowledge",
                "holder_id": ref,
                "assertion_id": assertion,
                "attitude": "unknown",
                "acquisition": "unknown",
                "validity": {},
            },
            "rule": {
                "kind": "rule",
                "title": "规则名称",
                "rule_type": "require",
                "conditions": [],
                "closed_world": False,
                "validity": {},
                "origin": "creative",
            },
            "scene": {
                "kind": "scene",
                "title": "场景名称",
                "validity": {"start": 0, "end": 10},
                "entity_ids": [ref],
                "asset_requirements": [],
                "binding_ids": [],
                "origin": "production",
            },
            "asset_binding": {
                "kind": "asset_binding",
                "entity_id": ref,
                "role": "角色形象",
                "asset_uri": "请填写固定资产引用",
                "version": "1",
                "sha256": "请填写64位SHA-256",
                "origin": "production",
            },
            "intervention": {
                "kind": "intervention",
                "prompt": "外部激励",
                "entity_ids": [ref],
                "event_budget": 5,
                "model_call_budget": 2,
                "entity_budget": 0,
                "origin": "creative",
            },
            "creative_intent": {
                "kind": "creative_intent",
                "actor_id": ref,
                "goal": "人物目标",
                "origin": "creative",
            },
            "artifact_observation": {
                "kind": "artifact_observation",
                "scene_id": "请填写场景ID",
                "scene_revision": self._store.revision,
                "artifact_uri": "请填写产物引用",
                "artifact_sha256": "请填写64位SHA-256",
                "attributes": [],
                "origin": "production",
            },
            "artifact_acceptance": {
                "kind": "artifact_acceptance",
                "scene_id": "请填写场景ID",
                "scene_revision": self._store.revision,
                "observation_ids": ["请填写观察ID"],
                "decision": "needs_review",
                "origin": "production",
            },
        }

    @operation
    def validate_project(self) -> list[dict]:
        snapshot = self.project_snapshot()
        # Recheck an unchanged object without storing a candidate or revision.
        objects = [
            obj
            for table in ("entities", "assertions", "events", "records")
            for obj in snapshot[table]
        ]
        if not objects:
            return []
        change = ChangeSet.model_validate(
            {"expected_revision": snapshot["revision"], "operations": [objects[0]]}
        )
        return [
            diagnostic.model_dump()
            for diagnostic in check(
                change,
                snapshot["revision"],
                {v["id"]: v for v in snapshot["entities"]},
                {v["id"]: v for v in snapshot["sources"]},
                {v["id"]: v for v in snapshot["events"]},
                {v["id"]: v for v in snapshot["records"]},
                {v["id"]: v for v in snapshot["assertions"]},
            )
        ]

    @operation
    def scene_package(self, scene_id: str, *, draft: bool = True) -> dict:
        return production.build_package(self.project_snapshot(), scene_id, draft=draft).model_dump(
            mode="json"
        )

    @operation
    def timeline(self, order: str = "story") -> dict:
        if order not in {"story", "disclosure"}:
            raise OperationError("时间轴排列方式无效")
        snapshot = self.project_snapshot()
        chapters = {source["id"]: index for index, source in enumerate(snapshot["sources"])}
        events = [
            {
                **event,
                "chapter_order": chapters.get(event["evidence"][0]["source_id"], 0)
                if event["evidence"]
                else 0,
            }
            for event in snapshot["events"]
        ]
        view = timeline.build_timeline(events, snapshot["entities"], order)
        view["diagnostics"] = state.diagnose(snapshot)
        return view

    @operation
    def event_state(self, event_id: str) -> dict:
        result = self.full_event_state(event_id)
        result["applied"] = any(
            event_id in row.get("record_ids", []) for row in result["after"]["states"]
        )
        return result

    @operation
    def state_at_time(self, minute: float, boundary: str = "after") -> dict:
        return state.query(self.project_snapshot(), minute, boundary)

    @operation
    def full_event_state(self, event_id: str) -> dict:
        return state.query_event(self.project_snapshot(), event_id)

    @operation
    def event_evidence(self, event_id: str) -> list[Evidence]:
        event = next((event for event in self.events() if event["id"] == event_id), None)
        if event is None:
            raise OperationError("事件不存在")
        return [Evidence.model_validate(evidence) for evidence in event["evidence"]]

    @operation
    def propose_event(
        self,
        *,
        title: str,
        action: Action,
        evidence: Evidence,
        actor_id: str | None = None,
        target_id: str | None = None,
        item_id: str | None = None,
        location_id: str | None = None,
        time_kind: Literal["point", "interval", "relative", "unknown"] = "unknown",
        time_start: float | None = None,
        time_end: float | None = None,
        after_event_id: str | None = None,
        disclosure_position: int | None = None,
        event_id: str | None = None,
        assertion_id: str | None = None,
        slot: str = "default",
        state_value: str | bool | int | float | None = None,
        persistence: Literal["until_changed", "bounded", "instant", "unknown"] | None = None,
        valid_until: float | None = None,
    ) -> str:
        event = Event(
            id=event_id or uid(),
            title=title.strip(),
            action=action,
            actor_id=actor_id,
            target_id=target_id,
            item_id=item_id,
            location_id=location_id,
            time_kind=time_kind,
            time_start=time_start,
            time_end=time_end,
            after_event_id=after_event_id,
            disclosure_position=(
                disclosure_position if disclosure_position is not None else evidence.start
            ),
            evidence=[evidence],
            assertion_id=assertion_id,
            slot=slot,
            state_value=state_value,
            persistence=persistence,
            valid_until=valid_until,
        )
        return self._service.propose(
            ChangeSet(
                expected_revision=self._store.revision,
                operations=[event],
                source="correction" if event_id else "manual",
            )
        )

    @operation
    def propose_event_correction(self, event_id: str) -> str:
        saved = next((event for event in self.events() if event["id"] == event_id), None)
        if saved is None:
            raise OperationError("事件不存在")
        return self._service.propose(
            ChangeSet(
                expected_revision=self._store.revision,
                operations=[Event.model_validate(saved)],
                source="correction",
                note="修改事件时间或动作后重新审核；采用后时间轴和状态按新修订重算。",
            )
        )

    @operation
    def propose_timeline_demo(self) -> str:
        text = "后来，林舟走进仓库。\n此前，林舟把铜钥匙交给沈砚。\n沈砚随后将铜钥匙还给林舟。"
        source_id = self.import_text("时间轴演示 · 插叙与交接", text)
        scope = f"timeline-demo:{source_id}"
        for row in self._store.rows("changes"):
            change = ChangeSet.model_validate_json(row["data"])
            if change.affected_scope == scope:
                if row["status"] == "pending":
                    return change.id
                if row["status"] == "accepted":
                    raise OperationError("演示事件已采用，请在时间轴查看；可创建事件修正候选。")
        entities = self.entities()
        additions: list[Operation] = []
        refs = {}
        for name, kind in (
            ("林舟", "person"),
            ("沈砚", "person"),
            ("铜钥匙", "item"),
            ("仓库", "location"),
        ):
            saved = next(
                (
                    entity
                    for entity in entities
                    if entity["name"] == name and entity["entity_type"] == kind
                ),
                None,
            )
            if saved:
                refs[name] = saved["id"]
            else:
                entity = Entity.model_validate({"name": name, "entity_type": kind})
                refs[name] = entity.id
                additions.append(entity)
        for title, action, minute, quote, fields in (
            ("林舟进入仓库", "move", 30, "后来，林舟走进仓库。", {"location_id": refs["仓库"]}),
            (
                "钥匙交给沈砚",
                "transfer",
                10,
                "此前，林舟把铜钥匙交给沈砚。",
                {"item_id": refs["铜钥匙"], "target_id": refs["沈砚"]},
            ),
            (
                "钥匙交还林舟",
                "transfer",
                20,
                "沈砚随后将铜钥匙还给林舟。",
                {"item_id": refs["铜钥匙"], "target_id": refs["林舟"]},
            ),
        ):
            evidence = self.locate(source_id, quote)[0]
            additions.append(
                Event.model_validate(
                    {
                        "title": title,
                        "action": action,
                        "actor_id": refs["沈砚"] if minute == 20 else refs["林舟"],
                        "time_kind": "point",
                        "time_start": minute,
                        "disclosure_position": evidence.start,
                        "evidence": [evidence.model_dump()],
                        **fields,
                    }
                )
            )
        return self._service.propose(
            ChangeSet(
                expected_revision=self._store.revision,
                operations=additions,
                affected_scope=scope,
                note="时间轴演示：10、20、30 是演示用相对分钟，人工设定，不是从原文自动推定。审核后才进入时间轴。",
            )
        )

    @operation
    def candidates(self, status: str | None = None) -> list[dict]:
        return [
            {
                "id": row["id"],
                "status": row["status"],
                "version": row["version"],
                "result_revision": row["result_revision"],
                "source": change.source,
                "expected_revision": change.expected_revision,
            }
            for row in self._store.rows("changes")
            if status is None or row["status"] == status
            for change in [ChangeSet.model_validate_json(row["data"])]
        ]

    @operation
    def revisions(self) -> list[dict]:
        return [{**row, "data": json.loads(row["data"])} for row in self._store.rows("revisions")]

    @operation
    def status(self) -> dict:
        return {
            "revision": self._store.revision,
            "sources": [{k: s[k] for k in ("id", "title", "hash")} for s in self.sources()],
            "entities": self.entities(),
            "events": self.events(),
            "changes": self.candidates(),
        }

    @operation
    def import_text(self, title: str, text: str) -> str:
        return self._service.import_text(title, text)

    @operation
    def import_bytes(self, title: str, content: bytes) -> str:
        return self.import_text(title, content.decode("utf-8"))

    @operation
    def import_file(self, path: str | Path, title: str | None = None) -> str:
        source = Path(path)
        return self.import_bytes(title or source.stem, source.read_bytes())

    @operation
    def load_demo(self) -> str:
        return seed(self._service)

    @operation
    def builtin_text_catalog(self) -> list[dict]:
        return builtin_texts.catalog()

    @operation
    def builtin_text(self, text_id: str) -> dict:
        entry, text = builtin_texts.load(text_id)
        return {**entry, "text": text}

    @operation
    def import_builtin_text(self, text_id: str) -> str:
        entry, text = builtin_texts.load(text_id)
        return self._service.import_text(entry["source_title"], text, attribution=entry)

    @operation
    def source_attribution(self, source_id: str) -> dict:
        self._store.source(source_id)
        with self._store.connect() as db:
            row = db.execute(
                "SELECT value FROM meta WHERE key=?", (f"source_attribution:{source_id}",)
            ).fetchone()
        return json.loads(row[0]) if row else {}

    @operation
    def locate(self, source_id: str, quote: str) -> list[Evidence]:
        return self._service.locate(source_id, quote)

    @operation
    def evidence_excerpt(self, evidence: Evidence | dict, context: int = 160) -> EvidenceExcerpt:
        checked = Evidence.model_validate(evidence)
        source = self.source(checked.source_id)
        text, start, end = source["text"], checked.start, checked.end
        if (
            checked.source_hash != source["hash"]
            or digest(text) != source["hash"]
            or not 0 <= start < end <= len(text)
            or text[start:end] != checked.quote
        ):
            raise OperationError("此候选证据不匹配原文，无法显示为有效定位。请修正证据。")
        return EvidenceExcerpt(
            source["title"],
            source["hash"],
            start,
            end,
            text[max(0, start - context) : start],
            text[start:end],
            text[end : end + context],
        )

    @operation
    def propose_entity(
        self,
        name: str,
        entity_type: Literal["person", "location", "item", "other"],
        aliases: list[str],
        *,
        entity_id: str | None = None,
    ) -> str:
        entity = Entity(
            id=entity_id or uid(),
            name=name.strip(),
            entity_type=entity_type,
            aliases=[alias.strip() for alias in aliases if alias.strip()],
        )
        return self._service.propose(
            ChangeSet(
                expected_revision=self._store.revision,
                operations=[entity],
                source="correction" if entity_id else "manual",
            )
        )

    @operation
    def propose_assertion(
        self,
        *,
        subject_id: str,
        predicate: str,
        value: str | bool | int | float | None,
        scope: Literal["world", "claim", "knowledge"],
        holder_id: str | None,
        evidence: Evidence,
        learning_method: str | None = None,
        story_time: str | None = None,
    ) -> str:
        assertion = Assertion(
            subject_id=subject_id,
            predicate=predicate.strip(),
            value=value,
            scope=scope,
            holder_id=holder_id if scope != "world" else None,
            learning_method=learning_method.strip() or None if learning_method else None,
            story_time=story_time.strip() or None if story_time else None,
            disclosure_position=evidence.start,
            evidence=[evidence],
        )
        return self._service.propose(
            ChangeSet(expected_revision=self._store.revision, operations=[assertion])
        )

    @operation
    def propose_correction(self, assertion_id: str) -> str:
        assertion = next((a for a in self.assertions() if a.id == assertion_id), None)
        if assertion is None:
            raise OperationError("断言不存在")
        return self._service.propose(
            ChangeSet(
                expected_revision=self._store.revision,
                operations=[assertion],
                source="correction",
                note="请在候选审核中编辑后接受；原值保留在修订记录",
            )
        )

    @operation
    def propose_event_order(
        self, event_id: str, after_event_id: str, evidence: Evidence
    ) -> str:
        return self._service.propose_event_order(event_id, after_event_id, evidence)

    @operation
    def propose_state_window(
        self,
        state_id: str,
        *,
        start_event_id: str | None = None,
        end_event_id: str | None = None,
        evidence: Evidence,
    ) -> str:
        return self._service.propose_state_window(
            state_id,
            start_event_id=start_event_id,
            end_event_id=end_event_id,
            evidence=evidence,
        )

    @operation
    def review_candidate(self, change_id: str) -> CandidateReview:
        change, row = self._service.candidate(change_id)
        return CandidateReview(
            change,
            row["version"],
            self._store.revision,
            self._service.diff(change),
            self._service.diagnostics(change),
        )

    @operation
    def edit_candidate(self, change_id: str, content: str, expected_version: int) -> None:
        change = ChangeSet.model_validate_json(content)
        if change.id != change_id:
            raise OperationError("编辑不能改变候选 ID")
        self._service.edit(change, expected_version)

    @operation
    def candidate_preview(self, change_id: str) -> dict:
        candidate = self.review_candidate(change_id)
        return {
            **review.build_candidate_preview(
                self.project_snapshot(), candidate.change, candidate.diagnostics
            ),
            "version": candidate.version,
            "current_revision": candidate.current_revision,
        }

    @operation
    def select_candidate_operations(
        self, change_id: str, keep_ids: list[str], expected_version: int
    ) -> dict:
        return self._service.select_operations(change_id, keep_ids, expected_version)

    @operation
    def rebase_candidate(
        self, change_id: str, expected_version: int, reviewed_revision: int
    ) -> None:
        if reviewed_revision != self._store.revision:
            raise OperationError("正式资料再次改变，请查看最新差异再更新基准")
        change, _ = self._service.candidate(change_id)
        change.expected_revision = reviewed_revision
        self._service.edit(change, expected_version)

    @operation
    def accept_candidate(self, change_id: str, expected_version: int) -> int:
        return self._service.accept(change_id, expected_version)

    @operation
    def reject_candidate(self, change_id: str, expected_version: int) -> None:
        self._service.reject(change_id, expected_version)

    @operation
    def create_run(self, source_id: str, size: int = 6000, overlap: int = 300) -> str:
        return extraction.create_run(self._service, source_id, size, overlap)

    @operation
    def create_recovery_run(self, previous_run_id: str, size: int, overlap: int = 0) -> str:
        """Create a fresh run for the same source after a stopped model attempt."""
        from .jobs import ProjectLease

        lease = ProjectLease(Path(str(self._store.path) + ".dsh.lock"))
        try:
            with self._store.connect() as db:
                previous = db.execute(
                    "SELECT source_id,status FROM runs WHERE id=?", (previous_run_id,)
                ).fetchone()
                if previous is None:
                    raise ValueError("原任务不存在")
                if previous["status"] not in {"failed", "interrupted", "cancelled", "partial"}:
                    raise ValueError("原任务尚未失败或停止；不能创建恢复任务")
                largest = db.execute(
                    "SELECT MAX(end-start) FROM chunks WHERE run_id=?", (previous_run_id,)
                ).fetchone()[0]
            if not isinstance(size, int) or isinstance(size, bool) or size < 1:
                raise ValueError("分块大小必须为正整数")
            if not isinstance(overlap, int) or isinstance(overlap, bool) or not 0 <= overlap < size:
                raise ValueError("重叠范围必须小于分块大小")
            if largest is None or size >= largest:
                raise ValueError("新分块必须小于原任务的最大分块")
            return self.create_run(previous["source_id"], size=size, overlap=overlap)
        finally:
            lease.close()

    @operation
    def provider_catalog(self) -> list[dict]:
        return providers.catalog()

    @operation
    def harness_settings(self, provider: str | None = None) -> dict:
        with self._store.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='harness_settings'").fetchone()
            profiles = db.execute("SELECT value FROM meta WHERE key='harness_profiles'").fetchone()
        active = (
            HarnessSettings.model_validate_json(row[0]).model_dump()
            if row
            else HarnessSettings().model_dump()
        )
        if provider is None or provider == active["provider"]:
            return active
        saved = json.loads(profiles[0]) if profiles else {}
        if provider in saved:
            return HarnessSettings.model_validate(saved[provider]).model_dump()
        if not providers.preset(provider)["model"]:
            return HarnessSettings(provider=provider, model="__unconfigured__").model_dump() | {"model": ""}
        return HarnessSettings(provider=provider).model_dump()

    @operation
    def harness_credential_status(self) -> dict:
        env_name = self.harness_settings()["key_env"]
        return (
            credential_status() if env_name == "DEEPSEEK_API_KEY" else credential_status(env_name)
        )

    @operation
    def save_harness_settings(self, settings: dict) -> dict:
        config = HarnessSettings.model_validate(settings)
        with self._store.connect(write=True) as db:
            row = db.execute("SELECT value FROM meta WHERE key='harness_profiles'").fetchone()
            profiles = json.loads(row[0]) if row else {}
            previous = db.execute("SELECT value FROM meta WHERE key='harness_settings'").fetchone()
            old = (
                HarnessSettings.model_validate_json(previous[0]) if previous else HarnessSettings()
            )
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

    @operation
    def start_extraction(self, run_id: str, *, api_key: str | None = None) -> None:
        jobs.start(
            self._store, run_id, HarnessSettings.model_validate(self.harness_settings()), api_key
        )

    @operation
    def cancel_extraction(self, run_id: str) -> None:
        jobs.cancel(self._store, run_id)

    @operation
    def runs(self) -> list[dict]:
        return [{**row, "data": json.loads(row["data"])} for row in self._store.rows("runs")]

    @operation
    def chunks(self, run_id: str) -> list[dict]:
        return [row for row in self._store.rows("chunks") if row["run_id"] == run_id]

    @operation
    def extraction_run_diagnostics(self, run_id: str) -> dict:
        with self._store.connect() as db:
            if db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone() is None:
                raise ValueError("任务不存在")
            rows = db.execute(
                "SELECT metadata FROM chunks WHERE run_id=? ORDER BY rowid", (run_id,)
            ).fetchall()
        metadata = [json.loads(row["metadata"] or "{}") for row in rows]
        reasons = list(
            dict.fromkeys(
                reason
                for item in metadata
                if (reason := item.get("finish_reason")) and reason != "completed"
            )
        )
        return {
            "finish_reasons": reasons,
            "capacity_truncated": any(jobs.is_capacity_finish(reason) for reason in reasons),
            "manually_repaired": any(item.get("manual_corrections") for item in metadata),
        }

    @operation
    def narrative_extraction_handoff(self, run_id: str) -> dict:
        run = next((row for row in self.runs() if row["id"] == run_id), None)
        if run is None:
            raise ValueError("旧提取任务不存在")
        if run["status"] in {"queued", "running"}:
            raise ValueError("旧提取任务仍在运行，不能交接分析")
        source = self.source(run["source_id"])
        frozen = run["data"]
        if frozen.get("source_hash") != source["hash"]:
            raise ValueError("旧提取任务原文与当前项目不一致")
        rows = self.chunks(run_id)
        if not rows or not any(row.get("raw") or row.get("error") for row in rows):
            raise ValueError("旧提取任务没有可观察的原始结果或错误")
        chunks = []
        for row in rows:
            review = self.extraction_review(row["id"]) if row.get("raw") else None
            chunks.append({
                "id": row["id"],
                "start": row["start"],
                "end": row["end"],
                "status": row["status"],
                "raw": row.get("raw"),
                "error": row.get("error"),
                "metadata": json.loads(row.get("metadata") or "{}"),
                "diagnostics": review["diagnostics"] if review else [],
            })
        return {
            "run_id": run_id,
            "source_id": run["source_id"],
            "source_hash": frozen["source_hash"],
            "prompt_version": frozen.get("prompt_version"),
            "expected_revision": frozen.get("expected_revision"),
            "current_revision": self.project_info().revision,
            "model": frozen.get("model"),
            "chunks": chunks,
            "diagnostics": self.extraction_run_diagnostics(run_id),
        }

    @operation
    def extraction_raw_response(self, run_id: str, chunk_id: str) -> dict:
        handoff = self.narrative_extraction_handoff(run_id)
        chunk = next((item for item in handoff["chunks"] if item["id"] == chunk_id), None)
        if chunk is None:
            raise ValueError("分块不属于该提取任务")
        metadata = chunk["metadata"]
        return {
            "run_id": run_id,
            "chunk_id": chunk_id,
            "status": chunk["status"],
            "raw": chunk["raw"],
            "error": chunk["error"],
            "finish_reason": metadata.get("finish_reason"),
            "usage": metadata.get("usage"),
            "diagnostics": chunk["diagnostics"],
        }

    @operation
    def token_usage(self, run_id: str | None = None) -> dict:
        with self._store.connect() as db:
            db.execute("BEGIN")
            runs = [dict(row) for row in db.execute("SELECT id,data FROM runs")]
            if run_id is not None and not any(run["id"] == run_id for run in runs):
                raise ValueError("任务不存在")
            if run_id is None:
                chunks = [
                    dict(row)
                    for row in db.execute(
                        "SELECT id,run_id,status,metadata,raw IS NOT NULL AS has_result FROM chunks ORDER BY rowid"
                    )
                ]
            else:
                chunks = [
                    dict(row)
                    for row in db.execute(
                        "SELECT id,run_id,status,metadata,raw IS NOT NULL AS has_result FROM chunks WHERE run_id=? ORDER BY rowid",
                        (run_id,),
                    )
                ]
        return {"run_id": run_id, **usage.summarize(chunks, runs)}

    @operation
    def export_run(self, run_id: str) -> dict:
        return extraction.export_run(self._service, run_id)

    @operation
    def ingest_result(self, chunk_id: str, raw: str, *, metadata: dict | None = None) -> str | None:
        self._require_complete_model_response(chunk_id)
        return extraction.ingest_result(self._service, chunk_id, raw, metadata=metadata)

    def _require_complete_model_response(self, chunk_id: str) -> None:
        with self._store.connect() as db:
            row = db.execute("SELECT metadata FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        if row is None:
            raise ValueError("分块不存在")
        finish_reason = json.loads(row["metadata"] or "{}").get("finish_reason")
        if finish_reason is not None and finish_reason != "completed":
            raise OperationError(
                f"模型未完整结束：{finish_reason}；原始结果保留，不能导入或修补为模型成功。请新建任务重新提取"
            )

    @operation
    def extraction_review(
        self,
        chunk_id: str,
        raw: str | None = None,
        quotes: dict[int, str] | None = None,
        event_quotes: dict[int, str] | None = None,
    ) -> dict:
        result = extraction.review_result(self._service, chunk_id, raw, quotes, event_quotes)
        with self._store.connect() as db:
            row = db.execute("SELECT metadata FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        finish_reason = json.loads(row["metadata"] or "{}").get("finish_reason") if row else None
        if finish_reason is not None and finish_reason != "completed":
            result["finish_reason"] = finish_reason
            result["capacity_truncated"] = jobs.is_capacity_finish(finish_reason)
            if raw is None or raw == result["raw"]:
                result["importable"] = False
        return result

    @operation
    def repair_extraction_result(
        self,
        chunk_id: str,
        raw: str,
        quote_starts: dict[int, int],
        *,
        expected_raw: str | None = None,
        event_quote_starts: dict[int, int] | None = None,
    ) -> str | None:
        with self._store.connect() as db:
            row = db.execute(
                "SELECT raw,metadata FROM chunks WHERE id=?", (chunk_id,)
            ).fetchone()
        if row is None:
            raise ValueError("分块不存在")
        finish_reason = json.loads(row["metadata"] or "{}").get("finish_reason")
        if (
            finish_reason is not None
            and finish_reason != "completed"
            and raw == row["raw"]
            and not quote_starts
            and not event_quote_starts
        ):
            raise OperationError("模型未完整结束；原响应不能直接导入。请明确修正完整结果，或新建任务重新提取")
        return extraction.repair_result(
            self._service, chunk_id, raw, quote_starts, expected_raw, event_quote_starts
        )

    @operation
    def export_input_file(self, source_id: str, destination: str | Path) -> str:
        target = Path(destination)
        if target.exists():
            raise OperationError("导出文件已存在，请使用新文件名")
        run_id = self.create_run(source_id)
        target.write_text(
            json.dumps(self.export_run(run_id), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return run_id

    @operation
    def ingest_result_file(self, chunk_id: str, path: str | Path) -> str | None:
        return self.ingest_result(chunk_id, Path(path).read_text(encoding="utf-8"))

    @operation
    def backup(self, destination: str | Path) -> Path:
        return self._store.backup(destination)

    @operation
    def backup_download(self) -> Download:
        target = self.backup(
            self._store.path.parent / "backups" / f"{self._store.path.stem}-{uid()[:8]}.sqlite3"
        )
        return Download(target.name, target.read_bytes())

    @staticmethod
    @operation
    def restore_file(backup: str | Path, destination: str | Path) -> Path:
        return Store.restore(backup, destination)

    @operation
    def restore_upload(self, content: bytes, name: str) -> Path:
        target = self._project_path(self._store.path.parent, name)
        temp = self._store.path.parent / "backups" / f"upload-{uid()}.sqlite3"
        temp.parent.mkdir(parents=True, exist_ok=True)
        try:
            temp.write_bytes(content)
            return self.restore_file(temp, target)
        finally:
            temp.unlink(missing_ok=True)
