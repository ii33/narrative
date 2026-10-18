"""Asynchronous Qt Quick adapter over public Operations graph capabilities.

No database connection, model runner or canvas geometry is shared with the GUI thread.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from .graph_operations import GraphOperations
from .native_models import CanvasState, candidate_summary, node_detail, search_result
from .operations import Operations

_NODE_TYPES = {"person", "group", "item", "location", "event", "scene", "storyline", "other"}


class NativeBridge(QObject):
    """QML context object named ``bridge``; all Operations I/O runs off the GUI thread."""

    resultReady = Signal(str, dict)
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
            ops = Operations.open_graph_project(path)
            return operation(ops)

        self._submit(kind, work, apply,
                     query_epoch=self._query_epoch if kind in {"canvas", "detail", "search", "expand", "timeline", "hierarchy", "reading_hierarchy"} else None)

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
        self._submit("projects", lambda: {"projects": Operations.list_projects(self._directory)})

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
            ops = Operations.create_graph_project(path)
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
            ops = Operations.open_graph_project(path)
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
            path = self._project()
        except ValueError as exc:
            self.errorOccurred.emit("graph_view", str(exc))
            return
        self._query_epoch += 1
        self._unified_active = True
        query_epoch = self._query_epoch
        requested = dict(spec)
        self._reading_cut = dict(requested.get("disclosure_cut") or {})
        self._query_spec = requested
        self._query_canvas = None
        self._query_folded.clear()
        self._query_visible.clear()
        self._canvas.clear(preserve_positions=True)

        def work() -> dict:
            ops = Operations.open_graph_project(path)
            revision = requested.get("as_of_revision")
            if revision is None:
                revision = ops.graph_project_info()["revision"]
            pinned = requested | {"as_of_revision": revision}
            views = {view: ops.graph_view(pinned | {"view": view})
                     for view in ("graph", "timeline", "table", "forest")}
            return {"views": views, "labels": ops.graph_display_map(as_of_revision=revision)["items"],
                    "index": ops.graph_event_index(context_id=requested.get("context_id", "canon"),
                        as_of_revision=revision, disclosure_cut=requested.get("disclosure_cut") or None)}

        def apply(result: dict) -> dict:
            views = result["views"]
            self._query_canvas = self._canvas.replace_query(views["graph"], result["labels"])
            self._query_visible = {node["id"] for node in self._query_canvas["nodes"]}
            return {"canvas": self._query_canvas,
                    "timeline": views["timeline"], "table": views["table"],
                    "hierarchy": views["forest"], "index": result["index"],
                    "query": views["graph"]["query"], "request": requested,
                    "query_json": json.dumps(views["graph"]["query"], ensure_ascii=False, indent=2)}

        self._submit("graph_view", work, apply, query_epoch=query_epoch)

    @Slot()
    def loadTimeline(self) -> None:
        self._project_task("timeline", lambda ops: ops.graph_timeline(disclosure_cut=self._reading_cut or None))

    @Slot(str)
    def collapseNode(self, node_id: str) -> None:
        try:
            if self._unified_active and self._query_canvas is None:
                raise ValueError("查询正在加载，请稍后折叠")
            if self._query_canvas is not None:
                self._query_folded.add(node_id)
                nodes, visible_edges = self._folded_query_projection()
                visible = {node["id"] for node in nodes}
                removed = self._query_visible - visible
                edges = [e["assertion_id"] for e in self._query_canvas["edges"]
                         if e["assertion_id"] not in {edge["assertion_id"] for edge in visible_edges}]
                self._query_visible = visible
                self.resultReady.emit("collapse", {"center_id": node_id,
                    "nodes_removed": sorted(removed), "edges_removed": edges})
                return
            self.resultReady.emit("collapse", self._canvas.collapse(node_id))
        except ValueError as exc:
            self.errorOccurred.emit("collapse", str(exc))

    def _folded_query_projection(self) -> tuple[list[dict], list[dict]]:
        view = self._query_canvas or {"nodes": [], "edges": []}
        focus = (view.get("focus") or {}).get("id")
        hidden = {edge[key] for edge in view["edges"]
                  if self._query_folded.intersection((edge["subject_id"], edge["object_id"]))
                  for key in ("subject_id", "object_id")} - self._query_folded - {focus}
        return ([node for node in view["nodes"] if node["id"] not in hidden],
                [edge for edge in view["edges"]
                 if edge["subject_id"] not in hidden and edge["object_id"] not in hidden])

    @Slot()
    @Slot(str)
    def loadHierarchy(self, candidate_id: str = "") -> None:
        """Read an accepted hierarchy or an explicit, review-only candidate preview."""
        def work(ops) -> dict:
            tree = ops.graph_hierarchy(candidate_id=candidate_id or None,
                                       disclosure_cut=None if candidate_id else self._reading_cut or None)
            if not candidate_id:
                tree["event_index"] = ops.graph_event_index(disclosure_cut=self._reading_cut or None)
            return tree
        self._project_task("hierarchy", work)

    @Slot()
    def loadReadingPositions(self) -> None:
        def work(ops) -> dict:
            orders = {row["source_id"]: row["ordinal"] for row in
                      ops.graph_snapshot().get("source_orders", []) if row["context_id"] == "canon"}
            sources = sorted((s for s in ops.sources() if s["id"] in orders),
                             key=lambda s: orders[s["id"]])
            items = []
            for source in sources:
                text = ops.source(source["id"])["text"]
                for match in re.finditer(r"[^\r\n]+", text):
                    excerpt = match.group().strip()
                    if excerpt:
                        items.append({"source_id": source["id"], "start": match.end() - 1,
                                      "label": source["title"] + " · " + excerpt[:45],
                                      "excerpt": excerpt})
            return {"items": items, "unregistered": len(ops.sources()) - len(sources)}
        self._project_task("reading_positions", work)

    @Slot(str, "QVariantMap")
    def loadReadingHierarchy(self, focus_id: str, cut: dict) -> None:
        self._query_epoch += 1
        self._unified_active = False
        if cut != self._reading_cut:
            self._epoch += 1
            self._reading_cut = dict(cut)
            self._canvas.clear(preserve_positions=True)
        def work(ops) -> dict:
            tree = ops.graph_hierarchy(disclosure_cut=cut or None)
            actual_focus = focus_id if any(n["id"] == focus_id for n in tree["nodes"]) else ""
            if actual_focus:
                tree = ops.graph_hierarchy(focus_id=actual_focus, disclosure_cut=cut or None)
            return {"hierarchy": tree, "index": ops.graph_event_index(
                focus_id=actual_focus or None, disclosure_cut=cut or None),
                "requested_focus_id": focus_id, "focus_id": actual_focus, "cut": cut}
        self._project_task("reading_hierarchy", work)

    @Slot(str, "QVariantList")
    def searchNodes(self, query: str, types: list) -> None:
        requested = [str(item) for item in types]
        if not set(requested) <= _NODE_TYPES:
            self.errorOccurred.emit("search", "节点类型筛选无效")
            return

        def work(ops) -> dict:
            view = ops.graph_query(query=query, limit=2000, disclosure_cut=self._reading_cut or None)
            return search_result(view, ops.graph_display_map()["items"], requested)

        self._project_task("search", work)

    @Slot(str)
    def expandNode(self, node_id: str) -> None:
        if self._unified_active and self._query_canvas is None:
            self.errorOccurred.emit("expand", "查询正在加载，请稍后恢复")
            return
        if self._query_canvas is not None:
            # Restore only the current query's bounded projection, never re-query neighbors.
            self._query_folded.discard(node_id)
            nodes, edges = self._folded_query_projection()
            self._query_visible = {node["id"] for node in nodes}
            self.resultReady.emit("expand", {"center_id": node_id,
                "revision": self._query_canvas["revision"],
                "nodes_added": nodes, "edges_added": edges, "pending_excluded": True})
            return
        def work(ops) -> dict:
            return {
                "view": ops.graph_neighborhood(node_id, depth=1, limit=2000, disclosure_cut=self._reading_cut or None),
                "labels": ops.graph_display_map()["items"],
            }

        def apply(result: dict) -> dict:
            return self._canvas.expand(result["view"], result["labels"])

        self._project_task("expand", work, apply)

    @Slot(str, float, float)
    def setNodePosition(self, node_id: str, x: float, y: float) -> None:
        self._canvas.set_position(node_id, x, y)

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

    @Slot(str)
    def loadCandidates(self, status: str) -> None:
        self._project_task(
            "candidates",
            lambda ops: {"items": [candidate_summary(item) for item in ops.graph_candidates(status or None)]},
        )

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
        # QML JavaScript numbers arrive through QVariantMap as doubles. Preserve
        # Operations' strict integer validation while restoring exact integers.
        normalized = dict(settings)
        for key in (
            "max_tokens", "context_window", "output_reserve", "compaction_reserve",
            "compaction_max_tokens", "timeout_seconds",
        ):
            value = normalized.get(key)
            if isinstance(value, float) and value.is_integer():
                normalized[key] = int(value)
        self._project_task("provider", lambda ops: {"profile": ops.save_provider_profile(normalized)})

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
