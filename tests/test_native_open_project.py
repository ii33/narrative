"""工程可以放在任何地方：「打开工程…」走绝对路径，不要求先搬进 projects 目录。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from narrative.native_bridge import NativeBridge
from narrative.project_catalog import create_graph_project
from narrative.project_package import pack


@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def bridge(tmp_path: Path, app, monkeypatch):
    # 最近打开的记录存在全局设置库；测试不能碰用户真正的那一份。
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "settings.sqlite3"))
    return NativeBridge(tmp_path / "projects")


def _project(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    create_graph_project(path)
    return path


def _wait(app, results: list, kind: str, timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        for got, payload in reversed(results):
            if got == kind:
                return payload
        time.sleep(0.01)
    raise TimeoutError(kind)


def test_a_project_outside_the_catalog_opens_by_absolute_path(bridge, app, tmp_path: Path) -> None:
    outside = _project(tmp_path / "elsewhere" / "trial.sqlite3")
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))

    bridge.openProject(str(outside))
    payload = _wait(app, results, "project")

    assert payload["project"]["path"] == str(outside)


def test_a_file_url_opens_too(bridge, app, tmp_path: Path) -> None:
    """QML 的 FileDialog 给的是 file:///…，桥接要认。"""
    outside = _project(tmp_path / "deep" / "one.sqlite3")
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))

    bridge.openProjectUrl(QUrl.fromLocalFile(str(outside)).toString())
    payload = _wait(app, results, "project")
    assert payload["project"]["path"] == str(outside)


def test_a_package_opens_by_its_directory(bridge, app, tmp_path: Path) -> None:
    loose = _project(tmp_path / "loose" / "book.sqlite3")
    package = Path(pack(loose)["package"])
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))

    bridge.openProject(str(package))
    payload = _wait(app, results, "project")
    # 报出来的是包里的库；包本身是它的上一级。
    assert Path(payload["project"]["path"]).parent == package


def test_something_that_is_not_a_project_says_so(bridge, app, tmp_path: Path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("not a project", encoding="utf-8")
    errors: list[tuple[str, str]] = []
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    bridge.openProject(str(junk))
    app.processEvents()
    assert errors and "不是工程" in errors[0][1]


def test_a_bare_name_still_only_resolves_inside_the_catalog(bridge, app, tmp_path: Path) -> None:
    errors: list[tuple[str, str]] = []
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))

    bridge.openProject("../elsewhere/trial")
    app.processEvents()
    assert errors and "图项目目录" in errors[0][1]


def test_recent_projects_remember_what_was_opened(bridge, app, tmp_path: Path) -> None:
    first = _project(tmp_path / "elsewhere" / "one.sqlite3")
    second = _project(tmp_path / "elsewhere" / "two.sqlite3")
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
    path = _project(tmp_path / "elsewhere" / "gone.sqlite3")
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.openProject(str(path))
    _wait(app, results, "project")

    del results[:]
    bridge.forgetRecentProject(str(path))
    items = _wait(app, results, "recent_projects")["items"]
    assert items == []


def test_a_recent_project_that_moved_is_listed_but_marked_missing(
    bridge, app, tmp_path: Path
) -> None:
    path = _project(tmp_path / "elsewhere" / "moved.sqlite3")
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.openProject(str(path))
    _wait(app, results, "project")

    path.unlink()
    del results[:]
    bridge.loadRecentProjects()
    items = _wait(app, results, "recent_projects")["items"]
    assert [row["exists"] for row in items] == [False]
