"""Tool transport blocks stay collapsed while visible model prose stays live."""
from __future__ import annotations

import json
import time


def test_tool_only_mixed_and_legacy_outputs_render_as_prose_plus_collapsed_activity(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('QT_QUICK_BACKEND', 'software')
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    from PySide6.QtCore import QPointF, Qt, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from narrative.chat_store import ChatStore
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    app = QApplication.instance() or QApplication([])
    entry = tmp_path / 'display.nproj'
    ops = Operations.create_graph_project(entry)
    revision = ops.graph_project_info()['revision']
    store = ChatStore(ops.project_info().path)
    chat = store.create('工具显示测试')
    tool = {'type': 'tool-call', 'id': 'call-1', 'name': 'read_source',
            'arguments': json.dumps({'source_id': 'SYNTHETIC_SOURCE', 'length': 20})}
    legacy = json.dumps([tool], ensure_ascii=False)
    store.append(chat['id'], 'assistant', '', kind='model_output', message_id='session:1',
                 full_output=legacy, task_id='task', seq=1)
    before = store.load(chat['id'])
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    panel = window.findChild(object, 'assistantPanel')
    assert isinstance(window.contentItem(), QQuickItem)

    def wait(check):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return
            time.sleep(.01)
        raise AssertionError('QML chat display did not update')

    def messages():
        return panel.property('messages').toVariant()

    def delegates():
        transcript = panel.findChild(object, 'assistantTranscript')
        transcript.forceLayout()
        return sorted([item for item in transcript.property('contentItem').childItems()
                       if item.property('modelData')], key=lambda item: item.y())

    def publish():
        bridge.resultReady.emit('conversation_progress', {
            'event': 'model_message', 'chat_id': chat['id'], 'current': chat['id'],
            'project_path': str(entry), 'chat': store.load(chat['id']),
            'items': store.list(), 'busy': True,
        })
        app.processEvents()

    try:
        bridge.openProject(str(entry))
        wait(lambda: any(kind == 'project' for kind, _ in results))
        window.setProperty('assistantOpen', True)
        wait(lambda: panel.property('chatId') == chat['id'] and len(messages()) == 1)
        wait(lambda: not window.property('busy'))
        assert not errors, errors
        assert messages()[0]['text'] == ''
        row = delegates()[0]
        answer = row.findChild(object, 'assistantAnswer')
        outer = row.findChild(object, 'assistantProcessActivity')
        toggle = row.findChild(object, 'assistantProcessToggle')
        assert answer.property('visible') is False
        assert outer.property('visible') and not outer.property('expanded')
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         toggle.mapToScene(QPointF(toggle.width() / 2, toggle.height() / 2)).toPoint())
        app.processEvents()
        from test_chat_process_groups import activities
        wait(lambda: len(activities(row)) == 1)
        activity = activities(row)[0]
        details = activity.findChild(object, 'assistantToolDetails')
        toggle = activity.findChild(object, 'assistantToolToggle')
        assert not activity.property('expanded')
        assert not details.property('visible') and not details.property('text')
        assert 'SYNTHETIC_SOURCE' not in toggle.property('text')
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         toggle.mapToScene(QPointF(toggle.width() / 2, toggle.height() / 2)).toPoint())
        app.processEvents()
        assert activity.property('expanded') and details.property('visible')
        assert 'read_source' in details.property('text') and 'SYNTHETIC_SOURCE' in details.property('text')
        assert store.load(chat['id']) == before  # Expansion is display state only.

        mixed = [{'type': 'text', 'text': '第一段正文。'}, tool]
        store.append(chat['id'], 'assistant', '第一段正文。', kind='model_output', message_id='session:2',
                     content=mixed, full_output=json.dumps(mixed, ensure_ascii=False), seq=2, task_id='task')
        publish()
        wait(lambda: len(messages()) == 2)
        assert messages()[1]['text'] == '第一段正文。' and messages()[1]['toolBatches'][0]['tools']
        old_mixed = json.dumps([{'type': 'text', 'text': '第二段正文。'}, tool], ensure_ascii=False)
        store.append(chat['id'], 'assistant', '', kind='model_output', message_id='session:3',
                     full_output=old_mixed, seq=3, task_id='task')
        publish()
        wait(lambda: len(messages()) == 3)
        assert [m['text'] for m in messages()] == ['', '第一段正文。', '第二段正文。']
        literal = '{"这是回答": "应保留的普通JSON正文"}'
        store.append(chat['id'], 'assistant', literal, kind='model_output', message_id='session:4', seq=4)
        publish()
        wait(lambda: len(messages()) == 4)
        assert messages()[3]['text'] == literal and not messages()[3]['toolBatches'][0]['tools']
        previous = messages()
        results.clear()
        bridge.openChat(chat['id'])
        wait(lambda: any(kind == 'chat' for kind, _ in results))
        assert messages() == previous
        assert len(store.load(chat['id'])['messages']) == 4
        assert store.load(chat['id'])['messages'][0]['full_output'] == legacy
        assert ops.graph_project_info()['revision'] == revision
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()


def test_cancelled_failure_and_format_diagnostics_have_distinct_neutral_labels(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('QT_QUICK_BACKEND', 'software')
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication

    from narrative.chat_store import ChatStore
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    app = QApplication.instance() or QApplication([])
    entry = tmp_path / 'diagnostic-display.nproj'
    ops = Operations.create_graph_project(entry)
    store = ChatStore(ops.project_info().path)
    chat = store.create('停止与诊断显示')
    for index, (kind, text) in enumerate([
        ('assistant_cancelled', '任务已停止。未产生新的入库变更。'),
        ('assistant_incomplete', '任务未完成。未产生新的入库变更。'),
        ('format_correction', '工具参数格式诊断。'),
    ], start=1):
        store.append(chat['id'], 'system', text, kind=kind, task_id=f'task-{index}',
                     diagnostic_path=f'C:/synthetic/{kind}.json',
                     audit_directory=f'C:/synthetic/{kind}')

    bridge = NativeBridge()
    results = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    panel = window.findChild(object, 'assistantPanel')

    def wait(check):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return
            time.sleep(.01)
        raise AssertionError('QML diagnostic display did not update')

    try:
        bridge.openProject(str(entry))
        wait(lambda: any(kind == 'project' for kind, _ in results))
        window.setProperty('assistantOpen', True)
        wait(lambda: panel.property('chatId') == chat['id'])
        transcript = panel.findChild(object, 'assistantTranscript')
        transcript.positionViewAtEnd()
        transcript.forceLayout()
        app.processEvents()
        rows = sorted([item for item in transcript.property('contentItem').childItems()
                       if item.property('modelData')], key=lambda item: item.y())
        labels = {}
        paths = {}
        for row in rows:
            link = row.findChild(object, 'assistantDiagnosticLink')
            audit = row.findChild(object, 'assistantAuditPath')
            if link and link.property('visible'):
                data = row.property('modelData')
                if hasattr(data, 'toVariant'):
                    data = data.toVariant()
                labels[data['kind']] = link.property('text')
                paths[data['kind']] = audit.property('text')

        assert labels == {
            'assistant_cancelled': '查看停止记录',
            'assistant_incomplete': '查看失败诊断',
            'format_correction': '查看格式诊断',
        }
        assert '停止记录：C:/synthetic/assistant_cancelled.json' in paths['assistant_cancelled']
        assert '诊断：C:/synthetic/assistant_incomplete.json' in paths['assistant_incomplete']
        assert '诊断：C:/synthetic/format_correction.json' in paths['format_correction']
        assert [row['kind'] for row in panel.property('messages').toVariant()] == [
            'assistant_cancelled', 'assistant_incomplete', 'format_correction']
        assert all('审计目录：' in value for value in paths.values())
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()
