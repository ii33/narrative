"""关系图：一条关系站一边，画布能缩放平移，圈子跟着关系多少变大。

两件事在这里钉住：

* 两侧写了同一个词（A —父子→ B、B —父子→ A）不是两条关系，是同一件事说了一遍：
  图里合成一条、两头带箭头，否则「有向」只是摆设。
* 圈的大小跟着关系条数走——枢纽本来就该比边角的人显眼——而画布是自由的：
  缩放、平移只改看的方式，不改数据。
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge

pytestmark = pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") != "offscreen",
    reason="Qt Quick 用例需要 offscreen 平台",
)


def _value(item: Any) -> Any:
    return item.toVariant() if hasattr(item, "toVariant") else item


@pytest.fixture()
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "settings.sqlite3"))
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    window.setProperty("mode", "workspace")
    window.setProperty("currentPage", "relations")
    found = None
    deadline = time.monotonic() + 5
    while found is None and time.monotonic() < deadline:
        app.processEvents()
        found = window.findChild(object, "relationPage")
        time.sleep(0.01)
    assert found is not None
    try:
        yield app, found
    finally:
        engine.deleteLater()
        bridge.close()
        app.processEvents()


def _person(node_id: str, name: str) -> dict:
    return {"id": node_id, "name": name, "label_id": node_id}


def _edge(left: str, right: str, kind: str) -> dict:
    return {"from": left, "to": right, "kind": kind, "state": ""}


def test_the_same_word_on_both_sides_becomes_one_edge(page) -> None:
    _app, view = page
    view.apply({
        "persons": [_person("P1", "林舟"), _person("P2", "苏娘")],
        "edges": [_edge("P1", "P2", "仇敌"), _edge("P2", "P1", "仇敌")],
    })

    links = _value(view.links())
    assert len(links) == 1
    assert links[0]["both"] is True
    assert links[0]["kind"] == "仇敌"


def test_a_directional_pair_stays_two_edges_with_their_own_words(page) -> None:
    """A —儿子→ B 和 B —父亲→ A 是两条，各写各的一侧。"""
    _app, view = page
    view.apply({
        "persons": [_person("P1", "林舟"), _person("P2", "老周")],
        "edges": [_edge("P1", "P2", "儿子"), _edge("P2", "P1", "父亲")],
    })

    links = _value(view.links())
    assert len(links) == 2
    assert [link["both"] for link in links] == [False, False]
    assert sorted(link["kind"] for link in links) == ["儿子", "父亲"]


def test_two_relations_between_the_same_pair_do_not_overlap(page) -> None:
    """同一对人的两条不同关系要各占一边，不然叠成一条线。"""
    _app, view = page
    view.apply({
        "persons": [_person("P1", "林舟"), _person("P2", "苏娘")],
        "edges": [_edge("P1", "P2", "仇敌"), _edge("P2", "P1", "旧识")],
    })

    links = _value(view.links())
    assert len(links) == 2
    assert sorted(link["slot"] for link in links) == [-0.5, 0.5]


def test_a_node_with_more_relations_gets_a_bigger_circle(page) -> None:
    _app, view = page
    view.apply({
        "persons": [_person("P1", "林舟"), _person("P2", "苏娘"), _person("P3", "老周")],
        "edges": [
            _edge("P1", "P2", "仇敌"), _edge("P2", "P1", "仇敌"),
            _edge("P1", "P3", "同伙"), _edge("P3", "P1", "同伙"),
        ],
    })

    hub = _value(view.radiusOf(_person("P1", "林舟")))
    leaf = _value(view.radiusOf(_person("P3", "老周")))
    assert hub > leaf
    # 名字长度不该影响圈的大小：放不下就写到圈外，不然「店员小姐」零关系比枢纽还大。
    assert _value(view.radiusOf(_person("P3", "名字特别长的一个人"))) == leaf
    # 没有关系的节点是最小的那一档，但不能没有。
    assert leaf >= 13


def test_zoom_is_clamped_and_pan_is_only_a_view(page) -> None:
    _app, view = page
    view.apply({"persons": [_person("P1", "林舟")], "edges": []})

    for _ in range(40):
        view.zoomAt(200, 200, 1.2)
    assert abs(view.property("zoom") - 3.0) < 1e-6

    for _ in range(40):
        view.zoomAt(200, 200, 1 / 1.2)
    assert abs(view.property("zoom") - 0.35) < 1e-6

    before_x = view.property("panX")
    before_y = view.property("panY")
    view.panBy(30, -12)
    assert abs(view.property("panX") - before_x - 30) < 1e-6
    assert abs(view.property("panY") - before_y + 12) < 1e-6

    view.resetView()
    assert view.property("zoom") == 1
    assert view.property("panX") == 0


def test_zooming_keeps_the_point_under_the_cursor(page) -> None:
    """以光标为中心缩放：那一点在屏幕上不该跑掉，否则缩放会「游」。"""
    _app, view = page
    view.apply({"persons": [_person("P1", "林舟")], "edges": []})

    canvas = None
    for child in view.findChildren(object, "relationCanvas"):
        canvas = child
    assert canvas is not None
    canvas.setProperty("width", 800)
    canvas.setProperty("height", 600)

    view.setProperty("panX", 40)
    view.setProperty("panY", -20)
    x, y = 600, 150
    before = view.property("panX")
    view.zoomAt(x, y, 1.5)
    after = view.property("panX")
    # 光标在世界坐标里的落点缩放前后一致：(x-cx-pan)/zoom 不变
    world_before = (x - 400 - before) / 1.0
    world_after = (x - 400 - after) / view.property("zoom")
    assert abs(world_before - world_after) < 0.001
