"""Actual painted label geometry: every relation remains visible and collision free."""

from __future__ import annotations

import itertools
import math

import pytest
from test_chat_scroll import transcript_shell


def payload():
    pairs = [
        (4, 8),
        (1, 3),
        (2, 3),
        (2, 7),
        (3, 7),
        (3, 4),
        (3, 2),
        (3, 1),
        (3, 5),
        (3, 6),
        (3, 4),
        (7, 3),
        (7, 2),
        (8, 4),
    ]
    kinds = [
        "旧日对手",
        "上司",
        "欺骗对象",
        "交往对象",
        "女友",
        "故交",
        "朋友",
        "秘书",
        "职员",
        "职员",
        "领路人",
        "男友",
        "旧识",
        "旧日对手",
    ]
    return {
        "persons": [{"id": f"P{i}", "name": f"合成人物{i}"} for i in range(1, 10)],
        "edges": [
            {"from": f"P{a}", "to": f"P{b}", "kind": kind, "state": "已结束" if i == 2 else ""}
            for i, ((a, b), kind) in enumerate(zip(pairs, kinds, strict=True))
        ],
    }


def plain(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


def curve_points(route):
    for i in range(301):
        t = i / 300
        yield (
            (1 - t) ** 2 * route["start"]["x"]
            + 2 * (1 - t) * t * route["cx"]
            + t * t * route["end"]["x"],
            (1 - t) ** 2 * route["start"]["y"]
            + 2 * (1 - t) * t * route["cy"]
            + t * t * route["end"]["y"],
        )


def segment_crosses_rect(a, b, rect):
    # Independent slab intersection, including crossings between sample points.
    low, high = 0.0, 1.0
    for axis, start, end in [
        (0, rect["x"], rect["x"] + rect["w"]),
        (1, rect["y"], rect["y"] + rect["h"]),
    ]:
        delta = b[axis] - a[axis]
        if abs(delta) < 1e-12:
            if not start <= a[axis] <= end:
                return False
        else:
            t0, t1 = sorted(((start - a[axis]) / delta, (end - a[axis]) / delta))
            low, high = max(low, t0), min(high, t1)
            if low > high:
                return False
    return True


def check_labels(page):
    from PySide6.QtGui import QFont, QFontMetricsF

    font = QFont("sans-serif")
    screen_font = page.property("labelScreenFontSize")
    assert 10 <= screen_font <= 14
    font.setPixelSize(math.floor(screen_font))
    metrics = QFontMetricsF(font)
    nodes = plain(page.property("graphLayout"))["positions"]
    geometry = plain(page.property("displayLabelLayout"))
    links = plain(page.links())
    routes = geometry["edges"]
    assert len(routes) == len(links) == 13
    assert sorted(r["edge_index"] for r in routes) == list(range(len(links)))
    assert len({r["edge_key"] for r in routes}) == len(links)
    for route in routes:
        edge = links[int(route["edge_index"])]
        assert route["edge"] == edge
        rect = route["label"]
        expected = edge["kind"] + ("（" + edge["state"] + "）" if edge.get("state") else "")
        camera = page.property("zoom") * plain(page.property("graphLabelLayout"))["fitScale"]
        assert rect["text"] == expected
        assert rect["w"] * camera >= metrics.horizontalAdvance(expected) - 1
        assert rect["h"] * camera >= metrics.height() - 1
        for point in nodes.values():
            dx = max(rect["x"] - point["x"], 0, point["x"] - rect["x"] - rect["w"])
            dy = max(rect["y"] - point["y"], 0, point["y"] - rect["y"] - rect["h"])
            assert math.hypot(dx, dy) > point["r"], (edge, point, rect)
        leader = route["leader"]
        if leader["from"] != leader["to"]:
            x, y = leader["from"]["x"], leader["from"]["y"]
            assert rect["x"] - 1e-6 <= x <= rect["x"] + rect["w"] + 1e-6
            assert rect["y"] - 1e-6 <= y <= rect["y"] + rect["h"] + 1e-6
            assert (
                min(
                    abs(x - rect["x"]),
                    abs(x - rect["x"] - rect["w"]),
                    abs(y - rect["y"]),
                    abs(y - rect["y"] - rect["h"]),
                )
                < 1e-6
            )
        # Independently solve the Bezier point for the stored leader anchor.
        best = min(
            math.hypot(
                (1 - t) ** 2 * route["start"]["x"]
                + 2 * (1 - t) * t * route["cx"]
                + t * t * route["end"]["x"]
                - leader["to"]["x"],
                (1 - t) ** 2 * route["start"]["y"]
                + 2 * (1 - t) * t * route["cy"]
                + t * t * route["end"]["y"]
                - leader["to"]["y"],
            )
            for t in [i / 20 for i in range(1, 20)]
        )
        assert best < 0.001
    for a, b in itertools.combinations(routes, 2):
        x, y = a["label"], b["label"]
        assert (
            x["x"] + x["w"] <= y["x"]
            or y["x"] + y["w"] <= x["x"]
            or x["y"] + x["h"] <= y["y"]
            or y["y"] + y["h"] <= x["y"]
        )
    for route in routes:
        for other in routes:
            if other["edge_key"] == route["edge_key"]:
                continue
            points = list(curve_points(other))
            assert not any(
                segment_crosses_rect(a, b, route["label"])
                for a, b in zip(points, points[1:], strict=False)
            ), (route["edge"], other["edge"])
    return geometry


@pytest.mark.parametrize("width,opened", [(1392, True), (1392, False), (1442, True), (1442, False)])
def test_painted_labels_avoid_all_nodes_and_each_other_without_hiding_relations(
    tmp_path, monkeypatch, width, opened
):
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QSignalSpy, QTest

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.window.setWidth(width)
        ui.window.setProperty("assistantOpen", opened)
        ui.window.setProperty("currentPage", "relations")
        ui.settle()
        page = ui.window.findChild(object, "relationPage")
        page.apply(payload())
        ui.wait(lambda: page.property("labelGeometryReady"))
        positions = plain(page.property("graphLayout"))
        base = plain(page.property("graphLabelLayout"))
        spy = QSignalSpy(page.graphLayoutChanged)
        fonts = []
        canvas = ui.window.findChild(object, "relationCanvas")
        cx, cy = canvas.width() / 2, canvas.height() / 2
        for zoom in (1.0, 1.55, 3.0, 0.35):
            page.resetView()
            if zoom != 1:
                page.zoomAt(cx, cy, zoom)
            ui.settle()
            ui.wait(lambda: bool(plain(page.property("displayLabelLayout"))["edges"]))
            geometry = check_labels(page)
            camera = zoom * base["fitScale"]
            hits = []
            for route in geometry["edges"]:
                rect = route["label"]
                x = cx + (rect["x"] + rect["w"] / 2) * camera
                y = cy + (rect["y"] + rect["h"] / 2) * camera
                if 3 < x < canvas.width() - 3 and 3 < y < canvas.height() - 3:
                    hits.append((route, x, y))
            assert hits
            route, x, y = hits[0]
            assert page.labelAt(x, y) == route["edge_key"]
            scene = canvas.mapToScene(QPointF(x, y))
            QTest.mouseMove(ui.window, scene.toPoint())
            ui.settle()
            assert page.property("hoveredEdgeKey") == route["edge_key"]
            assert page.highlightedNode(route["edge"]["from"]) and page.highlightedNode(
                route["edge"]["to"]
            )
            fonts.append(page.property("labelScreenFontSize"))
            assert plain(page.property("graphLayout")) == positions
            assert plain(page.property("graphLabelLayout")) == base
            assert page.property("panX") == pytest.approx(0) and page.property(
                "panY"
            ) == pytest.approx(0)
            assert page.property("zoom") == pytest.approx(zoom)
        assert spy.count() == 0
        assert fonts[0] < fonts[1] < fonts[2] and fonts[3] <= fonts[0]
        display = plain(page.property("displayLabelLayout"))
        page.panBy(32, -19)
        ui.settle()
        assert plain(page.property("displayLabelLayout")) == display
        assert plain(page.property("graphLayout")) == positions
