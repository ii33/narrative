"""审核页要看得见提取出来的候选——否则图永远是空的。

提取写的是「变更候选」（`change_propose`），而审核页原先只拉旧的图候选、还读错了字段名
（`payload.candidates`，桥接给的是 `items`）。结果：提取成功、候选存在，界面却一条都不显示，
浏览页也就一直是空的。这条用例盯的就是这条链。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge
from narrative.operations import Operations


def _project(tmp_path: Path) -> Path:
    ops = Operations.create_graph_project(tmp_path / "book.sqlite3")
    ops.import_source("t", "甲把信交给乙。")
    revision = ops.graph_project_info()["revision"]
    ops.change_propose(
        [
            {"kind": "node", "id": "p1", "node_type": "person", "name": "甲"},
            {"kind": "node", "id": "p2", "node_type": "person", "name": "乙"},
        ],
        [],
        revision,
        source="manual",
        note="原文提取：第一回",
    )
    return tmp_path / "book.sqlite3"


@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


def _pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.002)


def _wait(app, results: list, kind: str, timeout: float = 25) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        for got, payload in reversed(results):
            if got == kind:
                return payload
        time.sleep(0.005)
    raise TimeoutError(kind)


@pytest.fixture()
def window(app, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "settings.sqlite3"))
    bridge = NativeBridge(tmp_path)
    results: list[tuple[str, dict]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    errors: list[tuple[str, str]] = []
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(1400)
    window.setHeight(900)
    window.show()
    _pump(app, 0.4)
    yield window, bridge, results, errors
    window.close()
    _pump(app, 0.1)


def test_a_change_candidate_shows_up_in_the_review_page(window, app, tmp_path: Path) -> None:
    window_obj, bridge, results, errors = window
    project = _project(tmp_path)

    bridge.openProject(str(project))
    _wait(app, results, "project")
    window_obj.setProperty("mode", "workspace")
    _pump(app, 0.3)
    window_obj.setProperty("currentPage", "review")
    _pump(app, 0.5)

    page = window_obj.findChild(object, "reviewPage")
    assert page is not None
    # 启动页会顺手读一次全局设置；那条链有自己的存储问题，和审核无关。
    assert [row for row in errors if row[0] != "global_settings"] == []
    rows = page.property("changeRows") or []
    assert len(rows) == 1
    assert rows[0]["note"] == "原文提取：第一回"


def test_accepting_it_from_the_page_fills_the_graph(window, app, tmp_path: Path) -> None:
    window_obj, bridge, results, errors = window
    project = _project(tmp_path)

    bridge.openProject(str(project))
    _wait(app, results, "project")
    window_obj.setProperty("mode", "workspace")
    _pump(app, 0.3)
    window_obj.setProperty("currentPage", "review")
    _pump(app, 0.6)

    page = window_obj.findChild(object, "reviewPage")
    candidate_id = (page.property("changeRows") or [{}])[0].get("id", "")
    assert candidate_id

    del results[:]
    page.pick(candidate_id)
    detail = _wait(app, results, "change_candidate")
    assert len(detail["candidate"]["graph_objects"]) == 2

    button = window_obj.findChild(object, "acceptCandidateButton")
    assert button is not None
    _pump(app, 0.3)
    assert button.property("enabled") is True

    del results[:]
    button.click()
    review = _wait(app, results, "change_review", timeout=40)
    assert review["revision"] >= 1

    ops = Operations.open_graph_project(project)
    assert {node["id"] for node in ops.graph_snapshot()["nodes"]} == {"p1", "p2"}
