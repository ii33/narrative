"""Asynchronous Qt Quick adapter over public Operations graph capabilities.

No database connection, model runner or canvas geometry is shared with the GUI thread.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from . import graph_profile_ops, project_catalog, providers
from .extraction import build_candidate, extract_chapter
from .graph_operations import GraphOperations
from .harness import HarnessSettings
from .native_models import CanvasState, candidate_summary, node_detail, search_result
from .property_audit import audit_candidate
from .provider_store import ProviderStore

_NODE_TYPES = {"person", "group", "item", "location", "event", "scene", "storyline", "other"}


class NativeBridge(QObject):
    """QML context object named ``bridge``; all Operations I/O runs off the GUI thread."""

    # The string form is what QML needs: a bare ``dict`` registers a Python type
    # that QML cannot see, so every Connections handler silently fails.
    resultReady = Signal(str, "QVariantMap")  # type: ignore[arg-type]
    errorOccurred = Signal(str, str)
    busyChanged = Signal(bool)
    _workerDone = Signal(int, object, object)

    def __init__(self, project_directory: str | Path, parent: QObject | None = None):
        super().__init__(parent)
        self._directory = Path(project_directory).resolve()
        self._directory.mkdir(parents=True, exist_ok=True)
        self._project_path: Path | None = None
        self._canvas = CanvasState()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="narrative-native")
        self._requests: dict[int, tuple[str, int, Callable[[dict], dict] | None, int | None]] = {}
        self._next_request = 0
        self._epoch = 0
        self._query_epoch = 0
        self._query_spec: dict = {}
        self._query_canvas: dict | None = None
        self._unified_active = False
        self._query_folded: set[str] = set()
        self._query_visible: set[str] = set()
        self._detail_node = ""
        self._reading_cut: dict = {}
        self._closed = False
        self._workerDone.connect(self._on_worker_done)

    def close(self) -> None:
        """Stop queued UI reads; an already started graph agent remains independently observable."""
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _submit(
        self,
        kind: str,
        work: Callable[[], dict],
        apply: Callable[[dict], dict] | None = None,
        *,
        epoch: int | None = None,
        query_epoch: int | None = None,
    ) -> None:
        if self._closed:
            return
        self._next_request += 1
        request_id = self._next_request
        self._requests[request_id] = (kind, self._epoch if epoch is None else epoch, apply, query_epoch)
        if len(self._requests) == 1:
            self.busyChanged.emit(True)
        future = self._pool.submit(work)

        def completed(done) -> None:
            try:
                payload, error = done.result(), None
            except Exception as exc:  # surfaced to the UI, never silently swallowed
                payload, error = None, str(exc)
            try:
                if not self._closed:
                    self._workerDone.emit(request_id, payload, error)
            except RuntimeError:  # QObject was destroyed during window teardown
                pass

        future.add_done_callback(completed)

    @Slot(int, object, object)
    def _on_worker_done(self, request_id: int, payload: object, error: object) -> None:
        request = self._requests.pop(request_id, None)
        if not self._requests:
            self.busyChanged.emit(False)
        if request is None or self._closed:
            return
        kind, epoch, apply, query_epoch = request
        if epoch != self._epoch or (query_epoch is not None and query_epoch != self._query_epoch):
            return  # A different project was selected while the read was pending.
        if kind == "graph_view" and not self._unified_active:
            return
        if error is not None:
            self.errorOccurred.emit(kind, str(error))
            return
        try:
            result = payload if isinstance(payload, dict) else {}
            if kind == "detail" and result.get("node", {}).get("id") != self._detail_node:
                return
            if apply is not None:
                result = apply(result)
            self.resultReady.emit(kind, result)
        except Exception as exc:
            self.errorOccurred.emit(kind, str(exc))

    def _project(self) -> Path:
        if self._project_path is None:
            raise ValueError("请先打开图项目")
        return self._project_path

    def _project_task(
        self,
        kind: str,
        operation: Callable[[GraphOperations], dict],
        apply: Callable[[dict], dict] | None = None,
    ) -> None:
        try:
            path = self._project()
        except ValueError as exc:
            self.errorOccurred.emit(kind, str(exc))
            return

        def work() -> dict:
            ops = project_catalog.open_graph_project(path)
            return operation(ops)

        self._submit(kind, work, apply,
                     query_epoch=self._query_epoch if kind in {"canvas", "detail", "search", "expand", "timeline", "hierarchy", "reading_hierarchy", "event_compare", "property_context", "property_state", "property_world", "property_history",
                                   "node_properties"} else None)

    @staticmethod
    def _exact_integers(settings: dict) -> dict:
        """QML numbers arrive as doubles; strict int fields need exact integers back."""
        normalized = dict(settings)
        for key in (
            "max_tokens", "context_window", "output_reserve", "compaction_reserve",
            "compaction_max_tokens", "timeout_seconds",
        ):
            value = normalized.get(key)
            if isinstance(value, float) and value.is_integer():
                normalized[key] = int(value)
        return normalized

    def _path_for(self, name_or_path: str) -> Path:
        supplied = Path(name_or_path.strip())
        if not name_or_path.strip():
            raise ValueError("请选择项目")
        path = supplied if supplied.is_absolute() else self._directory / supplied
        if path.suffix == "":
            path = path.with_suffix(".sqlite3")
        path = path.resolve()
        if path.parent != self._directory or path.suffix.lower() != ".sqlite3":
            raise ValueError("项目须位于所选图项目目录")
        return path

    @staticmethod
    def _project_view(ops: GraphOperations) -> dict:
        info = ops.graph_project_info()
        return {
            "project": info,
            "sources": ops.sources(),
            "candidate_count": len(ops.graph_candidates("pending")),
            "provider": {
                key: ops.provider_profile().get(key) for key in (
                    "provider", "model", "max_tokens", "context_window", "output_reserve",
                    "compaction_reserve", "compaction_max_tokens",
                )
            },
        }

    @Slot()
    def refreshProjects(self) -> None:
        self._submit("projects", lambda: {"projects": project_catalog.list_projects(self._directory)})

    @Slot()
    def loadGlobalSettings(self) -> None:
        """Application-wide harness defaults; available before a project is open."""

        def work() -> dict:
            store = ProviderStore()
            return {
                "profile": graph_profile_ops.global_settings(store),
                "catalog": providers.catalog(),
                "path": str(store.path),
            }

        self._submit("global_settings", work)

    @Slot("QVariantMap")
    def saveGlobalSettings(self, settings: dict) -> None:
        normalized = self._exact_integers(settings)

        def work() -> dict:
            store = ProviderStore()
            profile = graph_profile_ops.save_global_settings(store, normalized)
            return {"profile": profile, "catalog": providers.catalog(), "path": str(store.path)}

        self._submit("global_settings", work)

    @Slot()
    def clearIncompatibleProjects(self) -> None:
        """Remove project files this build cannot open (older schema versions)."""

        def work() -> dict:
            result = project_catalog.clear_incompatible(self._directory)
            return result | {"projects": project_catalog.list_projects(self._directory)}

        self._submit("projects_cleared", work)

    @Slot()
    def closeProject(self) -> None:
        """Return to the launcher: drop the open project and its cached reads."""
        self._epoch += 1
        self._reading_cut = {}
        self._project_path = None
        self._canvas.clear()
        self._query_canvas = None
        self.resultReady.emit("project_closed", {})

    @Slot(str)
    def createProject(self, name: str) -> None:
        if not re.fullmatch(r"[\w-]{1,64}", name):
            self.errorOccurred.emit("project", "项目名只能含字母、数字、中文、下划线或短横线")
            return
        path = self._directory / f"{name}.sqlite3"
        if path.exists():
            self.errorOccurred.emit("project", "同名项目已经存在")
            return
        self._epoch += 1
        self._reading_cut = {}
        self._project_path = None
        self._canvas.clear()
        self._query_canvas = None
        epoch = self._epoch

        def work() -> dict:
            ops = project_catalog.create_graph_project(path)
            return self._project_view(ops)

        def apply(result: dict) -> dict:
            self._project_path = path
            self._canvas.clear()
            return result

        self._submit("project", work, apply, epoch=epoch)

    @Slot(str)
    def openProject(self, name_or_path: str) -> None:
        try:
            path = self._path_for(name_or_path)
        except ValueError as exc:
            self.errorOccurred.emit("project", str(exc))
            return
        self._epoch += 1
        self._reading_cut = {}
        self._project_path = None
        self._canvas.clear()
        self._query_canvas = None
        epoch = self._epoch

        def work() -> dict:
            ops = project_catalog.open_graph_project(path)
            return self._project_view(ops)

        def apply(result: dict) -> dict:
            self._project_path = path
            self._canvas.clear()
            return result

        self._submit("project", work, apply, epoch=epoch)

    @Slot()
    def loadGraph(self) -> None:
        self._unified_active = False
        def work(ops) -> dict:
            return {"view": ops.graph_query(limit=2000, disclosure_cut=self._reading_cut or None), "labels": ops.graph_display_map()["items"]}

        def apply(result: dict) -> dict:
            self._query_canvas = None
            return self._canvas.seed(result["view"], result["labels"], prefer_events=any(e.get("semantic") == "temporal" for e in result["view"].get("edges", [])))

        self._project_task("canvas", work, apply)

    @Slot("QVariantMap")
    def loadGraphView(self, spec: dict) -> None:
        """One shared query controls every reading projection and cancels stale reads."""
        try:
            self._project()
        except ValueError as exc:
            self.errorOccurred.emit("graph_view", str(exc))
            return
        self._query_epoch += 1
        self._unified_active = True
        requested = dict(spec)
        self._reading_cut = dict(requested.get("disclosure_cut") or {})
        self._query_spec = requested
        self._query_canvas = None
        self._query_folded.clear()
        self._query_visible.clear()
        self._canvas.clear(preserve_positions=True)

        def work(ops) -> dict:
            revision = requested.get("as_of_revision")
            if revision is None:
                revision = ops.graph_project_info()["revision"]
            pinned = requested | {"as_of_revision": revision}
            view = ops.graph_view(pinned | {"view": "table"})
            return {"view": view, "labels": ops.graph_display_map(as_of_revision=revision)["items"]}

        def apply(result: dict) -> dict:
            view = result["view"]
            self._query_canvas = view
            self._query_visible = {node["id"] for node in view["nodes"]}
            return {"canvas": view, "labels": result["labels"]}

        self._project_task("graph_view", work, apply)

    @Slot(str)
    def loadPropertyContext(self, node_id: str = "") -> None:
        """Load selectable nodes and the reviewed node-local property projection."""
        def work(ops) -> dict:
            snapshot = ops.graph_snapshot()
            world = ops.property_world_state()
            return {
                "revision": snapshot["revision"],
                "nodes": [{key: node.get(key) for key in ("id", "name", "node_type")}
                          for node in snapshot.get("nodes", [])],
                "world": world,
                "selected_node_id": node_id,
            }
        self._project_task("property_context", work)

    @Slot(str, str, str)
    def queryProperty(self, node_id: str, property_id: str, at_json: str = "") -> None:
        """Query one property and its append-only history through Operations."""
        try:
            at = json.loads(at_json) if at_json.strip() else None
        except json.JSONDecodeError as exc:
            self.errorOccurred.emit("property_state", f"at 必须是 JSON TimeRef：{exc.msg}")
            return

        def work(ops) -> dict:
            return {
                "node_id": node_id,
                "property_id": property_id,
                "at": at,
                "state": ops.property_state(node_id, property_id, at),
                "history": ops.property_history(node_id, property_id),
            }
        self._project_task("property_state", work)

    @Slot(str, str, "QVariantMap", str, str)
    def queryPropertyDisclosure(
        self, node_id: str, property_id: str, cut: dict,
        scope: str = "world", holder_id: str = "",
    ) -> None:
        """Reader-facing disclosure projection; ignores story time."""
        if not node_id.strip() or not property_id.strip():
            self.errorOccurred.emit("property_disclosure", "节点和属性不能为空")
            return
        if not cut.get("source_id") or cut.get("start") is None:
            self.errorOccurred.emit("property_disclosure", "披露切点须包含 source_id 和 start")
            return

        def work(ops) -> dict:
            return {
                "node_id": node_id, "property_id": property_id,
                "scope": scope or "world", "holder_id": holder_id or None,
                "cut": dict(cut),
                "state": ops.property_disclosure_state(
                    node_id, property_id, dict(cut),
                    scope=scope or "world", holder_id=holder_id or None,
                ),
            }
        self._project_task("property_disclosure", work)

    @Slot(str)
    def loadPropertyWorldState(self, at_json: str = "") -> None:
        """Load the aggregate projection; inverse holder lookups derive from it."""
        try:
            at = json.loads(at_json) if at_json.strip() else None
        except json.JSONDecodeError as exc:
            self.errorOccurred.emit("property_world", f"at 必须是 JSON TimeRef：{exc.msg}")
            return
        self._project_task("property_world", lambda ops: {"world": ops.property_world_state(at)})

    @Slot(str, int)
    def proposePropertyRecords(self, records_json: str, expected_revision: int) -> None:
        """Precheck and stage property records; acceptance remains a human action."""
        try:
            records = json.loads(records_json)
        except json.JSONDecodeError as exc:
            self.errorOccurred.emit("property_stage", f"记录 JSON 无效：{exc.msg}")
            return
        if not isinstance(records, list) or not records or not all(isinstance(row, dict) for row in records):
            self.errorOccurred.emit("property_stage", "请提供非空 JSON 对象数组")
            return

        def work(ops) -> dict:
            diagnostics = ops.property_precheck(records, expected_revision)
            if any(item.get("severity") == "error" for item in diagnostics):
                return {"status": "precheck_failed", "diagnostics": diagnostics,
                        "expected_revision": expected_revision}
            candidate_id = ops.property_propose(records, expected_revision, source="manual")
            return {"status": "pending", "candidate": ops.property_candidate(candidate_id),
                    "diagnostics": diagnostics}
        self._project_task("property_stage", work)

    @Slot()
    def auditProperties(self) -> None:
        """Whole-graph property audit; the review page shows the findings."""
        self._project_task("property_audit", lambda ops: ops.property_audit())

    @Slot(str)
    def loadNodeProperties(self, node_id: str) -> None:
        """Every property of one node, read-only; used by the browsing detail pane."""

        def work(ops) -> dict:
            return ops.property_node_summary(node_id)

        self._project_task("node_properties", work)

    @Slot(str, int)
    def acceptPropertyCandidate(self, candidate_id: str, version: int) -> None:
        """Accept a staged property candidate; the human action stays explicit."""

        def work(ops) -> dict:
            revision = ops.property_accept(candidate_id, version)
            return {"status": "accepted", "revision": revision}

        self._project_task("property_review", work)

    @Slot(str, "QVariantList")
    def searchNodes(self, query: str, types: list) -> None:
        requested = [str(item) for item in types]
        if not set(requested) <= _NODE_TYPES:
            self.errorOccurred.emit("search", "节点类型筛选无效")
            return

        def work(ops) -> dict:
            view = ops.graph_query(query=query, limit=2000, disclosure_cut=self._reading_cut or None)
            return search_result(view, ops.graph_display_map()["items"], requested)
    @Slot(str)
    def nodeDetail(self, node_id: str) -> None:
        self._detail_node = node_id
        cut = dict(self._reading_cut)
        def work(ops) -> dict:
            return node_detail(
                ops.graph_query(node_id=node_id, limit=2000, disclosure_cut=cut or None), node_id,
                ops.graph_display_map()["items"], ops.sources(),
            )

        self._project_task("detail", work)

    def _extraction_dir(self) -> Path:
        project = self._project()
        return project.with_name(project.stem + "_extraction")

    @Slot(str)
    def loadExtraction(self, source_id: str) -> None:
        """Read a stored extraction for one source, if it exists."""
        directory = self._extraction_dir()
        path = directory / f"{source_id}.json"

        def work() -> dict:
            if not path.is_file():
                return {"source_id": source_id, "nodes": [], "grouped": {}, "counts": {},
                        "rejected": [], "missing": True}
            return json.loads(path.read_text(encoding="utf-8"))

        self._submit("extraction", work)

    @Slot(str)
    def startExtraction(self, source_id: str) -> None:
        """Read the whole source and extract nodes and events with the project model."""
        directory = self._extraction_dir()

        def work(ops) -> dict:
            source = ops.source(source_id)
            settings = HarnessSettings.model_validate(
                ops.harness_settings() | {"timeout_seconds": 600}
            )
            api_key = os.environ.get(settings.key_env) or os.environ.get("DEEPSEEK_API_KEY")
            if not api_key:
                raise ValueError(f"缺少 {settings.key_env} 或 DEEPSEEK_API_KEY")
            data = extract_chapter(
                source["title"], source["text"],
                settings=settings, api_key=api_key,
                workdir=directory / source_id / "sdk", ops=ops,
            )
            data.pop("raw", None)
            data["source_id"] = source_id
            data["source_hash"] = source["hash"]
            data["extracted_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{source_id}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return data

        self._project_task("extraction", work)

    @Slot(str)
    def commitExtraction(self, source_id: str) -> None:
        """Turn a stored extraction into one pending candidate."""
        path = self._extraction_dir() / f"{source_id}.json"

        def work(ops) -> dict:
            if not path.is_file():
                raise ValueError("还没有提取结果")
            data = json.loads(path.read_text(encoding="utf-8"))
            built = build_candidate(ops, data)
            if built.get("empty"):
                return {"candidate": None, "empty": True, "diagnostics": []}
            if built["errors"]:
                return {"diagnostics": built["diagnostics"], "candidate": None}
            # Review round: audit the nodes this extraction touches before the
            # candidate is accepted, so overlaps and contradictions surface now.
            audit = audit_candidate(ops, built)
            candidate = ops.change_propose(
                built["objects"], built["property_records"], built["revision"],
                source="correction" if built.get("requires_correction") else "model",
                note=f"原文提取：{data.get('chapter', source_id)}",
            )
            return {
                "candidate": ops.change_candidate(candidate),
                "diagnostics": built["diagnostics"],
                "node_count": built["node_count"],
                "scene_links": built["scene_links"],
                "audit": audit,
            }

        self._project_task("extraction_commit", work)

    @Slot(str, "QVariantMap")
    def reviseNode(self, node_id: str, changes: dict) -> None:
        """Correct one node (type, name, source position, retraction) as a candidate.

        Nothing is written directly: the edit becomes a pending candidate that a
        human accepts on the candidate page, same as any other change.
        """
        allowed = {
            "node_type", "name", "aliases", "identity_note",
            "disclosure", "retracted",
        }
        payload = {
            key: value for key, value in (changes or {}).items() if key in allowed
        }
        if not payload:
            self.errorOccurred.emit("node_edit", "没有可应用的修改")
            return

        def work(ops) -> dict:
            snapshot = ops.graph_snapshot()
            node = next(
                (item for item in snapshot["nodes"] if item["id"] == node_id), None
            )
            if node is None:
                raise ValueError("节点不存在")
            updated = dict(node) | payload
            revision = ops.graph_project_info()["revision"]
            diagnostics = ops.change_precheck([updated], [], revision)
            errors = [item for item in diagnostics if item.get("severity") == "error"]
            if errors:
                return {"diagnostics": diagnostics, "candidate": None, "node_id": node_id}
            candidate = ops.change_propose(
                [updated], [], revision, source="correction",
                note=f"节点修正：{node['name']}",
            )
            return {
                "diagnostics": diagnostics,
                "candidate": ops.change_candidate(candidate),
                "node_id": node_id,
                "changes": payload,
            }

        self._project_task("node_edit", work)

    @Slot(str)
    def loadCandidates(self, status: str) -> None:
        self._project_task(
            "candidates",
            lambda ops: {"items": [candidate_summary(item) for item in ops.graph_candidates(status or None)]},
        )

    @Slot(str)
    def loadChangeCandidates(self, status: str) -> None:
        self._project_task(
            "change_candidates",
            lambda ops: {"items": ops.change_candidates(status or None)},
        )

    @Slot(str)
    def changeCandidateDetail(self, candidate_id: str) -> None:
        self._project_task(
            "change_candidate",
            lambda ops: {"candidate": ops.change_candidate(candidate_id)},
        )

    @Slot(str, int)
    def acceptChangeCandidate(self, candidate_id: str, version: int) -> None:
        def work(ops) -> dict:
            revision = ops.change_accept(candidate_id, version)
            return {"candidate_id": candidate_id, "status": "accepted", "revision": revision}

        def apply(result: dict) -> dict:
            self._canvas.clear(preserve_positions=True)
            return result | {"graph_reset_required": True}

        self._project_task("change_review", work, apply)

    @Slot(str, int)
    def rejectChangeCandidate(self, candidate_id: str, version: int) -> None:
        def work(ops) -> dict:
            ops.change_reject(candidate_id, version)
            return {"candidate_id": candidate_id, "status": "rejected",
                    "revision": ops.graph_project_info()["revision"]}

        self._project_task("change_review", work)

    @Slot(str, str, int, str, str)
    def proposeChange(
        self, graph_json: str, properties_json: str,
        expected_revision: int, source: str = "manual", note: str = "",
    ) -> None:
        try:
            graph_objects = json.loads(graph_json) if graph_json.strip() else []
            property_records = json.loads(properties_json) if properties_json.strip() else []
        except json.JSONDecodeError as exc:
            self.errorOccurred.emit("change_stage", f"JSON 解析失败：{exc.msg}")
            return
        if not isinstance(graph_objects, list) or not isinstance(property_records, list):
            self.errorOccurred.emit("change_stage", "图对象和属性记录都必须是 JSON 数组")
            return

        def work(ops) -> dict:
            diagnostics = ops.change_precheck(graph_objects, property_records, expected_revision)
            if any(item.get("severity") == "error" for item in diagnostics):
                return {"diagnostics": diagnostics, "candidate": None}
            candidate_id = ops.change_propose(
                graph_objects, property_records, expected_revision,
                source=source or "manual", note=note,
            )
            return {
                "diagnostics": diagnostics,
                "candidate": ops.change_candidate(candidate_id),
            }
        self._project_task("change_stage", work)

    @Slot(str)
    def candidateDetail(self, candidate_id: str) -> None:
        self._project_task(
            "candidate", lambda ops: {"preview": ops.graph_candidate_preview(candidate_id)},
        )

    @Slot(str, int)
    def acceptCandidate(self, candidate_id: str, version: int) -> None:
        def work(ops) -> dict:
            revision = ops.graph_accept(candidate_id, version)
            return {"candidate_id": candidate_id, "status": "accepted", "revision": revision}

        def apply(result: dict) -> dict:
            self._canvas.clear(preserve_positions=True)
            return result | {"graph_reset_required": True}

        self._project_task("review", work, apply)

    @Slot(str, int)
    def rejectCandidate(self, candidate_id: str, version: int) -> None:
        def work(ops) -> dict:
            ops.graph_reject(candidate_id, version)
            return {"candidate_id": candidate_id, "status": "rejected", "revision": ops.graph_project_info()["revision"]}

        self._project_task("review", work)

    @Slot()
    def loadSources(self) -> None:
        self._project_task("sources", lambda ops: {"items": ops.sources()})

    @Slot(str)
    def sourceText(self, source_id: str) -> None:
        self._project_task("source", lambda ops: {"source": ops.source(source_id)})

    @Slot(str, str)
    def importSource(self, title: str, text: str) -> None:
        def work(ops) -> dict:
            source_id = ops.import_source(title, text)
            return {"source_id": source_id, "sources": ops.sources(), "revision": ops.graph_project_info()["revision"]}

        self._project_task("source_imported", work)

    @Slot(str, str)
    def importSourceFile(self, title: str, file_path: str) -> None:
        def work(ops) -> dict:
            path = Path(file_path)
            if not path.is_file() or path.stat().st_size > 10_000_000:
                raise ValueError("原文文件不存在或超过 10 MB")
            source_id = ops.import_source(title or path.stem, path.read_text(encoding="utf-8-sig"))
            return {"source_id": source_id, "sources": ops.sources(), "revision": ops.graph_project_info()["revision"]}

        self._project_task("source_imported", work)

    @Slot()
    def loadProviderProfile(self) -> None:
        self._project_task(
            "provider", lambda ops: {"profile": ops.provider_profile(), "catalog": ops.provider_catalog()},
        )

    @Slot(str)
    def loadProviderProfileFor(self, provider: str) -> None:
        """Read this project's saved provider profile before editing or activating it."""
        self._project_task(
            "provider",
            lambda ops: {
                "profile": ops.harness_settings(provider.strip() or None),
                "catalog": ops.provider_catalog(),
            },
        )

    @Slot("QVariantMap")
    def saveProviderProfile(self, settings: dict) -> None:
        self._project_task(
            "provider", lambda ops: {"profile": ops.save_provider_profile(self._exact_integers(settings))}
        )

    @Slot("QVariantList", str, "QVariantMap")
    def startAnalysis(self, source_ids: list, task: str, budget: dict) -> None:
        def work(ops) -> dict:
            run_id = ops.start_graph_analysis(
                [str(item) for item in source_ids], task=task or None, budget=budget or None,
            )
            return {"run_id": run_id, "status": "queued"}

        self._project_task("run_started", work)

    @Slot()
    def loadRuns(self) -> None:
        self._project_task("runs", lambda ops: {"items": ops.graph_analysis_runs()})

    @Slot(str)
    def loadTrace(self, run_id: str) -> None:
        self._project_task("trace", lambda ops: {"trace": ops.graph_analysis_trace(run_id)})

    @Slot(str, int)
    def loadModelResponse(self, run_id: str, round_number: int) -> None:
        self._project_task(
            "model_response", lambda ops: {"response": ops.graph_analysis_response(run_id, round_number)},
        )

    @Slot(str)
    def pauseRun(self, run_id: str) -> None:
        self._project_task("run_control", lambda ops: self._control(ops, "pause", run_id))

    @Slot(str)
    def resumeRun(self, run_id: str) -> None:
        self._project_task("run_control", lambda ops: self._control(ops, "resume", run_id))

    @Slot(str, str)
    def instructRun(self, run_id: str, text: str) -> None:
        def work(ops) -> dict:
            instruction_id = ops.instruct_graph_analysis(run_id, text)
            return {"run_id": run_id, "action": "instruct", "instruction_id": instruction_id,
                    "control": ops.graph_analysis_control(run_id)}

        self._project_task("run_control", work)

    @Slot(str)
    def cancelRun(self, run_id: str) -> None:
        self._project_task("run_control", lambda ops: self._control(ops, "cancel", run_id))

    @staticmethod
    def _control(ops: GraphOperations, action: str, run_id: str) -> dict:
        method = {
            "pause": ops.pause_graph_analysis,
            "resume": ops.resume_graph_analysis,
            "cancel": ops.cancel_graph_analysis,
        }[action]
        method(run_id)
        return {"run_id": run_id, "action": action, "control": ops.graph_analysis_control(run_id)}
