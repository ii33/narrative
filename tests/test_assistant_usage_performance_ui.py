"""Usage updates must not create reload or layout feedback loops."""
import time

from test_chat_scroll import transcript_shell
from test_static_check_ui import click, plain


def test_usage_live_burst_keeps_gui_responsive_without_repeated_reads(tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer, qInstallMessageHandler

    from narrative.native_bridge import NativeBridge

    reads = []
    original = NativeBridge.loadUsageStatistics

    def tracked(self, chat_id, task_id=""):
        reads.append((chat_id, task_id))
        return original(self, chat_id, task_id)

    monkeypatch.setattr(NativeBridge, "loadUsageStatistics", tracked)
    warnings = []
    previous = qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
    try:
        with transcript_shell(tmp_path, monkeypatch) as ui:
            ui.settle()
            ui.window.setWidth(1100)
            click(ui, ui.panel.findChild(object, "assistantUsageToggle"))
            baseline = len(reads)
            chat_id = ui.panel.property("chatId")
            project = str(ui.bridge._project_path)
            messages = plain(ui.panel.property("rawMessages"))
            started = time.perf_counter()
            ticks = []
            timer = QTimer()
            timer.setInterval(1)
            timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
            timer.start()
            for index in range(500):
                ui.bridge.resultReady.emit("conversation_progress", {"event": "tool_start", "chat_id": chat_id, "project_path": project, "task_id": "burst-task", "busy": True})
                ui.bridge.resultReady.emit("assistant_usage", {"chat_id": chat_id, "project_path": project, "task_id": "burst-task", "data_updated_at": f"2026-11-05T10:{index//60:02d}:{index%60:02d}Z", "context": {"input_tokens": 1000+index, "context_window": 20000, "source": "actual", "stale": False}, "chat_usage": {"requests": index+1}, "task_usage": {"requests": index+1}})
                if index in (100, 300):
                    ui.window.setProperty("assistantOpen", False)
                    ui.app.processEvents()
                    ui.window.setProperty("assistantOpen", True)
                if index % 20 == 0:
                    ui.app.processEvents()
            ui.wait(lambda: plain(ui.panel.property("usageStatistics")).get("context", {}).get("input_tokens") == 1499)
            ui.wait(lambda: len(ticks) >= 2)
            timer.stop()
            print(f"500 live usage updates: {time.perf_counter()-started:.3f}s; reads={len(reads)-baseline}; heartbeat={len(ticks)}")
            assert len(reads)-baseline <= 1
            assert plain(ui.panel.property("rawMessages")) == messages
            assert not any("Binding loop" in message and ("Assistant" in message or "Label" in message) for message in warnings), warnings
    finally:
        qInstallMessageHandler(previous)
