"""Adjacent graph and chat wheel delivery stays inside its visible page."""
from __future__ import annotations

import pytest
from test_chat_scroll import transcript_shell


@pytest.mark.parametrize("width", [1392, 1400, 1100])
def test_graph_and_chat_wheels_are_isolated_including_chat_boundaries(tmp_path, monkeypatch, width):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtTest import QTest

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.window.setProperty("currentPage", "relations")
        ui.window.setWidth(width)
        ui.settle()
        page = ui.window.findChild(object, "relationPage")
        canvas = ui.window.findChild(object, "relationCanvas")
        page.setProperty("cutName", "很长的阅读位置名称" * 30)
        ui.settle()

        def rect(item):
            top = item.mapToScene(QPointF(0, 0))
            return top.x(), top.y(), top.x() + item.width(), top.y() + item.height()

        bounds = rect(page)
        for name in ("relationCanvas", "relationCut", "relationRelatedOnly", "relationZoomOut",
                     "relationZoomIn", "relationReset", "relationRefresh"):
            item = ui.window.findChild(object, name)
            actual = rect(item)
            assert item.property("visible"), name
            assert bounds[0] <= actual[0] < actual[2] <= bounds[2], (name, bounds, actual)
        assert rect(canvas)[2] < rect(ui.panel)[0]

        def graph_state():
            return page.property("zoom"), page.property("panX"), page.property("panY")

        def wheel(point, angle, pixel=0, settle=True):
            QTest.wheelEvent(ui.window, point, QPoint(0, angle), QPoint(0, pixel))
            ui.app.processEvents()
            if settle:
                ui.wait(lambda: not ui.view.property("moving"))
                ui.settle()

        # Left edge is deliberate: the old oversized canvas overlapped it at
        # the user's 1392px window width, while centre-only tests missed the bug.
        chat_point = ui.view.mapToScene(QPointF(2, ui.view.height() / 2))
        graph_point = canvas.mapToScene(QPointF(canvas.width() / 2, canvas.height() / 2))
        before = graph_state()
        wheel(chat_point, -120)  # Outward at the end must not zoom the graph.
        assert ui.bottom() and graph_state() == before
        wheel(chat_point, 1200)
        assert not ui.bottom() and graph_state() == before
        wheel(chat_point, 24000)
        assert ui.view.property("atYBeginning") and graph_state() == before
        wheel(chat_point, 120)
        assert ui.view.property("atYBeginning") and graph_state() == before

        reader = (ui.view.property("contentY"), ui.panel.property("followTail"),
                  ui.panel.property("scrollGeneration"))
        wheel(graph_point, 120)
        assert page.property("zoom") == pytest.approx(before[0] * 1.18)
        assert reader == (ui.view.property("contentY"), ui.panel.property("followTail"),
                          ui.panel.property("scrollGeneration"))
        zoom = page.property("zoom")
        pan = page.property("panY")
        wheel(graph_point, 0, pixel=18)
        assert page.property("zoom") == zoom and page.property("panY") == pan + 18
        assert reader == (ui.view.property("contentY"), ui.panel.property("followTail"),
                          ui.panel.property("scrollGeneration"))

        # Keep both handlers active while moving the pointer across panels.
        for _ in range(3):
            before = graph_state()
            wheel(chat_point, 120, settle=False)
            assert graph_state() == before
            generation = ui.panel.property("scrollGeneration")
            wheel(graph_point, -120, settle=False)
            assert page.property("zoom") == pytest.approx(before[0] / 1.18)
            assert ui.panel.property("scrollGeneration") == generation
        ui.settle()
        assert not ui.errors, ui.errors


@pytest.mark.parametrize("short", [False, True])
def test_empty_and_short_chat_cannot_pass_wheel_to_graph(tmp_path, monkeypatch, short):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtTest import QTest

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.window.setWidth(1100)
        ui.window.setProperty("currentPage", "relations")
        chat = ui.store.create("短会话" if short else "空会话")
        if short:
            ui.store.append(chat["id"], "assistant", "简短回答。", message_id="short:1")
        ui.bridge.openChat(chat["id"])
        ui.wait(lambda: ui.panel.property("chatId") == chat["id"])
        ui.settle()
        page = ui.window.findChild(object, "relationPage")
        state = (page.property("zoom"), page.property("panX"), page.property("panY"))
        # Also cover panel title/composer blank regions, not just the ListView.
        points = [ui.view.mapToScene(QPointF(2, ui.view.height() / 2)),
                  ui.panel.mapToScene(QPointF(2, 15)),
                  ui.panel.mapToScene(QPointF(2, ui.panel.height() - 12))]
        for point in points:
            for delta in (-120, 120):
                QTest.wheelEvent(ui.window, point, QPoint(0, delta))
                ui.settle()
                assert (page.property("zoom"), page.property("panX"), page.property("panY")) == state
        assert not ui.errors, ui.errors
