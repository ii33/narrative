"""Long conversation work survives stale UI reads and delivers its terminal state."""
from __future__ import annotations

import threading
import time

import pytest


@pytest.mark.parametrize("failure", [None, TimeoutError("conversation deadline exceeded"), ValueError("model failed")])
def test_conversation_retains_terminal_result_during_graph_refresh(monkeypatch, failure):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from narrative.native_bridge import NativeBridge

    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    release_conversation, release_read = threading.Event(), threading.Event()
    results, errors, busy = [], [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    bridge.busyChanged.connect(busy.append)

    def wait(check):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return
            time.sleep(0.005)
        pytest.fail("Queued request did not reach its terminal state")

    def conversation():
        assert release_conversation.wait(5)
        if failure is not None:
            raise failure
        return {"answer": "completed", "revision": 2}

    def slow_read():
        assert release_read.wait(5)
        return {"obsolete": True}

    try:
        bridge._submit("assistant", conversation)
        assistant_id = bridge._next_request
        bridge._submit("detail", slow_read)
        read_id = bridge._next_request
        for request_id in (assistant_id, read_id):
            request = bridge._requests[request_id]
            bridge._requests[request_id] = (*request[:4], time.perf_counter() - bridge.STALE_AFTER_SECONDS - 1)
        bridge._submit("graph_refresh", lambda: {"fresh": True})
        assert assistant_id in bridge._requests
        assert read_id not in bridge._requests
        wait(lambda: any(kind == "graph_refresh" for kind, _ in results))
        assert assistant_id in bridge._requests
        assert busy[-1] is True
        assert len(errors) == 1 and errors[0][0] == "timeout" and "detail" in errors[0][1]
        assert "assistant" not in errors[0][1]
        release_read.set()
        release_conversation.set()
        wait(lambda: not bridge._requests)
        assert busy[-1] is False
        if failure is None:
            assert ("assistant", {"answer": "completed", "revision": 2}) in results
            assert not any(kind == "assistant" for kind, _ in errors)
        else:
            assert ("assistant", str(failure)) in errors
            assert not any(kind == "assistant" for kind, _ in results)
        assert not any(kind == "detail" for kind, _ in results)
    finally:
        release_conversation.set()
        release_read.set()
        bridge.close()
        app.processEvents()
