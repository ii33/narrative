"""Source buttons delegate exact ranges to the current unified conversation."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest


@contextmanager
def shell(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('QT_QUICK_BACKEND', 'software')
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    monkeypatch.setenv('ARK_API_KEY', 'offline-placeholder')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'offline-placeholder')
    from PySide6.QtCore import QPointF, Qt, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    ops = Operations.create_graph_project(tmp_path / 'source-buttons.nproj')
    text = '😀甲与乙是朋友。\n乙向甲问好。'
    sid = ops.import_source('合成全文', text)
    start, end = text.index('甲'), text.index('。') + 1
    part = ops.create_source_slices(sid, [{'title': '合成片段', 'start': start, 'end': end}])[0]
    app = QApplication.instance() or QApplication([])
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda k, p: results.append((k, p)))
    bridge.errorOccurred.connect(lambda k, m: errors.append((k, m)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    assert isinstance(window.contentItem(), QQuickItem)

    def wait(check):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return
            time.sleep(.01)
        pytest.fail(f'Source task did not finish: {errors}')

    def invoke(kind, action):
        results.clear()
        action()
        wait(lambda: any(k == kind for k, _ in results))
        return next(p for k, p in reversed(results) if k == kind)

    def click(name):
        app.processEvents()
        QTest.qWait(30)
        button = window.findChild(object, name)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint())
        app.processEvents()

    try:
        invoke('project', lambda: bridge.openProject(str(tmp_path / 'source-buttons.nproj')))
        window.setProperty('currentPage', 'source')
        page = window.findChild(object, 'sourcePage')
        wait(lambda: page.property('selectedId') == sid and page.property('previewText') == text)
        chat = invoke('chat', bridge.newChat)['chat']
        wait(lambda: not window.property('busy'))
        yield SimpleNamespace(ops=ops, text=text, sid=sid, part=part, app=app, bridge=bridge,
                              results=results, errors=errors, window=window, page=page, chat=chat,
                              wait=wait, invoke=invoke, click=click)
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()


def test_full_and_selected_slice_buttons_use_current_chat_and_block_duplicate_clicks(tmp_path, monkeypatch):
    from narrative import native_bridge

    requests = []
    entered, release = threading.Event(), threading.Event()

    class Runner:
        def __init__(self, *_args, **_kwargs):
            pass

        def resume(self, _session):
            return False

        def agent_turn(self, instruction, session, *, on_message=None):
            requests.append((instruction, session, self))
            entered.set()
            assert release.wait(5)
            return SimpleNamespace(raw='离线工程回归完成', metadata={'finish_reason': 'completed'})

        def close(self, **_kwargs):
            release.set()

    monkeypatch.setattr(native_bridge, 'HarnessRunner', Runner)
    with shell(tmp_path, monkeypatch) as ui:
        ui.window.setProperty('assistantOpen', False)
        ui.click('sourceSliceExtract')
        ui.wait(entered.is_set)
        assert ui.window.property('assistantOpen')
        assert ui.window.property('busy')
        assert not ui.window.findChild(object, 'sourceSliceReview').property('enabled')
        # A graph refresh can arrive after a long model turn. Ordinary read
        # cleanup must not discard a conversation governed by its own deadline.
        for request_id, request in list(ui.bridge._requests.items()):
            if request[0] == 'assistant':
                ui.bridge._requests[request_id] = (*request[:4], time.perf_counter() - ui.bridge.STALE_AFTER_SECONDS - 1)
        ui.bridge.loadSourceSlices(ui.sid)
        assert 'assistant' in ui.bridge.pendingOperations()
        assert not ui.errors, ui.errors
        ui.click('sourceSliceReview')
        assert len(requests) == 1
        assert f'start=0, end={len(ui.text)}' in requests[0][0]
        assert '（全文）' in requests[0][0]
        assert requests[0][1] == ui.chat['session']
        release.set()
        ui.wait(lambda: any(k == 'assistant' for k, _ in ui.results))
        selected_chat = ui.invoke('chat', ui.bridge.newChat)['chat']
        ui.wait(lambda: not ui.window.property('busy'))
        ui.page.selectSlice(ui.part['id'])
        ui.wait(lambda: ui.page.property('previewText') == ui.text[ui.part['start']:ui.part['end']])
        ui.results.clear()
        ui.click('sourceSliceReview')
        ui.wait(lambda: any(k == 'assistant' for k, _ in ui.results))
        assert len(requests) == 2
        assert f"start={ui.part['start']}, end={ui.part['end']}" in requests[1][0]
        assert '复核原文' in requests[1][0] and '查漏、勘误' in requests[1][0]
        assert requests[1][1] == selected_chat['session']
        assert selected_chat['session'] != ui.chat['session']
        assert requests[0][2] is requests[1][2]
        ui.wait(lambda: not ui.window.property('busy'))
        assert not ui.window.property('busy')
        assert ui.ops.graph_project_info()['revision'] == 1
        assert not ui.errors


def test_project_switch_discards_delayed_source_task_before_it_can_start_a_model(tmp_path, monkeypatch):
    from narrative import native_bridge
    from narrative.graph_operations import GraphOperations
    from narrative.operations import Operations

    def forbidden_runner(*_args, **_kwargs):
        raise AssertionError('A stale Source click must not start any model runtime')

    monkeypatch.setattr(native_bridge, 'HarnessRunner', forbidden_runner)
    entered, release = threading.Event(), threading.Event()
    with shell(tmp_path, monkeypatch) as ui:
        original = GraphOperations.source

        def delayed_source(ops, source_id):
            if source_id == ui.sid:
                entered.set()
                assert release.wait(5)
            return original(ops, source_id)

        monkeypatch.setattr(GraphOperations, 'source', delayed_source)
        ui.click('sourceSliceExtract')
        ui.wait(entered.is_set)
        other = tmp_path / 'other.nproj'
        target = Operations.create_graph_project(other)
        ui.invoke('project', lambda: ui.bridge.openProject(str(other)))
        release.set()
        ui.wait(lambda: not ui.window.property('busy'))
        assert ui.bridge._assistant_runner is None
        assert ui.page.property('selectedId') == ''
        assert target.graph_snapshot()['nodes'] == []
        assert not any(k == 'assistant' for k, _ in ui.results)
        assert not ui.errors
