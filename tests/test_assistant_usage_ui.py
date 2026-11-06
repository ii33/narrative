"""Actual Qt usage display stays isolated from transcript movement and chat identity."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready
from test_static_check_ui import click, plain


def test_assistant_context_statistics_unknown_live_stale_and_chat_isolation(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        chat_id = ui.panel.property("chatId")
        project = str(ui.bridge._project_path)
        usage = ui.panel.findChild(object, "assistantUsage")
        occupancy = usage.findChild(object, "assistantContextOccupancy")
        assert "未知" in occupancy.property("text")
        base = {"chat_id": chat_id, "project_path": project, "task_id": "usage-task", "data_updated_at": "2026-11-05T10:00:01Z",
                "chat_usage": {"input_tokens": 12000, "output_tokens": 300, "total_tokens": 12300, "cache_read_tokens": 2000, "cache_hit_rate": 1/6, "cache_covered_requests": 2, "cache_unknown_requests": 0, "cache_write_tokens": None, "requests": 2, "incomplete": True},
                "task_usage": {"cache_read_tokens": 0, "cache_hit_rate": 0.0, "cache_covered_requests": 1, "cache_unknown_requests": 0, "input_tokens": 4000, "output_tokens": 100, "total_tokens": 4100, "requests": 1},
                "context": {"input_tokens": 4000, "context_window": 20000, "source": "actual", "stale": False, "compression_threshold": 16000, "output_reserve": 4000, "sampled_at": "2026-11-05T10:00:01Z"}}
        ui.bridge.resultReady.emit("assistant_usage", base)
        ui.wait(lambda: "20.0%" in occupancy.property("text"))
        assert usage.findChild(object, "assistantContextSource").property("text") == "实测"
        headline = usage.findChild(object, "assistantChatCacheHeadline")
        assert headline.property("visible")
        assert "2,000 Token · 16.7%" in headline.property("text")
        click(ui, usage.findChild(object, "assistantUsageToggle"))
        assert "0 Token · 0.0%" in usage.findChild(object, "assistantTaskCacheUsage").property("text")
        assert "12,300" in usage.findChild(object, "assistantChatUsage").property("text")
        assert "4,100" in usage.findChild(object, "assistantTaskUsage").property("text")
        partial = dict(base, data_updated_at="2026-11-05T10:00:01.5Z", chat_usage=dict(base["chat_usage"], total_tokens=None, cache_read_tokens=None, cache_hit_rate=None, known_cache_hit_rate=.25, cache_covered_requests=1, cache_unknown_requests=1, known_totals={"total_tokens": 12300,"cache_read_tokens":2000}))
        ui.bridge.resultReady.emit("assistant_usage", partial)
        ui.wait(lambda: "至少 12,300" in usage.findChild(object, "assistantChatUsage").property("text"))
        assert "至少 2,000 Token · 25.0%（已知请求）" in headline.property("text")
        assert "覆盖 1 个请求 · 未知 1 个请求" in usage.findChild(object, "assistantChatCacheUsage").property("text")
        assert "T10:" not in usage.findChild(object, "assistantContextDetails").property("text")
        click(ui, usage.findChild(object, "assistantUsageToggle"))
        render_ready(ui)
        pos = ui.view.mapToScene(QPointF(ui.view.width()/2, ui.view.height()/2))
        event = QWheelEvent(pos, ui.window.mapToGlobal(pos.toPoint()), QPoint(), QPoint(0,120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        ui.app.sendEvent(ui.window, event)
        ui.settle()
        before = ui.anchor()
        rows = plain(ui.panel.property("rawMessages"))
        newer = dict(base, data_updated_at="2026-11-05T10:00:02Z", context=dict(base["context"], input_tokens=6000, source="estimated", stale=True))
        ui.bridge.resultReady.emit("assistant_usage", newer)
        ui.wait(lambda: "30.0%" in occupancy.property("text"))
        after = ui.anchor()
        assert before[0] == after[0] and abs(before[1]-after[1]) < 1
        assert plain(ui.panel.property("rawMessages")) == rows
        ui.bridge.resultReady.emit("assistant_usage", base)
        ui.settle()
        assert "30.0%" in occupancy.property("text")
        ui.bridge.resultReady.emit("assistant_usage", dict(newer, project_path=str(tmp_path / "other.nproj"), data_updated_at="2026-11-05T10:00:03Z"))
        ui.settle()
        assert "30.0%" in occupancy.property("text")
        ui.bridge.resultReady.emit("conversation_progress", {"event": "task_started", "chat_id": chat_id, "project_path": project, "task_id": "next-task", "busy": True})
        ui.settle()
        assert plain(ui.panel.property("usageStatistics"))["task_usage"] == {}
        ui.bridge.resultReady.emit("assistant_usage", dict(newer, data_updated_at="2026-11-05T10:00:04Z"))
        ui.settle()
        assert plain(ui.panel.property("usageStatistics"))["task_id"] == "next-task"
        ui.bridge.resultReady.emit("assistant_usage", dict(newer, task_id="next-task", data_updated_at="2026-11-05T10:00:05Z"))
        ui.wait(lambda: plain(ui.panel.property("usageStatistics"))["task_usage"].get("total_tokens") == 4100)
        unavailable = dict(newer, task_id="next-task", data_updated_at="2026-11-05T10:00:06Z", context={"input_tokens": None, "source": "unknown", "stale": True, "last_known_input_tokens": 6000, "last_known_is_current_request": False})
        ui.bridge.resultReady.emit("assistant_usage", unavailable)
        ui.wait(lambda: "未知" in occupancy.property("text"))
        assert "6,000（当前占用未知）" in usage.findChild(object, "assistantLastKnownContext").property("text")
        other = next(item for item in ui.store.list() if item["id"] != chat_id)
        ui.bridge.openChat(other["id"])
        ui.wait(lambda: ui.panel.property("chatId") == other["id"])
        assert "未知" in occupancy.property("text")
        assert "命中率未知" in headline.property("text")
        ui.bridge.resultReady.emit("assistant_usage", dict(newer, data_updated_at="2026-11-05T10:00:04Z"))
        ui.settle()
        assert "未知" in occupancy.property("text")
