"""工程列表的「移除」：点得动，删不删由勾选决定。

两个坑都在这里钉住：

* 行本身的点击区如果盖在控件上面，按钮和「移除」全都收不到点击——看起来就是
  「按钮点不动」。所以测试不是看代码顺序，而是**真的点一下**。
* 删除磁盘文件是不可恢复的，所以默认只从列表拿掉；勾了才动文件，而且只认看着像
  工程包的目录（有 `graph.sqlite3` 或 `project.json`）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge
from narrative.project_catalog import create_graph_project

pytestmark = pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") != "offscreen",
    reason="Qt Quick 用例需要 offscreen 平台",
)


def _scene_point(item) -> QPointF:
    """控件中心在窗口里的坐标：逐层累加 x/y，不依赖 mapToItem（PySide 传不了 None）。"""
    x = item.property("width") / 2
    y = item.property("height") / 2
    node = item
    while node is not None:
        x += node.property("x") or 0
        y += node.property("y") or 0
        node = node.parentItem() if hasattr(node, "parentItem") else None
    return QPointF(x, y)


def _wait(app: QApplication, results: list[tuple[str, dict]], kind: str,
          timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        for name, payload in reversed(results):
            if name == kind:
                return payload
        time.sleep(0.01)
    raise TimeoutError(kind)


def _package(tmp_path: Path, name: str = "book") -> Path:
    package = tmp_path / f"{name}.nproj"
    create_graph_project(package)
    return package


def test_removing_from_the_list_keeps_the_files(bridge, app, tmp_path: Path) -> None:
    package = _package(tmp_path)
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.openProject(str(package))
    _wait(app, results, "project")

    bridge.removeProject(str(package), False)
    app.processEvents()

    assert package.is_file()
    # 只从列表拿掉也要说清楚，否则人会以为删过了
    removed = [payload for kind, payload in results if kind == "project_removed"]
    assert removed and removed[-1]["kept"] is True
    assert removed[-1]["deleted"] == []
    del results[:]
    bridge.loadRecentProjects()
    items = _wait(app, results, "recent_projects")["items"]
    assert [row["path"] for row in items] == []


def test_removing_with_delete_takes_the_package_and_its_side_dirs(
    bridge, app, tmp_path: Path
) -> None:
    package = _package(tmp_path)
    (tmp_path / "book_extraction").mkdir()
    (tmp_path / "book_chats").mkdir()
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.openProject(str(package))
    _wait(app, results, "project")

    bridge.removeProject(str(package), True)
    app.processEvents()

    assert not package.exists()
    removed = [payload for kind, payload in results if kind == "project_removed"]
    assert removed and removed[-1]["kept"] is False
    assert len(removed[-1]["deleted"]) == 4
    assert not (tmp_path / "book_data").exists()
    assert not (tmp_path / "book_extraction").exists()
    assert not (tmp_path / "book_chats").exists()


def test_a_directory_that_is_not_a_project_is_never_deleted(bridge, app,
                                                            tmp_path: Path) -> None:
    junk = tmp_path / "notes.nproj"
    junk.mkdir()
    (junk / "draft.txt").write_text("别删我", encoding="utf-8")
    errors: list[tuple[str, str]] = []
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    bridge.removeProject(str(junk), True)
    app.processEvents()

    assert (junk / "draft.txt").is_file()
    assert errors and "工程入口文件" in errors[0][1]


def test_the_row_click_area_sits_below_its_controls(bridge, app, tmp_path: Path) -> None:
    """行点击区必须**先声明**：后声明的盖在上面，按钮和「移除」就全点不动了。

    delegate 不在 QObject 树上（只能从 ListView 拿），所以这里看它第一个子项是不是
    那个铺满整行的 MouseArea——声明顺序就是叠放顺序。
    """
    package = _package(tmp_path, "clickable")
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.openProject(str(package))
    _wait(app, results, "project")

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    window.setProperty("mode", "launcher")
    window.setWidth(1100)
    window.setHeight(800)
    window.show()
    try:
        for _ in range(40):
            app.processEvents()
            time.sleep(0.01)
        bridge.loadRecentProjects()
        deadline = time.monotonic() + 5
        index = -1
        while index < 0 and time.monotonic() < deadline:
            app.processEvents()
            page = window.findChild(object, "launcherPage")
            if page is not None:
                index = page.rowClickIndex()
            time.sleep(0.01)
        # 铺满整行的点击区是第一个子项：它在最底层，按钮和「移除」才收得到点击
        assert index == 0
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()


def test_the_confirm_button_removes_what_the_dialog_shows(bridge, app,
                                                          tmp_path: Path) -> None:
    package = _package(tmp_path, "confirmed")
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.openProject(str(package))
    _wait(app, results, "project")

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    window.setProperty("mode", "launcher")
    window.setWidth(1100)
    window.setHeight(800)
    window.show()
    try:
        for _ in range(30):
            app.processEvents()
            time.sleep(0.01)
        page = window.findChild(object, "launcherPage")
        assert page is not None
        page.askRemove(str(package), "一本书")
        app.processEvents()
        dialog = window.findChild(object, "removeProjectDialog")
        assert dialog.property("visible") is True

        check = window.findChild(object, "removeDeleteFiles")
        check.setProperty("checked", True)
        app.processEvents()
        assert check.property("checked") is True

        page.confirmRemove()
        for _ in range(60):
            app.processEvents()
            time.sleep(0.01)

        assert not package.exists()
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()


@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def bridge(tmp_path: Path, app, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "settings.sqlite3"))
    return NativeBridge()


def test_deleting_entry_keeps_parent_and_unrelated_files(bridge, app, tmp_path):
    entry = _package(tmp_path, "owned")
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    bridge.removeProject(str(entry), True)
    app.processEvents()
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert tmp_path.is_dir()
    assert not entry.exists()
    assert not (tmp_path / "owned_data").exists()


@pytest.mark.parametrize("other_folder", [False, True])
def test_deleting_current_entry_retains_external_project_data(bridge, app, tmp_path, other_folder):
    import json
    import os

    from narrative.project_package import layout

    original = _package(tmp_path, "shared")
    found = layout(original)
    source = json.loads(original.read_text(encoding="utf-8"))
    other = tmp_path / "another-folder" if other_folder else tmp_path
    other.mkdir(exist_ok=True)
    external = other / "reference.nproj"
    source["layout"] = {
        key: Path(os.path.relpath(original.parent / value, other)).as_posix()
        for key, value in source["layout"].items()
    }
    external.write_text(json.dumps(source), encoding="utf-8")
    results = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.removeProject(str(external), True)
    app.processEvents()
    removed = [payload for kind, payload in results if kind == "project_removed"][-1]
    assert removed["retained_data"] is True
    assert removed["deleted"] == [str(external)]
    assert found.db.is_file() and original.is_file() and not external.exists()


@pytest.mark.parametrize("recent_only", [False, True])
def test_deleting_original_entry_keeps_data_referenced_by_an_alias(bridge, app, tmp_path, recent_only):
    import json
    import shutil

    from narrative.project_catalog import open_graph_project
    from narrative.project_package import layout
    from narrative.provider_store import GLOBAL_SCOPE, ProviderStore

    entry = _package(tmp_path, "original")
    if recent_only:
        other = tmp_path / "aliases"
        other.mkdir()
        alias = other / "copy.nproj"
        description = json.loads(entry.read_text(encoding="utf-8"))
        description["layout"] = {key: "../" + value for key, value in description["layout"].items()}
        alias.write_text(json.dumps(description), encoding="utf-8")
        ProviderStore().put(GLOBAL_SCOPE, "recent_projects", json.dumps([str(entry), str(alias)]))
    else:
        alias = tmp_path / "copy.nproj"
        shutil.copyfile(entry, alias)
    database = layout(entry).db
    results = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.removeProject(str(entry), True)
    app.processEvents()
    removed = [payload for kind, payload in results if kind == "project_removed"][-1]
    assert removed["retained_data"] is True
    assert removed["deleted"] == [str(entry)]
    assert not entry.exists() and database.is_file() and alias.is_file()
    assert open_graph_project(alias).graph_project_info()["revision"] == 0


@pytest.mark.parametrize("delete_files", [False, True])
@pytest.mark.parametrize("existing_directory", [False, True])
def test_forget_missing_or_legacy_directory_without_opening(
    bridge, app, tmp_path, monkeypatch, delete_files, existing_directory
):
    import json

    from narrative.provider_store import GLOBAL_SCOPE, ProviderStore

    target = tmp_path / "legacy"
    if existing_directory:
        target.mkdir()
        (target / "project.json").write_text("legacy untouched", encoding="utf-8")
    alias = QUrl.fromLocalFile(str(target)).toString()
    ProviderStore().put(GLOBAL_SCOPE, "recent_projects", json.dumps([alias, str(target)]))
    monkeypatch.setattr("narrative.project_package.layout", lambda path: pytest.fail("must not open legacy entries"))
    results = []
    errors = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    bridge.removeProject(str(target).upper() if os.name == "nt" else str(target), delete_files)
    removed = [payload for kind, payload in results if kind == "project_removed"]
    assert len(removed) == 1
    assert json.loads(ProviderStore().get(GLOBAL_SCOPE, "recent_projects")) == []
    assert bool(errors) == (existing_directory and delete_files)
    if existing_directory:
        assert (target / "project.json").read_text(encoding="utf-8") == "legacy untouched"


@pytest.mark.parametrize("failure", ["get", "put"])
def test_storage_failure_never_reports_removal_or_deletes_files(
    bridge, app, tmp_path, monkeypatch, failure
):
    import json

    from narrative.provider_store import GLOBAL_SCOPE, ProviderStore

    entry = _package(tmp_path, "save-failure")
    ProviderStore().put(GLOBAL_SCOPE, "recent_projects", json.dumps([str(entry)]))
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    def fail(*args, **kwargs):
        raise OSError("storage unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(ProviderStore, failure, fail)
        bridge.removeProject(str(entry), True)
    assert errors and "storage unavailable" in errors[-1][1]
    assert not any(kind == "project_removed" for kind, _ in results)
    assert entry.is_file()
    assert json.loads(ProviderStore().get(GLOBAL_SCOPE, "recent_projects")) == [str(entry)]


def test_old_recent_response_cannot_restore_removed_entry(bridge, app, tmp_path, monkeypatch):
    import json

    from narrative.provider_store import GLOBAL_SCOPE, ProviderStore

    entry = tmp_path / "missing.nproj"
    ProviderStore().put(GLOBAL_SCOPE, "recent_projects", json.dumps([str(entry)]))
    jobs, results = [], []
    monkeypatch.setattr(bridge, "_submit", lambda kind, work: jobs.append((kind, work)))
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.loadRecentProjects()
    stale = jobs[0][1]()
    bridge.forgetRecentProject(QUrl.fromLocalFile(str(entry)).toString())
    current = jobs[-1][1]()
    def finish(request_id, payload):
        bridge._requests[request_id] = ("recent_projects", bridge._epoch, None, None, time.perf_counter())
        bridge._on_worker_done(request_id, payload, None)
    finish(9001, current)
    finish(9002, stale)
    recent = [payload for kind, payload in results if kind == "recent_projects"]
    assert len(recent) == 1 and recent[0]["items"] == []


def test_launcher_removes_row_before_async_refresh(bridge, app, tmp_path, monkeypatch):
    import json

    from narrative.provider_store import GLOBAL_SCOPE, ProviderStore

    entry = tmp_path / "missing.nproj"
    ProviderStore().put(GLOBAL_SCOPE, "recent_projects", json.dumps([str(entry)]))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    window.setProperty("mode", "launcher")
    try:
        deadline = time.monotonic() + 5
        page = None
        while time.monotonic() < deadline:
            app.processEvents()
            page = window.findChild(object, "launcherPage")
            if page is not None and page.property("projectCount") == 1:
                break
            time.sleep(0.01)
        assert page is not None and page.property("projectCount") == 1
        monkeypatch.setattr(bridge, "loadRecentProjects", lambda: None)
        bridge.removeProject(str(entry), False)
        assert page.property("projectCount") == 0
        assert page.property("recentItems").toVariant() == []
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()
