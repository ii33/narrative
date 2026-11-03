"""Independent real wheel-event coverage for the assistant transcript."""
from __future__ import annotations

import pytest
from test_chat_scroll import transcript_shell


def wheel(ui, *, pixel=(0, 0), angle=(0, 120), phase=None, modifiers=None, item=None, touchpad=False, inverted=False, point=None):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QInputDevice, QPointingDevice, QWheelEvent
    from PySide6.QtWidgets import QApplication

    item = item or ui.view
    point = point or item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    if touchpad:
        device = getattr(ui, "_offline_touchpad", None)
        if device is None:
            device = QPointingDevice("offline precision touchpad", 993, QInputDevice.DeviceType.TouchPad,
                QPointingDevice.PointerType.Finger, QInputDevice.Capability.Position | QInputDevice.Capability.Scroll | QInputDevice.Capability.PixelScroll, 10, 0)
            ui._offline_touchpad = device
    else:
        device = QPointingDevice.primaryPointingDevice()
    event = QWheelEvent(point, QPointF(ui.window.mapToGlobal(point.toPoint())), QPoint(*pixel), QPoint(*angle), Qt.MouseButton.NoButton,
        modifiers or Qt.KeyboardModifier.NoModifier, phase or Qt.ScrollPhase.NoScrollPhase, inverted,
        Qt.MouseEventSource.MouseEventNotSynthesized, device)
    QApplication.sendEvent(ui.window, event)
    ui.settle()
    return event.isAccepted()


def state(ui):
    return {key: ui.view.property(key) for key in ("contentY", "originY", "contentHeight", "height", "moving", "atYBeginning", "atYEnd")} | {
        "followTail": ui.panel.property("followTail"), "scrollScheduled": ui.panel.property("scrollScheduled")}


@pytest.mark.parametrize("touchpad", [False, True])
def test_pixel_delta_only_wheel_scrolls_history_and_does_not_snap_back(tmp_path, monkeypatch, touchpad):
    from PySide6.QtCore import Qt

    with transcript_shell(tmp_path, monkeypatch) as ui:
        assert ui.bottom()
        before = ui.view.property("contentY")
        wheel(ui, pixel=(0, 0), angle=(0, 0), phase=Qt.ScrollPhase.ScrollBegin, touchpad=touchpad)
        for _ in range(3):
            wheel(ui, pixel=(0, 60), angle=(0, 0), phase=Qt.ScrollPhase.ScrollUpdate, touchpad=touchpad)
        wheel(ui, pixel=(0, 0), angle=(0, 0), phase=Qt.ScrollPhase.ScrollEnd, touchpad=touchpad)
        assert ui.view.property("contentY") < before - 20, state(ui)
        assert not ui.panel.property("followTail"), state(ui)
        anchor = ui.anchor()
        ui.store.append(ui.a["id"], "assistant", "尾部新输出\n" * 40, kind="model_output", message_id="pixel:new")
        ui.publish()
        assert ui.anchor()[0] == anchor[0] and abs(ui.anchor()[1] - anchor[1]) < 4, state(ui)


def test_single_tall_assistant_reply_accepts_real_wheel_input(tmp_path, monkeypatch):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        chat = ui.store.create("只有长回复")
        ui.store.append(chat["id"], "assistant", "长回复正文\n\n" * 150, kind="model_output", message_id="single:long")
        ui.bridge.openChat(chat["id"])
        ui.wait(lambda: ui.panel.property("chatId") == chat["id"] and len(ui.messages()) == 1)
        ui.settle()
        assert ui.view.property("contentHeight") > ui.view.height() * 2
        before = ui.view.property("contentY")
        wheel(ui)
        assert ui.view.property("contentY") < before - 10, state(ui)
        assert not ui.panel.property("followTail")


def test_shift_horizontal_wheel_does_not_capture_or_move_vertical_transcript(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt

    with transcript_shell(tmp_path, monkeypatch) as ui:
        before = ui.view.property("contentY")
        wheel(ui, angle=(120, 0), modifiers=Qt.KeyboardModifier.ShiftModifier)
        assert abs(ui.view.property("contentY") - before) < 2, state(ui)
        assert ui.panel.property("followTail"), state(ui)


def _clipped_center(ui, item):
    from PySide6.QtCore import QPointF, QRectF

    top = item.mapToScene(QPointF(0, 0))
    viewport = ui.view.mapToScene(QPointF(0, 0))
    rect = QRectF(top.x(), top.y(), item.width(), item.height()).intersected(
        QRectF(viewport.x(), viewport.y(), ui.view.width(), ui.view.height()))
    assert rect.width() > 4 and rect.height() > 4
    return rect.center()


def _visual_named(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = _visual_named(child, name)
        if found is not None:
            return found
    return None


def test_wheel_over_expanded_tool_details_scrolls_transcript_and_preserves_anchor(tmp_path, monkeypatch):
    import json

    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    with transcript_shell(tmp_path, monkeypatch) as ui:
        tool = {"type": "tool-call", "id": "long-tool", "name": "read_source", "arguments": json.dumps({
            "source_id": "SRC1", "diagnostic_lines": [f"第{i}条工具明细" for i in range(100)]}, ensure_ascii=False)}
        ui.store.annotate_message(ui.a["id"], "A:20", content=[tool], full_output=json.dumps([tool]), text="含长工具明细的回复")
        ui.publish()
        row = None
        for _ in range(120):
            row = next((item for item in ui.rows() if item.property("messageKey") == "A:20"), None)
            top = ui.view.property("contentY")
            if row is not None and top + 8 <= row.y() and row.y() + 50 < top + ui.view.height() - 8:
                break
            first = int(ui.anchor()[0].split(":")[-1])
            wheel(ui, angle=(0, 120 if first > 20 or row is not None and row.y() < top + 8 else -120))
        assert row is not None
        process_toggle = row.findChild(object, "assistantProcessToggle")
        QTest.mouseClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            process_toggle.mapToScene(QPointF(process_toggle.width() / 2, process_toggle.height() / 2)).toPoint())
        ui.settle()
        assert row.findChild(object, "assistantProcessActivity").property("expanded"), state(ui)
        tool_toggle = _visual_named(row, "assistantToolToggle")
        assert tool_toggle is not None
        QTest.mouseClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            tool_toggle.mapToScene(QPointF(tool_toggle.width() / 2, tool_toggle.height() / 2)).toPoint())
        ui.settle()
        details = _visual_named(row, "assistantToolDetails")
        assert details is not None and details.property("visible")
        before = ui.view.property("contentY")
        wheel(ui, angle=(0, -120), point=_clipped_center(ui, details))
        assert ui.view.property("contentY") > before + 10, state(ui)
        before = ui.view.property("contentY")
        wheel(ui, pixel=(0, -45), angle=(0, 0), touchpad=True, point=_clipped_center(ui, details))
        assert ui.view.property("contentY") > before + 10, state(ui)
        anchor = ui.anchor()
        ui.store.append(ui.a["id"], "assistant", "新尾部内容\n" * 40, kind="model_output", message_id="tool-detail:tail")
        ui.publish()
        assert ui.anchor()[0] == anchor[0] and abs(ui.anchor()[1] - anchor[1]) < 4, state(ui)


def test_wheel_over_prompt_or_outside_panel_does_not_move_transcript(tmp_path, monkeypatch):
    from PySide6.QtCore import QPointF

    with transcript_shell(tmp_path, monkeypatch) as ui:
        before = ui.view.property("contentY")
        prompt = ui.panel.findChild(object, "assistantPrompt")
        assert prompt is not None
        wheel(ui, pixel=(0, 60), angle=(0, 0), touchpad=True, item=prompt)
        assert abs(ui.view.property("contentY") - before) < 2, state(ui)
        top = ui.panel.mapToScene(QPointF(0, 0))
        wheel(ui, point=QPointF(top.x() - 40, top.y() + ui.panel.height() / 2))
        assert abs(ui.view.property("contentY") - before) < 2, state(ui)
        assert ui.panel.property("followTail"), state(ui)


def test_scrollbar_drag_while_streaming_preserves_reader_position_and_remains_draggable(tmp_path, monkeypatch):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    with transcript_shell(tmp_path, monkeypatch) as ui:
        bar = ui.view.findChild(object, "assistantTranscriptScrollBar")
        assert bar is not None
        thumb_y = (bar.property("position") + bar.property("size") / 2) * bar.height()
        start = bar.mapToScene(QPointF(bar.width() / 2, thumb_y)).toPoint()
        middle = bar.mapToScene(QPointF(bar.width() / 2, bar.height() * .48)).toPoint()
        before = ui.view.property("contentY")
        QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        try:
            ui.settle()
            assert bar.property("pressed")
            QTest.mouseMove(ui.window, middle, 20)
            ui.settle()
            assert ui.view.property("contentY") < before - 30, state(ui)
            assert not ui.panel.property("followTail")
            anchor = ui.anchor()
            ui.store.append(ui.a["id"], "assistant", "拖动期间的新回复\n" * 60, kind="model_output", message_id="bar:stream")
            ui.publish()
            assert bar.property("pressed") and not ui.bottom(), state(ui)
            assert ui.anchor()[0] == anchor[0] and abs(ui.anchor()[1] - anchor[1]) < 4, state(ui)
            position = ui.view.property("contentY")
            further = bar.mapToScene(QPointF(bar.width() / 2, bar.height() * .3)).toPoint()
            QTest.mouseMove(ui.window, further, 20)
            ui.settle()
            assert ui.view.property("contentY") < position - 10, state(ui)
        finally:
            QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                bar.mapToScene(QPointF(bar.width() / 2, bar.height() * .3)).toPoint())
            ui.settle()
        assert not ui.bottom() and not ui.panel.property("followTail"), state(ui)
