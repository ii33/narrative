"""工程就是一个 .nproj 目录，放在哪都行——没有「工程目录」这回事。

启动页的列表是「打开过的工程」，不是扫某个文件夹的结果。这组用例钉住：
按绝对路径打开、按包打开、新建在指定位置、最近打开记得住、不认的东西明确报错。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from narrative.native_bridge import NativeBridge
from narrative.project_catalog import create_graph_project


@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def bridge(tmp_path: Path, app, monkeypatch):
    # 最近打开的记录存在全局设置库；测试不能碰用户真正的那一份。
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "settings.sqlite3"))
    return NativeBridge()


def _package(tmp_path: Path, name: str = "book") -> Path:
    package = tmp_path / f"{name}.nproj"
    create_graph_project(package)
    return package


def _wait(app, results: list, kind: str, timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        for got, payload in reversed(results):
            if got == kind:
                return payload
        time.sleep(0.01)
    raise TimeoutError(kind)


def test_a_package_opens_by_absolute_path(bridge, app, tmp_path: Path) -> None:
    package = _package(tmp_path)
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))

    bridge.openProject(str(package))
    payload = _wait(app, results, "project")

    assert Path(payload["project"]["path"]).parent == package


def test_a_file_url_opens_too(bridge, app, tmp_path: Path) -> None:
    """QML 的 FolderDialog 给的是 file:///…，桥接要认。"""
    package = _package(tmp_path, "deep")
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))

    bridge.openProjectUrl(QUrl.fromLocalFile(str(package)).toString())
    payload = _wait(app, results, "project")
    assert Path(payload["project"]["path"]).parent == package


def test_something_that_is_not_a_package_says_so(bridge, app, tmp_path: Path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("not a project", encoding="utf-8")
    errors: list[tuple[str, str]] = []
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    bridge.openProject(str(junk))
    app.processEvents()
    assert errors and ".nproj" in errors[0][1]


def test_a_bare_relative_name_is_refused(bridge, app) -> None:
    """没有工程目录可解析，只写名字没有意义。"""
    errors: list[tuple[str, str]] = []
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    bridge.openProject("book")
    app.processEvents()
    assert errors and "完整路径" in errors[0][1]


def test_a_new_project_goes_where_it_is_told(bridge, app, tmp_path: Path) -> None:
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))

    bridge.createProjectAt(str(tmp_path), "新工程")
    _wait(app, results, "project")

    package = tmp_path / "新工程.nproj"
    assert package.is_dir()
    assert (package / "graph.sqlite3").is_file()
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "narrative-project"
    assert manifest["title"]


def test_a_new_project_needs_a_real_folder(bridge, app, tmp_path: Path) -> None:
    errors: list[tuple[str, str]] = []
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    bridge.createProjectAt(str(tmp_path / "missing"), "x")
    app.processEvents()
    assert errors and "文件夹" in errors[0][1]


def test_recent_projects_remember_what_was_opened(bridge, app, tmp_path: Path) -> None:
    first = _package(tmp_path, "one")
    second = _package(tmp_path, "two")
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))

    for path in (first, second, first):  # 再打开一次不该出现两遍
        del results[:]
        bridge.openProject(str(path))
        _wait(app, results, "project")

    del results[:]
    bridge.loadRecentProjects()
    items = _wait(app, results, "recent_projects")["items"]

    assert [row["path"] for row in items] == [str(first), str(second)]
    assert all(row["exists"] for row in items)


def test_forgetting_a_recent_project_drops_it(bridge, app, tmp_path: Path) -> None:
    package = _package(tmp_path, "gone")
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.openProject(str(package))
    _wait(app, results, "project")

    del results[:]
    bridge.forgetRecentProject(str(package))
    items = _wait(app, results, "recent_projects")["items"]
    assert items == []


def test_a_recent_project_that_moved_is_listed_but_marked_missing(
    bridge, app, tmp_path: Path
) -> None:
    package = _package(tmp_path, "moved")
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.openProject(str(package))
    _wait(app, results, "project")

    (package / "graph.sqlite3").unlink()
    del results[:]
    bridge.loadRecentProjects()
    items = _wait(app, results, "recent_projects")["items"]
    assert [row["exists"] for row in items] == [False]
