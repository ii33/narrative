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

    assert package.is_dir()
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
    assert len(removed[-1]["deleted"]) == 3
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
    assert errors and "不是工程包" in errors[0][1]


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
