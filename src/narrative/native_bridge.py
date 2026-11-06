"""Asynchronous Qt Quick adapter over public Operations graph capabilities.

No database connection, model runner or canvas geometry is shared with the GUI thread.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QObject, Signal, Slot

from . import graph_profile_ops, project_catalog, project_package, providers
from .browse_view import moment_tree
from .graph_operations import GraphOperations
from .harness import HarnessRunner, harness_env
from .native_models import CanvasState, node_detail, search_result
from .node_material import holds_at
from .prompt_defaults import DEFAULT_PROMPTS
from .prompt_settings import effective_prompt
from .provider_store import GLOBAL_SCOPE, ProviderStore

_NODE_TYPES = {"person", "group", "item", "location", "event", "action", "scene", "big_event", "storyline", "other"}
SCREENPLAY_WRITING_PROMPT_VERSION = "screenplay-writing-ui/9"

SCREENPLAY_SCENE_TABLE_RULES = DEFAULT_PROMPTS["scene_table"]["text"]



class NativeBridge(QObject):
    """QML context object named ``bridge``; all Operations I/O runs off the GUI thread."""

    # The string form is what QML needs: a bare ``dict`` registers a Python type
    # that QML cannot see, so every Connections handler silently fails.
    blockTextDoubleClicked = Signal(str)
    resultReady = Signal(str, "QVariantMap")  # type: ignore[arg-type]
    errorOccurred = Signal(str, str)
    busyChanged = Signal(bool)
    # 一次后台操作花了多久（毫秒）。只在明显偏慢时发，用来定位「界面卡」到底卡在哪一步。
    slowOperation = Signal(str, int)
    _workerDone = Signal(int, object, object)

    # 一个操作最多等这么久。超过就放弃它并把「处理中」清掉：
    # 结果要是永远不回来，界面就会一直卡在「处理中」，什么都点不动。
    STALE_AFTER_SECONDS = 10.0

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.resultReady.connect(self._invalidate_conversation_graph)
        from .native_text_events import ReadOnlyBlockTextEvents
        self._block_text_events = ReadOnlyBlockTextEvents(self)
        self._block_text_events.doubleClicked.connect(self.blockTextDoubleClicked.emit)
        from .native_content import FormSubmissions
        self._content_forms = FormSubmissions()
        self._project_path: Path | None = None
        self._canvas = CanvasState()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="narrative-native")
        self._requests: dict[
            int, tuple[str, int, Callable[[dict], dict] | None, int | None, float, str]
        ] = {}
        self._next_request = 0
        self._static_scan_generation = 0
        self._static_target_generation = 0
        self._screenplay_query_generation = 0
        self._epoch = 0
        self._query_epoch = 0
        self._recent_generation = 0
        self._query_spec: dict = {}
        self._query_canvas: dict | None = None
        self._unified_active = False
        self._query_folded: set[str] = set()
        self._query_visible: set[str] = set()
        self._detail_node = ""
        self._reading_cut: dict = {}
        self._state_cut: str = ""
        self._closed = False
        # 常驻协作者：一个工程一条会话，模型看得见自己前面的工具调用。
        self._assistant_runtime = None
        self._assistant_runner = None
        self._assistant_tool_host = None
        self._assistant_steps: list[dict] = []
        self._assistant_task_lock = threading.Lock()
        self._assistant_jobs: dict[int, dict] = {}
        self._assistant_activity_sequence = 0
        self._assistant_chat = ""
        self._assistant_resumed: set[str] = set()
        self._assistant_lock = threading.Lock()
        self._workerDone.connect(self._on_worker_done)

    def close(self) -> None:
        """Cancel owned tasks and close this project runtime; ignore late UI callbacks."""
        self._closed = True
        self._close_assistant()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _submit(
        self,
        kind: str,
        work: Callable[[], dict],
        apply: Callable[[dict], dict] | None = None,
        *,
        epoch: int | None = None,
        query_epoch: int | None = None,
        content_form: str = "",
    ) -> None:
        if self._closed:
            return
        self._drop_stale()
        self._next_request += 1
        request_id = self._next_request
        started = time.perf_counter()
        self._requests[request_id] = (
            kind, self._epoch if epoch is None else epoch, apply, query_epoch, started, content_form,
        )
        if len(self._requests) == 1:
            self.busyChanged.emit(True)
        future = self._pool.submit(work)

        def completed(done) -> None:
            try:
                payload, error = done.result(), None
            except Exception as exc:  # surfaced to the UI, never silently swallowed
                payload, error = None, str(exc)
            # 先把结果送出去，再报耗时：报告失败绝不能连累结果本身。
            # 这条路径上没有别的兜底——丢了它，界面就永远停在「处理中」。
            try:
                if not self._closed:
                    self._workerDone.emit(request_id, payload, error)
            except Exception:  # QObject destroyed during teardown
                pass
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms >= 150:
                try:
                    self.slowOperation.emit(kind, elapsed_ms)
                except Exception:
                    pass

        future.add_done_callback(completed)

    @Slot(str, "QVariantMap")
    def _invalidate_conversation_graph(self, kind, payload):
        if (kind == "conversation_progress" and payload.get("graph_reset_required")
                and self._project_path is not None
                and self.recentProjectKey(payload.get("project_path", "")) == self.recentProjectKey(str(self._project_path))):
            self._canvas.clear(preserve_positions=True)

    @Slot(int, object, object)
    def _on_worker_done(self, request_id: int, payload: object, error: object) -> None:
        with self._assistant_lock:
            self._assistant_jobs.pop(request_id, None)
        request = self._requests.pop(request_id, None)
        if not self._requests:
            self.busyChanged.emit(False)
        if request is None or self._closed:
            return
        kind, epoch, apply, query_epoch, started, content_form = request
        if epoch != self._epoch or (query_epoch is not None and query_epoch != self._query_epoch):
            return  # A different project was selected while the read was pending.
        if kind == "graph_view" and not self._unified_active:
            return
        if error is not None:
            conflict = self._content_forms.take_conflict(content_form)
            if conflict:
                self.resultReady.emit("content_submission_conflict", {**conflict, "form_id": content_form,
                    "project_path": str(self._project_path or "")})
            self.errorOccurred.emit(kind, str(error))
            return
        try:
            result = payload if isinstance(payload, dict) else {}
            conflict = self._content_forms.take_conflict(content_form)
            if conflict:
                self.resultReady.emit("content_submission_conflict", {**conflict, "form_id": content_form,
                    "project_path": str(self._project_path or "")})
            if kind in {"chat", "chats"}:
                result = result | self._assistant_activity_snapshot()
            if kind == "recent_projects" and result.get("generation") != self._recent_generation:
                return
            if kind == "detail" and result.get("node", {}).get("id") != self._detail_node:
                return
            from .native_content import receipt
            submission = receipt(result)
            if submission and submission.get("status") == "staged" and kind not in {"content_submission_read", "content_submissions"}:
                self.resultReady.emit("content_submission", {**submission, "form_id": content_form,
                    "original_kind": kind, "project_path": str(self._project_path or "")})
                return
            if apply is not None:
                result = apply(result)
            if submission and kind not in {"content_submission_read", "content_submissions"}:
                result = {**result, "content_form_id": content_form}
            # 界面自己处理这条结果花了多久：卡在布局/重建列表时，这个数会很大。
            handler_started = time.perf_counter()
            self.resultReady.emit(kind, result)
            handler_ms = int((time.perf_counter() - handler_started) * 1000)
            if handler_ms >= 100:
                self.slowOperation.emit(f"{kind}:界面", handler_ms)
            total_ms = int((time.perf_counter() - started) * 1000)
            if total_ms >= 300:
                self.slowOperation.emit(f"{kind}:合计", total_ms)
        except Exception as exc:
            self.errorOccurred.emit(kind, str(exc))

    def _drop_stale(self) -> None:
        """放弃迟迟不回来的操作，别让界面永远停在「处理中」。

        没有用定时器：那会在窗口对象销毁之后还触发，直接崩。改成每次提交前
        顺手清一次——「点了没反应」的那一刻，正是需要它的时候。
        """
        now = time.perf_counter()
        stale = [
            (request_id, request)
            for request_id, request in self._requests.items()
            # Conversation tasks run until completion or explicit stop; short
            # graph-refresh reads must never discard their terminal result.
            if request[0] not in {"assistant", "static_check"}
            and now - request[4] > self.STALE_AFTER_SECONDS
        ]
        if not stale:
            return
        for request_id, _request in stale:
            self._requests.pop(request_id, None)
        if not self._requests:
            self.busyChanged.emit(False)
        kinds = "、".join(sorted({request[0] for _rid, request in stale}))
        self.errorOccurred.emit(
            "timeout", f"{kinds} 超过 {int(self.STALE_AFTER_SECONDS)} 秒没有返回，已放弃这一步"
        )

    def pendingOperations(self) -> list[str]:
        """还没回来的操作（QML 与诊断用）：卡住时它说明卡在哪一步。"""
        return [request[0] for request in self._requests.values()]

    @Slot(result="QVariantList")
    def pendingKinds(self) -> list[str]:
        return self.pendingOperations()

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

        content_form = "" if kind in {"assistant", "screenplay_writing", "source_task", "event_actions_task"} else self._content_forms.current()
        def work() -> dict:
            ops = project_catalog.open_graph_project(path)
            return operation(self._content_forms.proxy(ops, content_form))

        self._submit(kind, work, apply, content_form=content_form,
                     query_epoch=self._query_epoch if kind in {"graph_view", "canvas", "detail", "search", "expand", "timeline", "hierarchy", "reading_hierarchy", "event_compare", "property_context", "property_state", "property_world", "property_history",
                                   "node_properties"} else None)

    @Slot(result=str)
    def newContentFormSession(self) -> str:
        return self._content_forms.token()

    @Slot(str, bool, int)
    def activateContentForm(self, form_id: str, active: bool, priority: int) -> None:
        self._content_forms.activate(form_id, active, priority)

    @Slot(str, str, str)
    def listContentSubmissions(self, operation: str, target_json: str, form_id: str) -> None:
        target = json.loads(target_json or "{}")
        def work(ops):
            result = ops.list_content_submissions(operation=operation or None, target=target or None)
            if operation == "apply_storyboard_changes":
                for row in result["submissions"]:
                    detail = ops.read_content_submission(row["submission_id"], paths=[["changes", 0]])
                    action = (detail.get("values") or [{}])[0].get("value") or {}
                    row["action_kind"] = action.get("op")
                    row["action_fields"] = list(action)
            return result | {"form_id": form_id,
                "project_path": str(project_package.layout(ops.project_info().path).entry)}
        self._project_task("content_submissions", work)

    @Slot(str, str)
    def readContentSubmission(self, submission_id: str, form_id: str) -> None:
        def work(ops):
            result = ops.read_content_submission(submission_id, paths=[[]])
            return result | {"form_id": form_id, "project_path": str(project_package.layout(ops.project_info().path).entry)}
        def apply(result):
            arguments = next((value["value"] for value in result.get("values", []) if not value["path"]), {})
            self._content_forms.restore(form_id, result, arguments)
            return result
        self._project_task("content_submission_read", work, apply)

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
        """Public opening accepts only the private entry information file."""
        text = name_or_path.strip()
        if not text:
            raise ValueError("请选择工程入口文件")
        supplied = Path(text)
        if not supplied.is_absolute():
            raise ValueError("请给出工程入口文件的完整路径（.nproj 文件）")
        resolved = supplied.expanduser()
        if resolved.suffix.lower() != project_package.PACKAGE_SUFFIX or resolved.is_dir():
            raise ValueError("请选择 .nproj 工程入口文件，不是文件夹或数据库")
        if not resolved.is_file():
            raise ValueError("找不到这个 .nproj 工程入口文件")
        return resolved.resolve()

    @staticmethod
    def _project_view(ops: GraphOperations) -> dict:
        info = ops.graph_project_info()
        info = info | {"path": str(project_package.layout(ops.project_info().path).entry)}
        return {
            "project": info,
            "sources": ops.sources(),
            "provider": {
                key: ops.provider_profile().get(key) for key in (
                    "provider", "model", "max_tokens", "context_window", "output_reserve",
                    "compaction_reserve", "compaction_max_tokens",
                )
            },
        }

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
    def closeProject(self) -> None:
        """Return to the launcher: drop the open project and its cached reads."""
        self._epoch += 1
        self._reading_cut = {}
        self._state_cut = ""
        self._project_path = None
        self._canvas.clear()
        self._query_canvas = None
        self._close_assistant()
        self.resultReady.emit("project_closed", {})
        # The launcher stays mounted while a project is open. Refresh its
        # initial snapshot after changing epoch, so it includes this visit.
        self.loadRecentProjects()

    @Slot(str, result=str)
    def urlToPath(self, url: str) -> str:
        """QML 的 FolderDialog 给的是 file:///…，界面要显示成路径。"""
        from PySide6.QtCore import QUrl

        return QUrl(url).toLocalFile() or url

    @Slot(result=str)
    def homeDirectory(self) -> str:
        """新建工程时的默认位置。没有「工程目录」，所以退到用户目录。"""
        return str(Path.home())

    @Slot(str, str)
    def createProjectAt(self, parent_url: str, name: str) -> None:
        """在选定的位置建一个工程包。没有「默认工程目录」这回事。"""
        if not re.fullmatch(r"[\w-]{1,64}", name):
            self.errorOccurred.emit("project", "项目名只能含字母、数字、中文、下划线或短横线")
            return
        from PySide6.QtCore import QUrl

        folder = Path(QUrl(parent_url).toLocalFile() or parent_url)
        if not folder.is_dir():
            self.errorOccurred.emit("project", "请先选一个存在的文件夹")
            return
        path = folder / f"{name}{project_package.PACKAGE_SUFFIX}"
        if path.exists():
            self.errorOccurred.emit("project", "同名项目已经存在")
            return
        self._epoch += 1
        self._reading_cut = {}
        self._state_cut = ""
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
            # 换了工程就是换了话题：旧的会话不该把上一个工程的内容带进来。
            self._close_assistant()
            self._remember_project(path)
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
        self._state_cut = ""
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
            # 换了工程就是换了话题：旧的会话不该把上一个工程的内容带进来。
            self._close_assistant()
            self._remember_project(path)
            return result

        self._submit("project", work, apply, epoch=epoch)

    # -- 最近打开 --------------------------------------------------------
    #
    # 工程可以放在任何地方，「打开工程…」打开的往往不在 projects 目录里。
    # 不记住的话，下次启动列表里就没有它了。

    RECENT_LIMIT = 8

    def _recent_paths(self, *, strict: bool = False) -> list[str]:
        try:
            raw = ProviderStore().get(GLOBAL_SCOPE, "recent_projects")
        except Exception:  # 存储坏了不该连累启动
            if strict:
                raise
            return []
        if not raw:
            return []
        try:
            rows = json.loads(raw)
        except ValueError:
            if strict:
                raise
            return []
        if strict and not isinstance(rows, list):
            raise ValueError("最近工程列表格式无效")
        return [row for row in rows if isinstance(row, str)] if isinstance(rows, list) else []

    def _remember_project(self, path: Path) -> None:
        text = str(path)
        rows = [row for row in self._recent_paths() if row != text]
        rows.insert(0, text)
        try:
            ProviderStore().put(
                GLOBAL_SCOPE,
                "recent_projects",
                json.dumps(rows[: self.RECENT_LIMIT], ensure_ascii=False),
            )
        except Exception:  # 记不住不影响打开
            pass

    @Slot(str)
    def openProjectUrl(self, url: str) -> None:
        """QML 的 FileDialog 给的是 file:///… URL，转成本地路径再打开。"""
        from PySide6.QtCore import QUrl

        local = QUrl(url).toLocalFile()
        self.openProject(local or url)

    @Slot(str, result=str)
    def recentProjectKey(self, path: str) -> str:
        from PySide6.QtCore import QUrl

        return os.path.normcase(str(Path(QUrl(path).toLocalFile() or path).resolve()))

    @Slot()
    def loadRecentProjects(self) -> None:
        """最近打开过的工程，带「还在不在」——搬走或删掉的不该点进去才报错。"""

        self._recent_generation += 1
        generation = self._recent_generation

        def work() -> dict:
            items = []
            for text in self._recent_paths():
                path = Path(self.urlToPath(text))
                name, title, exists, copyable = path.stem, path.stem, False, False
                if path.is_file() and path.suffix.lower() == project_package.PACKAGE_SUFFIX:
                    try:
                        found = project_package.layout(path)
                        exists = found.db.is_file()
                        from .operations import Operations
                        copyable = Operations.inspect_graph_project(path)["status"] == "ready"
                        title = project_package.project(found)["project"]["title"] or name
                    except (OSError, ValueError, KeyError):
                        pass
                items.append(
                    {"path": text, "name": name, "title": title, "exists": exists, "copyable": copyable}
                )
            return {"items": items, "generation": generation}

        self._submit("recent_projects", work)

    @Slot(str)
    def suggestProjectCopy(self, source: str) -> None:
        from PySide6.QtCore import QUrl

        from .operations import Operations

        def work() -> dict:
            path = Operations.next_project_copy_path(self.urlToPath(source))
            return {"source": source, "destination": QUrl.fromLocalFile(str(path)).toString()}

        self._submit("project_copy_destination", work)

    @Slot(str, str)
    def duplicateProject(self, source: str, destination: str) -> None:
        from .operations import Operations

        def work() -> dict:
            target = Path(self.urlToPath(destination))
            copied = Operations.duplicate_graph_project(self.urlToPath(source), target)
            self._remember_project(target)
            return {"path": str(target), "report": getattr(copied, "_copy_report", {})}

        def apply(result: dict) -> dict:
            self.loadRecentProjects()
            return result

        self._submit("project_duplicated", work, apply)

    @Slot(str, bool)
    def removeProject(self, path: str, delete_files: bool) -> None:
        """Forget the entry; explicit deletion only removes its validated owned files."""
        from PySide6.QtCore import QUrl

        target = Path(QUrl(path).toLocalFile() or path).resolve()
        key = self.recentProjectKey(path)
        try:
            rows = [row for row in self._recent_paths(strict=True)
                    if self.recentProjectKey(row) != key]
            ProviderStore().put(
                GLOBAL_SCOPE, "recent_projects", json.dumps(rows, ensure_ascii=False)
            )
        except Exception as exc:
            self.errorOccurred.emit("project", f"无法保存最近工程列表：{exc}")
            return
        self._recent_generation += 1
        if self._project_path is not None and self.recentProjectKey(str(self._project_path)) == key:
            self.closeProject()
        removed: list[str] = []
        retained_data = False
        deletion_error = ""
        if delete_files and target.exists():
            try:
                if not target.is_file() or target.suffix.lower() != project_package.PACKAGE_SUFFIX:
                    raise ValueError("这不是有效的 .nproj 工程入口文件，没有删除")
                found = project_package.layout(target)
                parent = target.parent.resolve()
                directories = list(dict.fromkeys(
                    item.resolve() for item in (found.root, found.extraction, found.chats)
                ))
                owned = [parent / f"{target.stem}{suffix}"
                         for suffix in ("_data", "_extraction", "_chats")]
                shared = False
                other_entries = set(parent.glob("*.nproj")) | {Path(self.urlToPath(row)) for row in rows}
                for other in other_entries:
                    if other.resolve() == target or not other.is_file():
                        continue
                    try:
                        referenced = project_package.layout(other)
                    except (OSError, ValueError):
                        continue
                    references = (referenced.db, referenced.material,
                                  referenced.extraction, referenced.chats)
                    if any(directory == reference.resolve()
                           or directory in reference.resolve().parents
                           or reference.resolve() in directory.parents
                           for directory in directories for reference in references):
                        shared = True
                        break
                if (shared or any(item == parent or item.parent != parent for item in directories)
                    or directories != [item.resolve() for item in owned]
                    or found.material.resolve() != found.root.resolve() / "material"):
                    # An entry may reference shared/external data: only unlink the entry.
                    directories = []
                    retained_data = True
                for directory in directories:
                    if directory.exists():
                        shutil.rmtree(directory)
                        removed.append(str(directory))
                target.unlink()
                removed.append(str(target))
            except (OSError, ValueError) as exc:
                deletion_error = str(exc)
                self.errorOccurred.emit("project", deletion_error)
        self.resultReady.emit("project_removed", {
            "path": str(target), "deleted": removed, "kept": not removed,
            "retained_data": retained_data, "key": key,
            "deletion_error": deletion_error,
        })
        self.loadRecentProjects()

    @Slot(str)
    def forgetRecentProject(self, path: str) -> None:
        self.removeProject(path, False)

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
            view = ops.graph_view(requested | {"view": "table"})
            return {"view": view, "labels": ops.graph_display_map()["items"]}

        def apply(result: dict) -> dict:
            view = result["view"]
            self._query_canvas = view
            self._query_visible = {node["id"] for node in view["nodes"]}
            return {"canvas": view, "labels": result["labels"]}

        self._project_task("graph_view", work, apply)

    @Slot()
    @Slot(str, bool)
    def loadRelations(self, cut: str = "", related_only: bool = False) -> None:
        """人物关系网：人是点、关系是有方向的边，给关系图用。

        `cut` 是阅读位置——只画那一步之前已经知道的关系；`related_only` 再收一层，
        只留跟这一步有关的人。
        """
        from .tool_api import _relation_map

        spec = {"cut": cut, "related_only": related_only}
        self._project_task("relations", lambda ops: _relation_map(ops, spec))

    @Slot("QVariantMap")
    def loadBrowse(self, spec: dict) -> None:
        """One question for the browsing page: nodes, counts, steps and the cut.

        Filtering, counting, the step list and the related-node rule all happen
        in the projection; the page only renders what comes back. The cut is
        resolved there too, so the list and the property pane cannot disagree.
        """
        try:
            self._project()
        except ValueError as exc:
            self.errorOccurred.emit("browse", str(exc))
            return
        self._query_epoch += 1
        epoch = self._query_epoch
        requested = dict(spec)

        def work() -> dict:
            ops = project_catalog.open_graph_project(self._project())
            return ops.browse_view(
                granularity=str(requested.get("granularity") or "scene"),
                cut_id=str(requested.get("cut_id") or ""),
                node_type=str(requested.get("node_type") or ""),
                query=str(requested.get("query") or ""),
                related_only=bool(requested.get("related_only")),
                time_filter=bool(requested.get("time_filter")),
            )

        def apply(result: dict) -> dict:
            cut = result.get("cut") or {}
            # The property pane cuts at the same anchor event the list uses.
            self._state_cut = str(requested.get("cut_id") or cut.get("event_id") or "")
            return result

        self._submit("browse", work, apply, query_epoch=epoch)

    @Slot(str)
    def loadNodeProperties(self, node_id: str) -> None:
        """Every property of one node, read-only; used by the browsing detail pane."""

        def work(ops) -> dict:
            return ops.property_node_summary(node_id, cut=self._state_cut or None)

        self._project_task("node_properties", work)

    @Slot()
    def loadPropertyCategories(self) -> None:
        """The display vocabulary per node type, one list for each type."""

        def work(ops) -> dict:
            return {"vocabulary": ops.property_categories()}

        self._project_task("property_categories", work)

    @Slot(str)
    def savePropertyCategories(self, config_json: str) -> None:
        """Replace the vocabulary. Existing records keep their own category."""
        try:
            config = json.loads(config_json)
        except json.JSONDecodeError as exc:
            self.errorOccurred.emit("property_categories", f"分类配置 JSON 无效：{exc.msg}")
            return

        def work(ops) -> dict:
            result = ops.set_property_categories(config)
            return {
                "vocabulary": result["vocabulary"],
                "saved": True,
                "moved": len(result.get("moved") or []),
            }

        self._project_task("property_categories", work)

    @Slot(str, str)
    def categorizeProperties(self, targets_json: str, category: str) -> None:
        """Move disclosures to a category. Appends a record; nothing is rewritten."""
        try:
            targets = json.loads(targets_json)
        except json.JSONDecodeError as exc:
            self.errorOccurred.emit("property_categorize", f"记录 ID 列表无效：{exc.msg}")
            return
        if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
            self.errorOccurred.emit("property_categorize", "请提供记录 ID 数组")
            return

        def work(ops) -> dict:
            return ops.property_categorize([str(item) for item in targets], category)

        self._project_task("property_categorize", work)

    @Slot(str, str)
    def mergeProperties(self, source_id: str, target_id: str) -> None:
        """Physically merge definitions and their references."""
        self._project_task("property_merge", lambda ops: ops.merge_properties(
            source_id, target_id, "用户合并属性", ops.graph_project_info()["revision"],
        ) | {"target_id": target_id})

    @Slot(str, str)
    def renameProperty(self, from_id: str, to_id: str) -> None:
        """Rename the current definition and update references."""
        self._project_task("property_rename", lambda ops: ops.rename_property(
            from_id, to_id, "用户重命名属性", ops.graph_project_info()["revision"],
        ) | {"to_id": to_id})

    @Slot("QVariantMap")
    def addPropertyRecord(self, payload: dict) -> None:
        fields = {key: payload[key] for key in ("value", "value_type", "cardinality", "symmetric", "scope", "holder_id",
                  "context_id", "quote", "source_id", "category", "source", "related_nodes") if key in payload and (key not in {"quote", "source_id"} or payload[key])}
        self._project_task("property_add", lambda ops: ops.record_property_change(
            payload.get("node_id"), payload.get("property_id"), payload.get("event_id"), payload.get("op"),
            expected_revision=int(payload["expected_revision"]), reason=payload.get("reason") or "人工记录属性变更", **fields))

    @Slot(str, "QVariantMap")
    def correctPropertyRecord(self, record_id: str, payload: dict) -> None:
        changes = {key: payload[key] for key in ("op", "value", "event_id", "category", "scope", "holder_id", "context_id",
                   "source", "related_nodes", "source_id", "quote") if key in payload and (key not in {"quote", "source_id"} or payload[key])}
        self._project_task("property_correct", lambda ops: {"status": "accepted", **ops.correct_property(
            record_id, changes, payload.get("reason") or "更正属性变更录入错误", expected_revision=int(payload["expected_revision"]))})

    @Slot(str, int)
    def deletePropertyRecords(self, targets_json: str, expected_revision: int) -> None:
        try:
            targets = json.loads(targets_json)
            if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
                raise ValueError("请提供变更 ID 数组")
        except ValueError as exc:
            self.errorOccurred.emit("property_delete", str(exc))
            return
        self._project_task("property_delete", lambda ops: ops.delete_property_records(
            targets, "用户删除错误变更", expected_revision=expected_revision) | {"deleted": targets})

    @Slot(str, int)
    def previewRollback(self, source_id: str, start: int) -> None:
        self._project_task("rollback_preview", lambda ops: ops.rollback_preview(
            source_id, start, ops.graph_project_info()["revision"],
        ))

    @Slot(str, int, int, str)
    def rollbackTo(self, source_id: str, start: int, expected_revision: int, reason: str) -> None:
        def apply(result: dict) -> dict:
            if result.get("status") == "accepted":
                self._canvas.clear()
                self._query_canvas = None
                result = result | {"graph_reset_required": True}
            return result
        self._project_task("rollback", lambda ops: ops.rollback_to(
            source_id, start, expected_revision, reason,
        ), apply)

    @Slot(str)
    def loadNodeEditor(self, node_id: str) -> None:
        """Everything the node editor needs, scoped to the reading position.

        At a cut the editor shows what the reader had met by then and is read-only;
        at 最新 it shows the whole history and can edit.
        """
        cut = self._state_cut or None
        self._project_task("node_editor", lambda ops: ops.node_editor(node_id, cut=cut))

    # -- 节点资料（一张张带生效/失效时间的资料卡）-----------------------

    @Slot()
    def loadAdaptation(self) -> None:
        self._project_task("adaptation", lambda ops: ops.adaptation_snapshot())

    @Slot(str)
    def loadScreenplayWorkspace(self, screenplay_id: str) -> None:
        self._screenplay_query_generation += 1
        generation = self._screenplay_query_generation
        def work(ops):
            storage = ops.inspect_screenplay_storage()
            if storage["status"] == "incomplete":
                return {"storage": storage, "screenplays": [], "adaptation": ops.adaptation_snapshot(),
                        "timeline": {"nodes": [], "revision": 0}, "document": {}}
            items = ops.list_screenplays()
            selected = screenplay_id if any(item["id"] == screenplay_id for item in items) else (items[0]["id"] if items else "")
            return {"storage": storage, "screenplays": items, "adaptation": ops.adaptation_snapshot(),
                    "timeline": ops.read_screenplay_timeline(selected) if selected else {"nodes": [], "revision": 0},
                    "document": ops.read_screenplay(selected) if selected else {}}
        self._project_task("screenplay_workspace", work, lambda result: result if
                           generation == self._screenplay_query_generation else {"superseded": True})

    @Slot(QObject)
    def observeBlockTextDoubleClicks(self, item: QObject) -> None:
        if item.objectName() == "scriptBlockDisplayText" and item.property("readOnly") is True:
            self._block_text_events.observe(item)

    @Slot(str, "QVariantMap")
    def screenplayAction(self, operation: str, arguments: dict) -> None:
        allowed = {
            "preview_adaptation_span", "create_adaptation_span", "update_adaptation_span", "read_adaptation_span",
            "create_screenplay", "read_screenplay", "apply_screenplay_structure",
            "create_script_segment", "update_script_segment", "read_script_segment",
            "read_scene_table_context", "submit_scene_table", "apply_scene_table_changes", "adopt_scene_table",
            "read_script_unit_context", "submit_script_unit", "apply_script_block_changes",
            "mark_writing_basis_current", "check_screenplay", "export_screenplay", "compile_screenplay",
            "load_compile_settings", "save_compile_settings",
            "load_creative_agreement", "save_creative_agreement",
            "prepare_script_writing_targets",
            "read_screenplay_timeline", "import_screenplay_timeline_candidates", "import_screenplay_timeline",
            "append_screenplay_timeline",
            "edit_screenplay_timeline_node", "move_screenplay_timeline_node",
            "copy_screenplay_timeline_node", "delete_screenplay_timeline_node",
            "inspect_screenplay_storage", "reset_screenplay_storage",
        }
        if operation not in allowed:
            self.errorOccurred.emit("screenplay", "未知剧本操作")
            return
        args: dict[str, Any] = json.loads(json.dumps(dict(arguments), ensure_ascii=False))
        request_token = args.pop("_view_request_token", None)
        def normalize(value) -> Any:
            if isinstance(value, dict):
                    return {key: int(item) if (key.startswith("expected_") or key == "position") and item is not None
                        and isinstance(item, (int, float)) and item == int(item) else normalize(item)
                        for key, item in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value
        args = dict(normalize(args))
        self._project_task("screenplay", lambda ops: {
            "operation": operation, "request_screenplay_id": args.get("screenplay_id"),
            "request_selection_ids": args.get("selection_ids"),
            "request_token": request_token,
            "result": getattr(ops, operation)(**args)})

    @Slot(str, result=str)
    def storyboardImageUrl(self, path: str) -> str:
        from PySide6.QtCore import QUrl
        return QUrl.fromLocalFile(path).toString() if path else ""

    @Slot("QVariantMap", str, str, result=str)
    def storyboardPanelPreviewUrl(self, board: dict, shot_id: str, panel_id: str) -> str:
        """Render the already-read board snapshot without changing project state."""
        import base64
        import io

        from .storyboard_panels import render_panel
        try:
            shot = next(row for row in board.get("shots", []) if row["id"] == shot_id)
            panel = next(row for row in shot.get("panels", []) if row["id"] == panel_id)
            output = io.BytesIO()
            render_panel(board, shot, panel, annotated=False).save(output, format="PNG")
            return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        except (ValueError, OSError, KeyError, StopIteration):
            return ""

    @Slot(str, "QVariantMap", int)
    def storyboardAction(self, operation: str, arguments: dict, token: int) -> None:
        """Keep the response scoped to the scene and request that opened it."""
        from .node_material import _as_path

        def normalize(value, key=""):
            if isinstance(value, dict):
                return {k: normalize(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if key in {"sketch_path", "style_path", "output_path", "audio_path", "video_path", "path"} and isinstance(value, str) and value:
                return str(_as_path(value))
            if key in {"revision", "expected_revision", "offset_ms", "duration_ms", "index", "time_ms", "action_start_ms", "fps", "source_in_ms", "in_ms", "out_ms", "start_ms", "end_ms"}:
                if isinstance(value, float) and value.is_integer():
                    return int(value)
            return value

        def work(ops):
            try:
                actions = {
                    "read_storyboard", "check_storyboard", "preview_storyboard", "preview_storyboard_changes", "apply_storyboard_changes",
                    "load_storyboard_camera_presets", "save_storyboard_camera_preset",
                    "read_storyboard_video_settings", "save_storyboard_video_settings", "read_storyboard_video_receipts",
                    "set_storyboard_animation", "render_storyboard_animatic", "attach_storyboard_video", "detach_storyboard_video",
                    "prepare_storyboard_video", "submit_storyboard_video", "refresh_storyboard_video",
                    "generate_storyboard_sketch", "export_storyboard_style_package", "render_storyboard_style",
                    "export_storyboard_script", "export_storyboard_panel", "generate_storyboard_panel", "generate_storyboard_panels",
                }
                if operation not in actions:
                    raise ValueError("未知分镜操作")
                result = getattr(ops, operation)(**normalize(dict(arguments)))
                return {"token": token, "operation": operation, "result": result}
            except (ValueError, OSError, KeyError) as exc:
                return {"token": token, "operation": operation, "error": str(exc)}
            except Exception as exc:
                if operation != "check_storyboard":
                    raise
                # A background diagnostic must never release an unrelated write.
                return {"token": token, "operation": operation, "error": str(exc), "error_type": type(exc).__name__}
        self._project_task("storyboard", work)

    @Slot(str, str, str)
    def saveScreenplayExport(self, screenplay_id: str, export_format: str, path: str) -> None:
        def work(ops):
            from .node_material import _as_path
            destination = _as_path(path)
            return ops.compile_screenplay(screenplay_id, format=export_format, path=str(destination))
        self._project_task("screenplay_export_saved", work)

    @Slot("QVariantMap")
    def loadAdaptationBrowse(self, spec: dict) -> None:
        self._project_task("adaptation_browse", lambda ops: ops.adaptation_browse_view(**dict(spec)))

    @Slot(str, str)
    def loadAdaptationDetail(self, node_id: str, cut: str) -> None:
        self._project_task("adaptation_detail", lambda ops: ops.adaptation_node_detail(node_id, cut=cut or None))

    @Slot(str, str)
    def loadAdaptationProperties(self, node_id: str, cut: str) -> None:
        self._project_task("adaptation_node_properties", lambda ops: ops.adaptation_node_properties(node_id, cut=cut or None))

    @Slot(str, str)
    def loadAdaptationEditor(self, node_id: str, cut: str) -> None:
        self._project_task("adaptation_node_editor", lambda ops: ops.adaptation_node_editor(node_id, cut=cut or None))

    @Slot(str, str)
    def loadAdaptationMaterial(self, node_id: str, cut: str) -> None:
        self._project_task("adaptation_material", lambda ops: ops.adaptation_node_assets(node_id, cut=cut or None))

    @Slot()
    def loadAdaptationImportCandidates(self) -> None:
        self._project_task("adaptation_import_candidates", lambda ops: {"moments": ops.import_adaptation_candidates()})

    @Slot("QVariantList", int)
    def importAdaptation(self, moment_ids: list, revision: int) -> None:
        self._project_task("adaptation", lambda ops: ops.import_adaptation(
            moment_ids, expected_revision=revision, reason="从提取导入"))

    @Slot(str, "QVariantMap")
    def changeAdaptation(self, action: str, arguments: dict) -> None:
        methods = {
            "sync": "sync_adaptation", "edit": "edit_adaptation_node",
            "copy": "copy_adaptation_node", "move": "move_adaptation_node",
            "suppress": "suppress_adaptation_node", "property": "set_adaptation_property",
            "delete_property_changes": "delete_adaptation_property_changes",
        }
        if action not in methods:
            self.errorOccurred.emit("adaptation", "未知改编操作")
            return
        args = dict(arguments)
        if args.get("expected_revision") is not None:
            args["expected_revision"] = int(args["expected_revision"])
        self._project_task("adaptation", lambda ops: getattr(ops, methods[action])(**args))

    @Slot()
    def checkAdaptation(self) -> None:
        self._project_task("adaptation_check", lambda ops: ops.check_adaptation())

    @Slot(str, str, str)
    def loadAssets(self, category: str, moment: str, node_id: str) -> None:
        def work(ops):
            result = ops.list_assets(category, moment=moment, node_id=node_id)
            return result | {"moments": ops.asset_moments(),
                             "nodes": ops.adaptation_snapshot().get("nodes", [])}
        self._project_task("assets", work)

    @Slot("QVariantMap", int)
    def saveAsset(self, payload: dict, revision: int) -> None:
        self._project_task("asset_saved", lambda ops: {
            "asset": ops.save_asset(dict(payload), expected_revision=revision)})

    @Slot(str)
    def loadAsset(self, asset_id: str) -> None:
        self._project_task("asset_detail", lambda ops: {"asset": ops.get_asset(asset_id)})

    @Slot(str, int)
    def deleteAsset(self, asset_id: str, revision: int) -> None:
        self._project_task("asset_saved", lambda ops: ops.delete_asset(
            asset_id, expected_revision=revision))

    def _material_moments(self, ops) -> dict:
        """生效/失效下拉用的分级阅读位置：一列一层（大事件 → 场景 → 事件）。

        层级表在 `browse_view.MOMENT_LEVELS`，父子关系看谁把谁列在 `events` 里。
        带上当前阅读位置和属性取值：大事件的「走到哪了」
        由已经读过的场景那句话攒出来——同一份数据，读者停在哪就显示到哪。
        """
        return moment_tree(
            ops.graph_snapshot(),
            cut_event_id=self._state_cut,
            records=ops.property_records(),
        )

    def _material_payload(self, ops, node_id: str) -> dict:
        return ops.material_view(node_id, cut=self._state_cut) | {
            "moments": self._material_moments(ops)
        }

    @staticmethod
    def _images_from_json(text: str) -> list[dict]:
        """编辑器传上来的图片清单：已有的带 id，新加的带 path。"""
        raw = (text or "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"图片清单不是合法 JSON：{exc.msg}") from exc
        if not isinstance(parsed, list):
            raise ValueError("图片清单应该是一个数组")
        return [item for item in parsed if isinstance(item, dict)]

    def _material_task(self, node_id: str, change) -> None:
        """改一次资料，然后把这一刻的整页发回去：界面只认一种 payload。"""

        def work(ops) -> dict:
            changed = change(ops)
            if isinstance(changed, dict) and changed.get("status") == "staged":
                return changed
            payload = self._material_payload(ops, node_id)
            if isinstance(changed, dict) and changed.get("submission_id"):
                for key in ("status", "submission_id", "submission_revision", "operation", "target", "base_revisions", "validation_status", "problems"):
                    if key in changed:
                        payload[key] = changed[key]
            # 刚改的那张如果不在当前阅读位置上，界面要说一声——否则像是没保存。
            if isinstance(changed, dict) and changed.get("id"):
                payload["changed_id"] = changed["id"]
                payload["changed_visible"] = holds_at(changed, self._state_cut, snapshot=ops.graph_snapshot())
            return payload

        self._project_task("material", work)

    @Slot(str)
    def loadMaterial(self, node_id: str) -> None:
        """这个节点在当前阅读位置上有效的资料卡。"""

        def work(ops) -> dict:
            return self._material_payload(ops, node_id)

        self._project_task("material", work)

    @Slot(str, str, str, str, str, str)
    def addMaterialCard(
        self,
        node_id: str,
        title: str,
        detail: str,
        valid_from: str,
        valid_to: str,
        images_json: str,
    ) -> None:
        images = self._images_from_json(images_json)
        self._material_task(
            node_id,
            lambda ops: ops.material_add_card(
                node_id, title, detail, valid_from, valid_to, images
            ),
        )

    @Slot(str, str, str, str, str, str, str)
    def updateMaterialCard(
        self,
        node_id: str,
        card_id: str,
        title: str,
        detail: str,
        valid_from: str,
        valid_to: str,
        images_json: str,
    ) -> None:
        images = self._images_from_json(images_json)
        self._material_task(
            node_id,
            lambda ops: ops.material_update_card(
                node_id,
                card_id,
                title=title,
                detail=detail,
                valid_from=valid_from,
                valid_to=valid_to,
                images=images,
            ),
        )

    @Slot(str, str)
    def removeMaterialCard(self, node_id: str, card_id: str) -> None:
        self._material_task(node_id, lambda ops: ops.material_remove_card(node_id, card_id))

    @Slot(str, str, str, str)
    def addMaterialImage(self, node_id: str, card_id: str, path: str, caption: str) -> None:
        self._material_task(
            node_id, lambda ops: ops.material_add_image(node_id, card_id, Path(path), caption)
        )

    @Slot(str, str, str, str)
    def updateMaterialImageCaption(
        self, node_id: str, card_id: str, image_id: str, caption: str
    ) -> None:
        self._material_task(
            node_id,
            lambda ops: ops.material_update_image_caption(node_id, card_id, image_id, caption),
        )

    @Slot(str, str, str)
    def removeMaterialImage(self, node_id: str, card_id: str, image_id: str) -> None:
        self._material_task(
            node_id, lambda ops: ops.material_remove_image(node_id, card_id, image_id)
        )

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
            from .graph_event_order import _storyline_spans

            detail = node_detail(
                ops.graph_query(node_id=node_id, limit=2000, disclosure_cut=cut or None), node_id,
                ops.graph_display_map()["items"], ops.sources(),
            )
            # 大事件是一段：把它的起止事件附上，页面上才看得出它串了哪几场。
            snapshot = ops.graph_snapshot()
            spans = {row["id"]: row for row in _storyline_spans(
                snapshot, "canon", cut or None
            )}
            span = spans.get(node_id)
            detail["span"] = span
            return detail

        self._project_task("detail", work)

    # -- 常驻协作者 ------------------------------------------------------

    def _close_assistant(self) -> None:
        with self._assistant_lock:
            for job in self._assistant_jobs.values():
                job["stop_reason"] = "project_cancelled"
                job["cancel"].set()
            self._assistant_jobs.clear()
            runtime = self._assistant_runtime
            self._assistant_runtime = None
            self._assistant_runner = None
            self._assistant_tool_host = None
            self._assistant_steps = []
            self._assistant_resumed = set()
            self._assistant_chat = ""
        if runtime is not None:
            runtime.close("project_closed")

    def _attach_conversation_runtime(self, ops, settings, api_key, expected_path):
        from .conversation_runtime import get_runtime

        runtime = get_runtime(ops, settings, api_key, runner_factory=HarnessRunner)
        with self._assistant_lock:
            cancelled = self._closed or self._project_path != expected_path
            if not cancelled:
                self._assistant_runtime = runtime
                self._assistant_runner = runtime.runner
                self._assistant_tool_host = runtime.host
                self._assistant_resumed = runtime._resumed
        if cancelled:
            runtime.close("project_closed")
            raise ValueError("工程已切换，任务已停止")
        return runtime

    def _clear_closed_conversation(self, runtime) -> None:
        with self._assistant_lock:
            if self._assistant_runtime is runtime and runtime.closed:
                self._assistant_runtime = None
                self._assistant_runner = None
                self._assistant_tool_host = None
                self._assistant_resumed = set()
                self._assistant_steps = []

    def _assistant_activity_snapshot(self):
        with self._assistant_lock:
            self._assistant_activity_sequence += 1
            return {'active_tasks': {job['chat_id']: job['task_id'] for job in self._assistant_jobs.values()
                                     if job.get('chat_id') and not job.get('finished')},
                    'activity_sequence': self._assistant_activity_sequence,
                    'project_path': str(self._project_path or '')}

    @Slot()
    def loadAssistantActivity(self):
        if not self._closed:
            self.resultReady.emit('assistant_activity', self._assistant_activity_snapshot())

    def _conversation_event(self, ops, expected_path, event: dict, *, busy=True):
        if self._closed or self._project_path != expected_path:
            return
        if event.get("event") == "usage_update":
            statistics = event.get("usage_statistics")
            if isinstance(statistics, dict):
                self.resultReady.emit("assistant_usage", {
                    **statistics,
                    "project_path": str(project_package.layout(expected_path).entry),
                })
            return
        store = self._chats(ops)
        chat_id = event.get("chat_id")
        if not chat_id:
            chat = store.active(create=False)
            chat_id = chat["id"] if chat else ""
        if not chat_id:
            return
        self.resultReady.emit("conversation_progress", {
            **event, **self._assistant_activity_snapshot(), "chat_id": chat_id, "chat": store.load(chat_id), "items": store.list(),
            "current": self._assistant_chat, "project_path": str(project_package.layout(expected_path).entry),
            "busy": busy,
        })

    def _chats(self, ops):
        from .chat_store import ChatStore

        return ChatStore(Path(ops.project_info().path))

    @Slot(str, str)
    def loadUsageStatistics(self, chat_id: str, task_id: str = "") -> None:
        if self._closed or self._project_path is None or not chat_id:
            return
        def work(ops) -> dict:
            return {
                **ops.read_usage_statistics(chat_id=chat_id or None, task_id=task_id or None),
                "project_path": str(project_package.layout(ops.project_info().path).entry),
            }

        self._project_task("assistant_usage", work)

    @Slot(str)
    def loadChatAccess(self, chat_id: str) -> None:
        if self._closed or self._project_path is None:
            return
        def work(ops):
            identity = {"operation": "read", "chat_id": chat_id or None, "project_path": str(project_package.layout(ops.project_info().path).entry)}
            try:
                return ops.read_assistant_access(chat_id or None) | identity
            except (ValueError, OSError) as exc:
                return identity | {"error": str(exc)}
        self._project_task("chat_access", work)

    @Slot(str, str)
    def setChatAccessMode(self, chat_id: str, access_mode: str) -> None:
        if self._closed or self._project_path is None:
            return
        def work(ops):
            identity = {"operation": "set", "chat_id": chat_id, "project_path": str(project_package.layout(ops.project_info().path).entry)}
            try:
                return ops.set_assistant_access(chat_id, access_mode) | identity
            except (ValueError, OSError) as exc:
                return identity | {"error": str(exc)}
        self._project_task("chat_access", work)

    @Slot()
    def loadChats(self) -> None:
        """会话清单：最近说过的排在前面，外加「现在是哪一个」。"""

        def work(ops) -> dict:
            store = self._chats(ops)
            chat = store.active(create=False)
            self._assistant_chat = chat["id"] if chat else ""
            return {"items": store.list(), "current": self._assistant_chat}

        self._project_task("chats", work)

    @Slot(str)
    def openChat(self, chat_id: str) -> None:
        def work(ops) -> dict:
            chat = self._chats(ops).set_active(chat_id)
            self._assistant_chat = chat["id"]
            return {"chat": chat, "items": self._chats(ops).list()}

        self._project_task("chat", work)

    @Slot()
    def newChat(self) -> None:
        def work(ops) -> dict:
            store = self._chats(ops)
            chat = store.create()
            self._assistant_chat = chat["id"]
            return {"chat": chat, "items": store.list()}

        self._project_task("chat", work)

    @Slot(str, str)
    def renameChat(self, chat_id: str, title: str) -> None:
        def work(ops) -> dict:
            store = self._chats(ops)
            chat = store.rename(chat_id, title)
            return {"chat": chat, "items": store.list()}

        self._project_task("chat", work)

    @Slot(str)
    def deleteChat(self, chat_id: str) -> None:
        def work(ops) -> dict:
            store = self._chats(ops)
            store.delete(chat_id)
            active = store.active(create=False)
            self._assistant_chat = active["id"] if active else ""
            items = store.list()
            chat = active or {"id": "", "session": "", "title": "新会话", "messages": []}
            return {"chat": chat, "items": items, "removed": chat_id}

        self._project_task("chat", work)

    def _capture_assistant_access(self, requested_chat):
        ops = project_catalog.open_graph_project(self._project())
        chat = self._chats(ops).select(requested_chat, activate=False)
        if chat is None:
            raise ValueError("无法选择工程会话")
        return chat["id"], ops.read_assistant_access(chat["id"])["access_mode"]

    @Slot(str, str)
    def assistantTurn(self, text: str, image_path: str = "") -> None:
        self._assistant_turn(text, image_path, self._assistant_chat)

    @staticmethod
    def _attachment_paths(attachments_json):
        paths = json.loads(attachments_json or '[]')
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ValueError('附件须为本机文件路径数组')
        return list(dict.fromkeys(paths))

    @Slot(str, str)
    def assistantTurnWithAttachments(self, text: str, attachments_json: str = '[]') -> None:
        try:
            paths = self._attachment_paths(attachments_json)
        except (ValueError, TypeError) as error:
            self.errorOccurred.emit('assistant', str(error))
            return
        self._assistant_turn(text, '', self._assistant_chat, attachment_paths=paths)

    @Slot(str, str, str, str)
    def assistantSteer(self, text: str, attachments_json: str, chat_id: str, task_id: str) -> None:
        try:
            path = self._project()
            paths = self._attachment_paths(attachments_json)
            message = (text or '').strip()
            if not message and not paths:
                raise ValueError('请提供插话文字或附件')
            with self._assistant_lock:
                job = next((item for item in self._assistant_jobs.values()
                            if item.get('chat_id') == chat_id and item.get('task_id') == task_id and not item['cancel'].is_set() and not item.get('finished')), None)
                if job is None or job.get('runtime') is None or not job.get('binding_nonce'):
                    raise ValueError('目标任务已结束或尚未就绪，插话未发送；不会另开任务')
                runtime, nonce = job['runtime'], job['binding_nonce']
        except (ValueError, TypeError) as error:
            self.resultReady.emit('assistant_steer', {'chat_id': chat_id, 'task_id': task_id, 'status': 'not_delivered',
                                                    'reason': str(error), 'project_path': str(self._project_path or '')})
            return
        message = message or '补充本条附件。'
        message_id = f'steer-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}'
        def work(ops):
            from .assistant import build_attachment_blocks
            from .conversation_steering import pack_instruction
            store = self._chats(ops)
            attachments = ops.import_chat_attachments(chat_id, paths, task_id=task_id)['attachments'] if paths else []
            store.append(chat_id, 'user', message, message_id=message_id, task_id=task_id,
                         kind='user_steering', steering_status='sending', attachments=attachments)
            audit_directory = project_package.layout(path).extraction / 'assistant' / 'tasks' / task_id / 'steering'
            audit_directory.mkdir(parents=True, exist_ok=True)
            state_lock = threading.RLock()
            def receipt(event):
                status = event.get('status', 'not_delivered')
                with state_lock:
                    row = store.annotate_steering(chat_id, message_id, status, steering_reason=event.get('reason', ''))
                    record = {'message_id': message_id, 'chat_id': chat_id, 'task_id': task_id, 'text': message,
                              'attachments': attachments, 'status': row['steering_status'], 'reason': row.get('steering_reason', '')}
                    temporary = audit_directory / (message_id+'.tmp')
                    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
                    temporary.replace(audit_directory / (message_id+'.json'))
                self._conversation_event(ops, path, {'event': 'steering_status', **record})
                if not self._closed and self._project_path == path:
                    self.resultReady.emit('assistant_steer', {**record, 'project_path': str(path)})
            try:
                if self._closed or self._project_path != path or job['cancel'].is_set():
                    raise ValueError('目标任务已结束或工程已切换，附件已入档但插话未送达')
                blocks = pack_instruction(build_attachment_blocks(message, attachments))
                result = runtime.steer(chat_id, task_id, nonce, blocks, message_id=message_id, on_status=receipt)
                receipt(result | {'status': result.get('status', 'queued')})
                return {**result, 'chat_id': chat_id, 'task_id': task_id, 'message_id': message_id,
                        'attachments': attachments, 'project_path': str(path)}
            except Exception as error:
                receipt({'status': 'not_delivered', 'reason': str(error)})
                return {'chat_id': chat_id, 'task_id': task_id, 'message_id': message_id, 'status': 'not_delivered',
                        'reason': str(error), 'attachments': attachments, 'project_path': str(path)}
        self._project_task('assistant_steer', work)

    @Slot(str, "QVariantMap")
    def screenplayWritingTurn(self, text: str, target: dict) -> None:
        try:
            requested_chat, access_mode = self._capture_assistant_access(self._assistant_chat)
        except (ValueError, OSError) as exc:
            self.errorOccurred.emit("screenplay_writing", str(exc))
            return
        frozen_target = json.loads(json.dumps(dict(target), ensure_ascii=False))

        def apply(task):
            self._assistant_turn(text, "", requested_chat, writing_task=task, access_mode_override=access_mode)
            with self._assistant_lock:
                task_id = self._assistant_jobs.get(self._next_request, {}).get("task_id", "")
            return {"target": task.get("target", frozen_target), "started": True, "task_id": task_id}

        self._project_task("screenplay_writing", lambda ops: ops.prepare_script_writing_task(
            frozen_target, prompt=text), apply)

    def _assistant_turn(self, text: str, image_path: str, requested_chat: str | None,
                        *, writing_task: dict | None = None, access_mode_override: str | None = None, attachment_paths: list[str] | None = None) -> None:
        """Run an assistant turn with the tools in assistant.ASSISTANT_TOOLS.

        Native tools create source slices, plan exact pattern ranges, and
        precheck and automatically submit extraction batches through Operations.
        Chats persist on disk.
        """
        from .assistant import ASSISTANT_VERSION, _call, _public, run_turn, tool_catalog
        from .conversation_session import (
            ConversationCancelled,
            open_conversation_session,
            publish_event,
            use_chat,
        )

        paths = list(attachment_paths or [])
        if str(image_path or "").strip():
            paths.append(str(image_path).strip())
        message = (text or "").strip() or ("请查看本条附件。" if paths else "")
        if not message:
            self.errorOccurred.emit("assistant", "先说一句想问什么")
            return
        try:
            path = self._project()
        except ValueError as exc:
            self.errorOccurred.emit("assistant", str(exc))
            return
        image = str(image_path or "").strip()
        try:
            if access_mode_override is None:
                requested_chat, access_mode_override = self._capture_assistant_access(requested_chat)
            if access_mode_override not in {"standard", "full"}:
                raise ValueError("访问模式须为standard或full")
        except (ValueError, OSError) as exc:
            self.errorOccurred.emit("assistant", str(exc))
            return

        job = {"chat_id": requested_chat, "task_id": f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}", "runtime": None,
               "cancel": threading.Event(), "stop_reason": "user_stopped"}

        def check_cancelled(metadata=None):
            if job.get("control_error"):
                raise RuntimeError("停止控制通道失败，运行时已关闭：" + job["control_error"])
            if job["cancel"].is_set():
                raise ConversationCancelled(metadata, reason=job["stop_reason"])

        def work(ops) -> dict:
            def observer(event):
                if not job["cancel"].is_set():
                    self._conversation_event(ops, path, event | {"access_mode": job.get("access_mode", "standard")})
            with self._assistant_task_lock, use_chat(ops, requested_chat, on_event=observer) as selected:
                if self._closed or self._project_path != path:
                    raise ValueError("工程已切换，协作者任务已停止")
                started = time.monotonic()
                starting_revision = ops.graph_project_info()["revision"]
                store = self._chats(ops)
                chat_id = selected["id"]
                access_mode = access_mode_override
                job["access_mode"] = access_mode
                if not self._assistant_chat or self._assistant_chat == requested_chat:
                    self._assistant_chat = chat_id
                attachments = ops.import_chat_attachments(chat_id, paths, task_id=job["task_id"])["attachments"] if paths else []
                store.append(chat_id, "user", message, attachments=attachments, task_id=job["task_id"])
                task_id = job["task_id"]
                with self._assistant_lock:
                    job["chat_id"] = chat_id
                publish_event(ops, {"event": "assistant_started", "chat_id": chat_id, "task_id": task_id, "access_mode": access_mode})
                extraction_granularity = ops.load_project_settings()["extraction_granularity"]
                settings, api_key, workdir = harness_env(ops)
                runtime = self._attach_conversation_runtime(ops, settings, api_key, path)
                with self._assistant_lock:
                    job.update(chat_id=chat_id, runtime=runtime)
                entry = project_package.layout(path).entry
                task_id = job["task_id"]
                with self._assistant_lock:
                    job["task_id"] = task_id
                audit_dir = workdir / "assistant" / "tasks" / task_id
                audit_dir.mkdir(parents=True)
                audit = {"version": ASSISTANT_VERSION, "task_id": task_id,
                         "chat_id": chat_id, "status": "running", "calls": [], "access_mode": access_mode, "attachments": attachments,
                         "extraction_granularity": extraction_granularity,
                         "config": settings.model_dump(exclude={"key_env"}),
                         "response_idle_timeout_seconds": settings.timeout_seconds,
                         "runtime_restart": runtime.restart_reason, "prompt_config": runtime._prompt_config,
                         "project_style": writing_task["project_style"] if writing_task is not None else ops.load_project_style()}
                if writing_task is not None:
                    audit["writing_task"] = writing_task
                    audit["writing_prompt_version"] = SCREENPLAY_WRITING_PROMPT_VERSION
                    audit["writing_task_metadata"] = {
                        "task_id": task_id, "manifest_ref": str(audit_dir / "writing-task.json"),
                    }
                native_steps: list[dict] = []
                self._assistant_steps = native_steps
                calls = 0
                outcome: dict | None = None
                model_message_ids: list[str] = []
                message_lock = threading.RLock()

                def model_message(event):
                    from .assistant_audit import sanitize_host_audit
                    event = sanitize_host_audit(event)
                    message_id = str(event.get("message_id") or "")
                    if not message_id:
                        return
                    with message_lock:
                        if message_id in model_message_ids:
                            return
                        store.append(
                            chat_id, "assistant", event.get("text") or "",
                            kind="model_output", sdk_turn=True, task_id=task_id,
                            message_id=message_id, full_output=event.get("full_output"),
                            display_kind=event.get("display_kind", "answer"),
                            content=event.get("content"), seq=event.get("seq"),
                        )
                        model_message_ids.append(message_id)
                    if not job["cancel"].is_set():
                        self._conversation_event(ops, path, {
                            "event": "model_message", "chat_id": chat_id,
                            "task_id": task_id, "message_id": message_id,
                        })

                session_runner = None
                host = runtime.host
                if host is None:
                    raise ValueError("会话工具服务未就绪")
                task_secrets = [api_key, host.token]

                def redact(value: str) -> str:
                    for secret in task_secrets:
                        if secret:
                            value = value.replace(secret, "[REDACTED]")
                    return value

                def save(name, value):
                    from .assistant_audit import sanitize_host_audit
                    value = sanitize_host_audit(value)
                    (audit_dir / name).write_text(
                        redact(json.dumps(value, ensure_ascii=False, indent=2)), encoding="utf-8",
                    )

                native_call_lock = threading.Lock()
                submitted_unit_ids: list[str] = []

                def native_call(name, args):
                    with native_call_lock:
                        check_cancelled()
                        effective_name, effective_args = name, args
                        from .tool_fingerprint_refs import decode_tool_parameters
                        try:
                            if name == "patch_content_submission":
                                decoded = decode_tool_parameters(ops, args)
                                if not isinstance(decoded.get("submission_id"), str) or not isinstance(decoded.get("patches"), list):
                                    raise ValueError("请提供暂存编号和字段修补数组")
                                candidate = ops.preview_content_patch(decoded.get("submission_id"), decoded.get("expected_revision"), decoded.get("patches"))
                                effective_name, effective_args = candidate["operation"], candidate["arguments"]
                            if writing_task is not None and access_mode == "standard":
                                from .screenplay_writing import validate_script_writing_tool_call
                                validate_script_writing_tool_call(writing_task, effective_name, decode_tool_parameters(ops, effective_args), ops=ops)
                        except ValueError as exc:
                            native_steps.append({"tool": name, "args": args, "ok": False,
                                                 "error": str(exc), "diagnostic": "writing_target_scope"})
                            raise
                        from .tool_execution_context import bind_tool_execution_metadata
                        task_metadata = (audit.get("writing_task_metadata") if access_mode == "standard" else None) or {
                            "task_id": task_id, "manifest_ref": str(audit_dir / "task.json")}
                        with bind_tool_execution_metadata(task_metadata):
                            step = _call(entry, name, args, access_mode=access_mode)
                        from .assistant_audit import sanitize_host_audit
                        step = sanitize_host_audit(step)
                        native_steps.append(step)
                        self.resultReady.emit("conversation_progress", {
                            "event": "tool_complete", "chat_id": chat_id, "task_id": task_id, "access_mode": access_mode,
                            "project_path": str(entry), "current": self._assistant_chat, "busy": True,
                            "step_index": len(native_steps) - 1, "step": _public([step])[0],
                        })
                        if not step["ok"]:
                            raise ValueError(step["error"])
                        committed = isinstance(step["result"], dict) and step["result"].get("status") in {"accepted", "unchanged"}
                        if committed and effective_name in {"apply_storyboard_changes", "generate_storyboard_sketch", "generate_storyboard_panel", "generate_storyboard_panels", "render_storyboard_style",
                                    "set_storyboard_animation", "attach_storyboard_video", "detach_storyboard_video",
                                    "prepare_storyboard_video", "submit_storyboard_video", "refresh_storyboard_video"}:
                            self._conversation_event(ops, path, {
                                "event": "storyboard_submission", "chat_id": chat_id, "task_id": task_id,
                                "unit_id": effective_args.get("unit_id"), "board": step["result"],
                            })
                        if committed and writing_task is not None and effective_name in {"submit_script_unit", "apply_script_block_changes"}:
                            unit_id = str(effective_args.get("unit_id") or "")
                            if unit_id and unit_id not in submitted_unit_ids:
                                submitted_unit_ids.append(unit_id)
                            self._conversation_event(ops, path, {
                                "event": "screenplay_submission", "chat_id": chat_id, "task_id": job["task_id"],
                                "writing_target": writing_task.get("target"), "writing_status": "running",
                                "submitted_unit_ids": list(submitted_unit_ids),
                            })
                        return step["result"]

                save("task.json", audit)
                if writing_task is not None:
                    save("writing-task.json", {"version": SCREENPLAY_WRITING_PROMPT_VERSION,
                         "task": writing_task, "metadata": audit["writing_task_metadata"]})

                try:
                    check_cancelled()
                    task_catalog = tool_catalog(access_mode=access_mode)
                    audit["task_tool_names"] = [tool["name"] for tool in task_catalog]
                    session_runner, session = open_conversation_session(
                        ops, settings, api_key, HarnessRunner,
                        task_tools=task_catalog, tool_handler=native_call,
                        chat_id=chat_id, prompt_config=audit["prompt_config"], task_id=task_id,
                        access_mode=access_mode, cancel_event=job["cancel"],
                        output_presentation="thought" if writing_task is not None else "answer",
                    )
                    audit["session_id"] = session
                    with self._assistant_lock:
                        job["binding_nonce"] = session_runner.binding_nonce

                    def ask(instruction) -> str:
                        nonlocal calls
                        check_cancelled()
                        calls += 1
                        save(f"prompt-{calls}.json", {"instruction": instruction})
                        try:
                            reply = session_runner.agent_turn(instruction, session, on_message=model_message)
                        except Exception as exc:
                            failed_metadata = getattr(exc, "metadata", None) or session_runner.usage_metadata()
                            save(f"metadata-{calls}.json", failed_metadata)
                            save(f"partial-raw-{calls}.json", {"responses": session_runner.partial_raw()})
                            audit["calls"].append(failed_metadata)
                            save("task.json", audit)
                            check_cancelled(failed_metadata)
                            raise
                        (audit_dir / f"raw-{calls}.txt").write_text(redact(reply.raw), encoding="utf-8")
                        save(f"metadata-{calls}.json", reply.metadata)
                        for event in reply.metadata.get("model_messages") or []:
                            model_message(event)
                        audit["calls"].append(reply.metadata)
                        save("task.json", audit)
                        check_cancelled(reply.metadata)
                        return reply.raw

                    def on_format_error(event):
                        check_cancelled(event.get("metadata"))
                        diagnostic_path = event.get("diagnostic_path") or str(audit_dir / f"metadata-{calls}.json")
                        audit.setdefault("format_repairs", []).append({
                            "attempt": event.get("attempt"), "code": event.get("code"),
                            "diagnostic_path": diagnostic_path,
                            "metadata_path": str(audit_dir / f"metadata-{calls}.json"),
                        })
                        save("task.json", audit)
                        store.append(
                            chat_id, "system", "工具参数格式错误，已反馈同一任务修正。",
                            kind="format_correction", task_id=task_id,
                            diagnostic_path=diagnostic_path, audit_directory=str(audit_dir),
                            error_code=event.get("code"), attempt=event.get("attempt"),
                        )
                        self._conversation_event(ops, path, {
                            "event": "format_correction", "chat_id": chat_id, "task_id": task_id,
                        })

                    result = run_turn(
                        ask, message if writing_task is None else message + "\n\n" + str(writing_task.get("instruction") or "")
                        + ("\n当前页面创作焦点及输入清单（完全访问模式不限制写入范围）：" if access_mode == "full" else "\n固定写作目标及输入清单：") + json.dumps(writing_task, ensure_ascii=False)
                        + "\n任务与依据档案由程序内部关联；请只提供业务内容、对象ID和并发校验参数。"
                        + "\n写作提示版本：" + SCREENPLAY_WRITING_PROMPT_VERSION,
                        project=entry, image="" if attachments else image, attachments=attachments, steps=native_steps,
                        on_format_error=on_format_error,
                        extraction_granularity=extraction_granularity, prompt_config=audit["prompt_config"],
                        project_style=audit["project_style"], task_id=task_id, access_mode=access_mode,
                    )
                    check_cancelled()
                    if model_message_ids:
                        saved = store.annotate_message(
                            chat_id, model_message_ids[-1],
                            steps=result.get("steps") or [], task_version=ASSISTANT_VERSION,
                        )
                    else:
                        saved = store.append(
                            chat_id, "assistant", result.get("answer") or "",
                            steps=result.get("steps") or [], task_id=task_id,
                            task_version=ASSISTANT_VERSION, sdk_turn=True,
                        )
                    audit["status"] = "completed"
                    revision = ops.graph_project_info()["revision"]
                    result.update(revision=revision, graph_reset_required=revision != starting_revision)
                    outcome = result | {"chat_id": chat_id, "title": saved["title"], "chat": saved,
                                        "items": store.list(), "task_id": task_id,
                                        "audit_directory": str(audit_dir)}
                    return outcome
                except Exception as exc:
                    if job["cancel"].is_set() and not job.get("control_error") and not isinstance(exc, ConversationCancelled):
                        exc = ConversationCancelled(getattr(exc, "metadata", None), reason=job["stop_reason"])
                    cancelled = isinstance(exc, ConversationCancelled)
                    audit.update(status="cancelled" if cancelled else "incomplete",
                                 error_type=type(exc).__name__, error=redact(str(exc)))
                    if not cancelled or session_runner is not None and not session_runner.turn_settled or not runtime.healthy():
                        if session_runner is not None:
                            session_runner.cancel()
                        else:
                            runtime.close("task_failed")
                    self._clear_closed_conversation(runtime)
                    # An already running local operation may finish atomically.
                    # Wait for it before reporting the actual retained revision.
                    with native_call_lock:
                        pass
                    failure_metadata = getattr(exc, "metadata", None) or {}
                    diagnostic_path = getattr(exc, "diagnostic_path", "") or str(audit_dir / "task.json")
                    reason = redact(str(exc))
                    if getattr(exc, "code", "") == "tool_input_invalid_json" and "提取" in message:
                        reason = "模型工具参数 JSON 格式错误，本次提取未完成"
                    revision = ops.graph_project_info()["revision"]
                    state = "实际已提交内容见程序入库汇总。"
                    if not cancelled:
                        state += "本次任务未全部完成。"
                    audit.update(finish_reason=failure_metadata.get("finish_reason"),
                                 sdk_error_diagnostics=failure_metadata.get("sdk_error_diagnostics") or [],
                                 diagnostic_path=diagnostic_path, starting_revision=starting_revision,
                                 final_revision=revision)
                    store.append(chat_id, "system", reason + "。" + state,
                                 kind="assistant_cancelled" if cancelled else "assistant_incomplete", task_id=task_id,
                                 diagnostic_path=diagnostic_path, audit_directory=str(audit_dir),
                                 steps=_public(native_steps),
                                 error_code=getattr(exc, "code", type(exc).__name__),
                                 finish_reason=failure_metadata.get("finish_reason"),
                                 revision=revision, starting_revision=starting_revision)
                    if cancelled:
                        outcome = {"status": "cancelled", "chat_id": chat_id, "task_id": task_id,
                                   "chat": store.load(chat_id), "items": store.list(), "revision": revision,
                                   "graph_reset_required": revision != starting_revision,
                                   "audit_directory": str(audit_dir)}
                        return outcome
                    raise ValueError(reason + "。" + state + "诊断：" + diagnostic_path) from exc
                finally:
                    host_cleanup = None
                    if session_runner is not None:
                        host_cleanup = session_runner.close(completed=audit["status"] == "completed")
                    if not isinstance(host_cleanup, dict):
                        from .assistant_host import read_host_task_cleanup
                        host_cleanup = read_host_task_cleanup(path, task_id)
                    audit['host_cleanup'] = host_cleanup
                    unfinished = [row for row in host_cleanup.get('cleanup_receipts', [])
                                  if row.get('reason') == 'task_completed_before_command_finished']
                    if unfinished and audit['status'] == 'completed':
                        audit['model_finish_reason'] = (audit['calls'][-1].get('finish_reason') if audit['calls'] else None) or 'completed'
                        audit['status'] = 'incomplete'
                        if outcome is not None:
                            outcome.update(status='incomplete', model_finish_reason=audit['model_finish_reason'], host_cleanup=host_cleanup)
                    from .task_write_summary import build_submission_summary
                    with native_call_lock:
                        commit_summary = build_submission_summary(
                            native_steps, task_id=task_id,
                            status="failed" if audit["status"] == "incomplete" else audit["status"],
                            host_cleanup=host_cleanup,
                        )
                    audit.update(commit_summary=commit_summary,
                                 commit_summary_text=commit_summary["text"])
                    store.append(chat_id, "system", commit_summary["text"],
                                 kind="task_summary", task_id=task_id,
                                 commit_summary=commit_summary)
                    if outcome is not None:
                        outcome.update(commit_summary=commit_summary, host_cleanup=host_cleanup,
                                       commit_summary_text=commit_summary["text"],
                                       chat=store.load(chat_id), items=store.list())
                    audit.update(steps=native_steps, public_steps=_public(native_steps),
                                 model_calls=calls, elapsed_seconds=time.monotonic() - started)
                    if writing_task is not None:
                        audit["submitted_unit_ids"] = list(submitted_unit_ids)
                    save("task.json", audit)
                    with self._assistant_lock:
                        job["finished"] = True
                    self._conversation_event(ops, path, {
                        "chat_id": chat_id, "event": "task_complete", "task_id": task_id, "access_mode": access_mode,
                        "revision": ops.graph_project_info()["revision"],
                        "graph_reset_required": ops.graph_project_info()["revision"] != starting_revision,
                        **({"writing_target": writing_task.get("target"), "writing_status": audit["status"],
                            "submitted_unit_ids": list(submitted_unit_ids)} if writing_task is not None else {}),
                    }, busy=False)

        def apply(result: dict) -> dict:
            if result.get("graph_reset_required"):
                self._canvas.clear(preserve_positions=True)
            return result

        with self._assistant_lock:
            request_id = self._next_request + 1
            self._assistant_jobs[request_id] = job
        self._project_task("assistant", work, apply)

    @Slot(str, str)
    def assistantStop(self, chat_id: str, task_id: str) -> None:
        """Stop only the identified running task, never another chat or queued job."""
        if self._closed:
            return
        with self._assistant_lock:
            job = next((item for item in self._assistant_jobs.values()
                        if item['chat_id'] == chat_id and item['task_id'] == task_id and not item.get('finished')), None)
            current = next((item for item in self._assistant_jobs.values() if item['chat_id'] == chat_id and not item.get('finished')), None)
            duplicate = bool(job and job['cancel'].is_set())
            if job is not None:
                job['stop_reason'] = 'user_stopped'
                job['cancel'].set()
                runtime, nonce = job.get('runtime'), job.get('binding_nonce')
            else:
                runtime, nonce = None, None
        identity = {'chat_id': chat_id, 'task_id': task_id, **self._assistant_activity_snapshot()}
        if job is None:
            self.resultReady.emit('assistant_stop', identity | {'status': 'not_running', 'reason': '目标任务已结束或当前进程没有该任务'})
            if current is None:
                self.resultReady.emit('conversation_progress', identity | {'event': 'task_complete', 'busy': False, 'status': 'not_running'})
            else:
                self.resultReady.emit('conversation_progress', identity | {'event': 'stop_ignored', 'busy': True,
                                      'task_id': current['task_id'], 'access_mode': current.get('access_mode', 'standard')})
            return
        self.resultReady.emit('assistant_stop', identity | {'status': 'stopping'})
        if duplicate:
            return
        if runtime is not None and nonce:
            runtime.signal_stop(nonce)
            def interrupt():
                try:
                    runtime.interrupt(nonce)
                except Exception as exc:
                    job['control_error'] = str(exc)
                    runtime.close('interrupt_failed')
            self._pool.submit(interrupt)

    @Slot()
    def assistantReset(self) -> None:
        """换个话题：开一个新会话，旧的那个还在清单里。"""
        self.newChat()

    @Slot(str, "QVariantMap")
    def reviseNode(self, node_id: str, changes: dict) -> None:
        """Save an explicit user correction after deterministic prechecks."""
        allowed = {
            "node_type", "name", "aliases", "identity_note",
            "disclosure",
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
            result = ops.apply_changes([updated], [], revision, f"节点修正：{node['name']}")
            if result["status"] in {"accepted", "unchanged"}:
                self._canvas.clear(preserve_positions=True)
            return result | {"graph_reset_required": result["status"] == "accepted",
                             "node_id": node_id, "changes": payload}

        def apply(result: dict) -> dict:
            if result.get("status") == "accepted":
                self._canvas.clear(preserve_positions=True)
            return result

        self._project_task("node_edit", work, apply)

    @Slot()
    def runStaticCheck(self) -> None:
        self._static_scan_generation += 1
        scan_id = self._static_scan_generation
        try:
            path = self._project()
        except ValueError as exc:
            self.errorOccurred.emit("static_check", str(exc))
            return
        self.resultReady.emit("static_check_started", {"scan_id": scan_id, "project_path": str(path)})

        def work(ops) -> dict:
            try:
                report = ops.check_project()
            except Exception as exc:
                return {"scan_id": scan_id, "failed": True, "error": str(exc), "project_path": str(path)}
            current = ops.graph_project_info()["revision"]
            return report | {"scan_id": scan_id, "current_revision": current,
                             "is_stale": current != report["revision"], "project_path": str(path)}

        self._project_task("static_check", work)

    @Slot()
    def loadStaticCheckRevision(self) -> None:
        self._project_task("static_check_revision", lambda ops: {"revision": ops.graph_project_info()["revision"]})

    @Slot("QVariantMap", str, str)
    def resolveStaticIssue(self, issue: dict, kind: str, record_id: str = "") -> None:
        self._static_target_generation += 1
        generation = self._static_target_generation

        def work(ops) -> dict:
            snapshot = ops.graph_snapshot()
            nodes = {row["id"]: row for row in snapshot["nodes"]}
            record = None
            requested_record = record_id or issue.get("record_id")
            if requested_record:
                record = next((row for row in ops.property_records()
                               if row["id"] == requested_record), None)
            if kind not in {"node", "record", "source"}:
                raise ValueError("未知的检查定位类型")
            if kind == "record":
                node_id = (record or {}).get("node_id")
                target_id = (record or {}).get("target_id")
                if not node_id and target_id:
                    target = next((row for row in ops.property_records()
                                   if row["id"] == target_id), {})
                    node_id = target.get("node_id")
            else:
                node_id = issue.get("node_id") or (record or {}).get("node_id")
            node = nodes.get(node_id, {})
            if kind == "record" or (kind == "source" and record_id):
                evidence = (record or {}).get("evidence") or {}
                disclosure = (record or {}).get("disclosure") or {}
                source_id = evidence.get("source_id") or disclosure.get("source_id")
                start, end = evidence.get("start", disclosure.get("start")), evidence.get("end")
                quote = evidence.get("quote") or ""
            else:
                evidence = (record or {}).get("evidence") or node.get("evidence") or {}
                disclosure = (record or {}).get("disclosure") or node.get("disclosure") or {}
                source_id = issue.get("source_id") or evidence.get("source_id") or disclosure.get("source_id")
                start, end = issue.get("start"), issue.get("end")
                quote = issue.get("quote") or evidence.get("quote") or ""
            if kind == "record" and record is None:
                raise ValueError("该属性记录已不存在，不能定位旧检查结果")
            if kind == "node" and not node:
                raise ValueError("该节点已不存在，不能定位旧检查结果")
            if source_id:
                try:
                    text = ops.source(source_id)["text"]
                except (ValueError, KeyError):
                    if kind == "source":
                        raise ValueError("该问题引用的原文来源不存在，不能定位") from None
                    source_id = ""
                    start, end = None, None
                else:
                    if type(start) is not int:
                        if quote:
                            from .source_location import locate_text
                            hits = locate_text(text, [quote], 2)[0]["matches"]
                            if len(hits) != 1:
                                if kind == "source":
                                    raise ValueError("引文无法唯一定位，请重新检查或在原文中核对")
                            else:
                                start, end = hits[0]["start"], hits[0]["end"]
                        if type(start) is not int:
                            original_start = disclosure.get("start")
                            start = original_start if type(original_start) is int else 0
                    if type(end) is not int:
                        end = start + len(quote) if quote else min(len(text), start + 80)
                    valid_range = 0 <= start <= end <= len(text)
                    if quote and (not valid_range or text[start:end] != quote):
                        from .source_location import locate_text
                        hits = locate_text(text, [quote], 2)[0]["matches"]
                        if len(hits) == 1:
                            start, end = hits[0]["start"], hits[0]["end"]
                            valid_range = True
                        else:
                            if kind == "source":
                                raise ValueError("披露坐标不是该引文位置，且引文无法唯一定位")
                            source_id = ""
                            start, end = None, None
                    elif not valid_range:
                        if kind == "source":
                            raise ValueError("检查结果的原文坐标已无效，请重新运行检查")
                        source_id = ""
                        start, end = None, None
            elif kind == "source":
                raise ValueError("该问题没有可定位的原文来源")
            if kind == "record" and node_id not in nodes:
                node_id = ""
            return {"kind": kind, "node_id": node_id or "", "record": record or {},
                    "record_id": requested_record or "", "source_id": source_id or "",
                    "start": start, "end": end, "quote": quote, "generation": generation}

        def apply(result: dict) -> dict:
            return result | {"superseded": generation != self._static_target_generation}

        self._project_task("static_check_target", work, apply)

    @Slot(str, str, bool)
    def runSourceTask(self, source_id: str, slice_id: str, review: bool = False) -> None:
        """Send the selected source range to the current unified conversation."""
        try:
            requested_chat, access_mode = self._capture_assistant_access(self._assistant_chat)
        except (ValueError, OSError) as exc:
            self.errorOccurred.emit("source_task", str(exc))
            return

        def work(ops) -> dict:
            source = ops.source(source_id)
            start, end = 0, len(source["text"])
            title = source.get("title") or source_id
            if slice_id:
                part = ops.source_slice(source_id, slice_id)
                start, end = part["start"], part["end"]
                title = part.get("title") or title
            action = "复核" if review else "提取"
            purpose = effective_prompt(ops, "review" if review else "source_extraction")
            instruction = (f"{action}原文：{title}。\n来源 source_id={source_id}，"
                           f"范围 start={start}, end={end}（Unicode字符坐标，左闭右开）"
                           + (f"，slice_id={slice_id}" if slice_id else "（全文）") + "。\n"
                           + purpose)
            return {"instruction": instruction, "source_id": source_id, "slice_id": slice_id,
                    "start": start, "end": end, "review": review, "chat_id": requested_chat}

        def apply(result: dict) -> dict:
            self._assistant_turn(result["instruction"], "", requested_chat, access_mode_override=access_mode)
            return result

        self._project_task("source_task", work, apply)

    @Slot()
    def loadSources(self) -> None:
        self._project_task("sources", lambda ops: {"items": ops.sources()})

    @Slot(str)
    def sourceText(self, source_id: str) -> None:
        self._project_task("source", lambda ops: {"source_id": source_id, "source": ops.source(source_id)})

    @Slot(str, str)
    def importSource(self, title: str, text: str) -> None:
        def work(ops) -> dict:
            source_id = ops.import_source(title, text)
            return {"source_id": source_id, "sources": ops.sources(), "revision": ops.graph_project_info()["revision"]}

        self._project_task("source_imported", work)

    @Slot(str, str)
    def importSourceFile(self, title: str, file_path: str) -> None:
        def work(ops) -> dict:
            source_id = ops.import_source_file(self.urlToPath(file_path), title=title)
            return {"source_id": source_id, "sources": ops.sources(), "revision": ops.graph_project_info()["revision"]}

        self._project_task("source_imported", work)

    @Slot(str)
    def loadSourceSlices(self, source_id: str) -> None:
        self._project_task(
            "source_slices",
            lambda ops: {"source_id": source_id, "items": ops.source_slices(source_id)},
        )

    @Slot(str, str)
    def sourceSlice(self, source_id: str, slice_id: str) -> None:
        self._project_task(
            "source_slice",
            lambda ops: {"source_id": source_id, "slice": ops.source_slice(source_id, slice_id)},
        )

    @Slot()
    @Slot(int)
    def loadProjectSettings(self, request_token: int = 0) -> None:
        self._project_task("project_settings", lambda ops: ops.load_project_settings() | {
            "catalog": ops.provider_catalog(), "request_token": request_token,
            "project_path": str(project_package.layout(ops.project_info().path).entry)})

    @Slot("QVariantMap")
    @Slot("QVariantMap", int)
    def saveProjectSettings(self, settings: dict, request_token: int = 0) -> None:
        self.saveProjectSettingsWithKey(settings, "", False, request_token)

    @Slot("QVariantMap", str, bool)
    @Slot("QVariantMap", str, bool, int)
    def saveProjectSettingsWithKey(self, settings: dict, api_key: str, clear_api_key: bool,
                                   request_token: int = 0) -> None:
        payload = dict(settings)
        payload["profile"] = self._exact_integers(dict(payload.get("profile") or {}))
        def work(ops):
            identity = {"request_token": request_token,
                        "project_path": str(project_package.layout(ops.project_info().path).entry)}
            try:
                return ops.save_project_settings(payload, api_key=api_key or None, clear_api_key=clear_api_key) | {
                    **identity, "catalog": ops.provider_catalog(), "saved": True}
            except Exception as error:
                # Secret-bearing failures must never cross signals or UI logs.
                return identity | {"error": "设置未保存，请检查配置及本机密钥存储。", "error_type": type(error).__name__}
        self._project_task("project_settings", work)

    @Slot("QVariantMap", int)
    def loadProjectKeyStatus(self, profile: dict, request_token: int) -> None:
        payload = self._exact_integers(dict(profile))
        def work(ops):
            identity = {"request_token": request_token,
                        "project_path": str(project_package.layout(ops.project_info().path).entry)}
            try:
                return identity | {"api_key_status": ops.provider_api_key_status(payload)}
            except (ValueError, OSError):
                return identity | {"error": "当前配置的密钥状态暂不可用。"}
        self._project_task("project_key_status", work)

    @Slot("QVariantMap", str, bool, int)
    def checkProjectApiKey(self, profile: dict, api_key: str, clear_api_key: bool,
                           request_token: int) -> None:
        try:
            path = self._project()
        except ValueError:
            return
        epoch = self._epoch
        payload = self._exact_integers(dict(profile))
        def work():
            identity = {"project_path": str(path), "request_token": request_token}
            try:
                ops = project_catalog.open_graph_project(path)
                result = ops.check_model_credential(payload, api_key=api_key or None,
                                                    ignore_saved_key=clear_api_key)
            except Exception:
                result = {"valid": None, "code": "check_failed", "message": "暂时无法完成有效性检查。"}
            if not self._closed and self._project_path == path and self._epoch == epoch:
                try:
                    self.resultReady.emit("project_key_check", result | identity)
                except RuntimeError:
                    pass  # The window was destroyed while the request completed.
        self._pool.submit(work)

    @Slot()
    def loadProjectStyle(self) -> None:
        self._project_task("project_style", lambda ops: ops.load_project_style() | {
            "project_path": str(project_package.layout(ops.project_info().path).entry)})

    @Slot(str, int)
    def saveProjectStyle(self, text: str, revision: int) -> None:
        self._project_task("project_style", lambda ops: ops.save_project_style(
            text, expected_revision=revision, reason="编辑项目风格约定") | {
                "project_path": str(project_package.layout(ops.project_info().path).entry), "saved": True})

    @Slot()
    def loadPromptSettings(self) -> None:
        self._project_task("prompt_settings", lambda ops: ops.load_prompt_settings() | {
            "project_path": str(project_package.layout(ops.project_info().path).entry)})

    @Slot(str, "QVariant", int)
    def savePromptOverride(self, prompt_id: str, text: object, revision: int) -> None:
        if text is not None and not isinstance(text, str):
            self.errorOccurred.emit("prompt_settings", "提示词须为文本")
            return
        self._project_task("prompt_settings", lambda ops: ops.save_prompt_override(
            prompt_id, text, expected_revision=revision, reason="编辑工程提示词") | {
                "project_path": str(project_package.layout(ops.project_info().path).entry),
                "saved": True, "prompt_id": prompt_id, "restored": text is None})

    @Slot(str)
    def renameProject(self, title: str) -> None:
        normalized = title.strip()
        if not normalized:
            self.errorOccurred.emit("project_renamed", "工程名称不能为空")
            return

        def rename(ops: GraphOperations) -> dict:
            ops.set_graph_title(normalized)
            project = ops.graph_project_info()
            project["path"] = str(project_package.layout(ops.project_info().path).entry)
            return {"project": project}

        self._project_task("project_renamed", rename)

    @Slot(str)
    def extractEventActions(self, node_id: str) -> None:
        """An explicit user request, dispatched into the selected unified chat."""
        try:
            requested_chat, access_mode = self._capture_assistant_access(self._assistant_chat)
        except (ValueError, OSError) as exc:
            self.errorOccurred.emit("event_actions_task", str(exc))
            return
        def work(ops) -> dict:
            node = next((row for row in ops.graph_snapshot()["nodes"] if row["id"] == node_id), None)
            if node is None or node.get("node_type") != "event":
                raise ValueError("动作提取只能针对已存在的event事件")
            instruction = (effective_prompt(ops, "action_extraction") + "\n目标事件："
                           + json.dumps({"event_id": node_id, "name": node.get("name")}, ensure_ascii=False))
            return {"instruction": instruction, "event_id": node_id, "chat_id": requested_chat}
        def apply(result: dict) -> dict:
            self._assistant_turn(result["instruction"], "", requested_chat, access_mode_override=access_mode)
            return result
        self._project_task("event_actions_task", work, apply)

    @Slot(str)
    @Slot(str, int)
    def loadProviderProfileFor(self, provider: str, request_token: int = 0) -> None:
        """Read this project's saved provider profile before editing or activating it."""
        def work(ops):
            profile = ops.harness_settings(provider.strip() or None)
            return {"profile": profile, "catalog": ops.provider_catalog(),
                    "api_key_status": ops.provider_api_key_status(profile),
                    "request_token": request_token,
                    "project_path": str(project_package.layout(ops.project_info().path).entry)}
        self._project_task("provider", work)
