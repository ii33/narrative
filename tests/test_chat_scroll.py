"""Real QML transcript scrolling: follow tail or preserve the reader's anchor."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager


@contextmanager
def transcript_shell(tmp_path, monkeypatch, *, single_long_user=False):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('QT_QUICK_BACKEND', 'software')
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtWidgets import QApplication

    from narrative.chat_store import ChatStore
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations

    app = QApplication.instance() or QApplication([])
    entry = tmp_path / 'scroll.nproj'
    ops = Operations.create_graph_project(entry)
    store = ChatStore(ops.project_info().path)
    a, b = store.create('A'), store.create('B')
    tool = {'type': 'tool-call', 'id': 'tool-20', 'name': 'read_source',
            'arguments': json.dumps({'source_id': 'synthetic', 'length': 100})}
    if single_long_user:
        user_text = '只查原文字数。以下为排版验收数据，不是其他任务。\n\n' + '\n\n'.join(
            f'排版数据{index:02d}：甲乙相遇，这一行只用于测试阅读位置。' for index in range(48))
        store.append(a['id'], 'user', user_text)
        store.append(a['id'], 'assistant', '', kind='model_output', message_id='A:tool',
                     content=[tool], full_output=json.dumps([tool], ensure_ascii=False))
        store.append(a['id'], 'assistant', '实际回答。\n\n' + '原文字数为11。\n\n' * 10,
                     kind='model_output', message_id='A:final')
    for chat, prefix, count in ((a, 'A', 0 if single_long_user else 50), (b, 'B', 20)):
        for index in range(count):
            text = f'{prefix}-{index:03d} 历史正文\n' + '\n'.join(f'第{line}行可见内容' for line in range(6))
            content = [{'type': 'text', 'text': text}]
            if prefix == 'A' and index == 20:
                content.append(tool)
            store.append(chat['id'], 'assistant', text, kind='legacy' if prefix == 'A' and index == 21 else 'model_output',
                         message_id=f'{prefix}:{index}', seq=index, content=content,
                         full_output=json.dumps(content, ensure_ascii=False))
    store.set_active(a['id'])
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    assert isinstance(window.contentItem(), QQuickItem)
    panel = window.findChild(object, 'assistantPanel')
    view = panel.findChild(object, 'assistantTranscript')

    def wait(check):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            app.processEvents()
            if check():
                return
            time.sleep(.01)
        raise AssertionError(f'Transcript did not settle: y={view.property("contentY")} height={view.property("contentHeight")} errors={errors}')

    def settle():
        for _ in range(8):
            app.processEvents()
            time.sleep(.01)

    def messages():
        value = panel.property('messages')
        return value.toVariant() if hasattr(value, 'toVariant') else value

    def rows():
        view.forceLayout()
        return [row for row in view.property('contentItem').childItems() if row.property('modelData')]

    def anchor():
        y = view.property('contentY')
        visible = [row for row in rows() if row.y() + row.height() > y and row.y() < y + view.height()]
        assert visible
        row = min(visible, key=lambda item: item.y())
        return row.property('messageKey'), row.y() - view.property('contentY')

    def bottom():
        return abs(view.property('contentY') + view.height() - view.property('contentHeight') - view.property('originY')) < 4

    def publish(chat=a, event='model_message'):
        bridge.resultReady.emit('conversation_progress', {
            'event': event, 'chat_id': chat['id'], 'current': chat['id'],
            'project_path': str(entry), 'chat': store.load(chat['id']), 'items': store.list(), 'busy': True,
        })
        settle()

    try:
        bridge.openProject(str(entry))
        wait(lambda: any(kind == 'project' for kind, _ in results))
        window.setProperty('assistantOpen', True)
        wait(lambda: panel.property('chatId') == a['id'] and len(messages()) == (3 if single_long_user else 50))
        wait(lambda: not window.property('busy'))
        settle()
        assert not errors, errors
        assert view.property('contentHeight') > (1 if single_long_user else 3) * view.height()
        yield type('Transcript', (), {'app': app, 'window': window, 'panel': panel, 'view': view,
             'bridge': bridge, 'results': results, 'errors': errors, 'store': store, 'a': a, 'b': b,
             'messages': staticmethod(messages), 'rows': staticmethod(rows), 'anchor': staticmethod(anchor),
             'bottom': staticmethod(bottom), 'publish': staticmethod(publish), 'wait': staticmethod(wait),
             'settle': staticmethod(settle)})()
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()


def test_tail_follows_long_append_and_same_count_growth(tmp_path, monkeypatch):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.view.positionViewAtEnd()
        ui.settle()
        assert ui.bottom()
        long_text = 'A-LONG 新输出\n' + '\n'.join(f'长回答第{n}行' for n in range(100))
        ui.store.append(ui.a['id'], 'assistant', long_text, kind='model_output', message_id='A:long')
        ui.publish()
        assert ui.bottom()
        assert ui.messages()[-1]['text'] == long_text
        grown = long_text + '\n' + '\n'.join(f'增量第{n}行' for n in range(50))
        ui.store.annotate_message(ui.a['id'], 'A:long', text=grown, steps=[{'tool': 'read_source', 'summary': 'done', 'ok': True}])
        ui.publish(event='task_result')
        assert ui.bottom()
        assert len(ui.messages()) == 51 and ui.messages()[-1]['text'] == grown
        before = ui.messages()
        ui.results.clear()
        ui.bridge.openChat(ui.a['id'])
        ui.wait(lambda: any(kind == 'chat' for kind, _ in ui.results))
        ui.settle()
        assert ui.bottom() and ui.messages() == before


def test_history_anchor_survives_new_output_same_count_update_tools_and_chat_switch(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.view.positionViewAtEnd()
        ui.settle()
        point = ui.view.mapToScene(QPointF(ui.view.width() / 2, ui.view.height() / 2))
        # One enormous wheel delta can overshoot to the first row depending on
        # Qt's current height estimate. Read actual middle history using normal
        # wheel steps, and measure relative to ListView's nonzero origin.
        for _ in range(60):
            QTest.wheelEvent(ui.window, point, QPoint(0, 240))
            ui.wait(lambda: not ui.view.property('moving'))
            ui.settle()
            extent = ui.view.property('contentHeight') - ui.view.height()
            history_fraction = (ui.view.property('contentY') - ui.view.property('originY')) / extent
            if .35 <= history_fraction <= .7:
                break
        assert .35 <= history_fraction <= .7
        assert not ui.bottom() and not ui.panel.property('followTail')
        key, offset = ui.anchor()
        ui.store.append(ui.a['id'], 'assistant', 'A-NEW 尾部新输出\n' + '新的正文\n' * 30,
                        kind='model_output', message_id='A:new')
        ui.publish()
        assert ui.anchor()[0] == key and abs(ui.anchor()[1] - offset) < 4
        ui.store.annotate_message(ui.a['id'], 'A:0', text='A-000 上方正文增长\n' + '新增历史行\n' * 50,
                                 steps=[{'tool': 'read_source', 'summary': '同count更新', 'ok': True}])
        ui.publish(event='task_result')
        assert ui.anchor()[0] == key and abs(ui.anchor()[1] - offset) < 4
        # Open an actual visible tool disclosure while reading history.
        # Reach the tool row with user scrolling too. A programmatic jump can
        # land on a cached but offscreen delegate while a layout settles.
        row = None
        for _ in range(80):
            row = next((item for item in ui.rows() if item.property('messageKey') == 'A:20'), None)
            top = ui.view.property('contentY')
            if row is not None and row.y() >= top + 8 and row.y() + 48 <= top + ui.view.height() - 8:
                break
            first_key = ui.anchor()[0]
            first_index = int(first_key.split(':')[-1])
            direction = 120 if first_index > 20 or (row is not None and row.y() < top + 8) else -120
            QTest.wheelEvent(ui.window, point, QPoint(0, direction))
            ui.wait(lambda: not ui.view.property('moving'))
            ui.settle()
        assert row is not None and row.y() >= ui.view.property('contentY') + 8
        assert row.y() + 48 <= ui.view.property('contentY') + ui.view.height() - 8
        toggle = row.findChild(object, 'assistantProcessToggle')
        tool_anchor = ui.anchor()
        QTest.mouseClick(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         toggle.mapToScene(QPointF(toggle.width() / 2, toggle.height() / 2)).toPoint())
        ui.settle()
        assert row.findChild(object, 'assistantProcessActivity').property('expanded')
        assert ui.anchor()[0] == tool_anchor[0] and abs(ui.anchor()[1] - tool_anchor[1]) < 4
        ui.publish()
        row = next(row for row in ui.rows() if row.property('messageKey') == 'A:20')
        assert row.findChild(object, 'assistantProcessActivity').property('expanded')
        ui.store.annotate_message(ui.a['id'], 'A:21', steps=[{'tool': 'list_nodes', 'summary': '同count步骤更新', 'ok': True}])
        ui.publish(event='task_result')
        step_row = next(item for item in ui.rows() if item.property('messageKey') == 'A:21')
        assert step_row.findChild(object, 'assistantProcessActivity').property('visible')
        assert ui.anchor()[0] == tool_anchor[0] and abs(ui.anchor()[1] - tool_anchor[1]) < 4
        assert len(ui.messages()) == 51
        ui.results.clear()
        ui.bridge.openChat(ui.b['id'])
        ui.wait(lambda: ui.panel.property('chatId') == ui.b['id'])
        ui.settle()
        assert ui.bottom() and len(ui.messages()) == 20
        assert all(row['text'].startswith('B-') for row in ui.messages())
        ui.bridge.openChat(ui.a['id'])
        ui.wait(lambda: ui.panel.property('chatId') == ui.a['id'])
        ui.settle()
        assert ui.bottom() and len(ui.messages()) == 51
        assert all(row['text'].startswith('A-') for row in ui.messages())
        assert not ui.errors, ui.errors



def test_single_tall_user_can_wheel_back_to_tail_then_follow_new_output(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtTest import QTest

    with transcript_shell(tmp_path, monkeypatch, single_long_user=True) as ui:
        ui.view.positionViewAtEnd()
        ui.settle()
        assert ui.bottom()
        point = ui.view.mapToScene(QPointF(ui.view.width() / 2, ui.view.height() / 2))
        QTest.wheelEvent(ui.window, point, QPoint(0, 1200))
        ui.settle()
        assert not ui.bottom()
        positions = []
        for _ in range(30):
            QTest.wheelEvent(ui.window, point, QPoint(0, -120))
            ui.settle()
            positions.append(ui.view.property('contentY'))
            if ui.bottom():
                break
        assert ui.bottom(), positions
        assert ui.panel.property('followTail')
        ui.store.append(ui.a['id'], 'assistant', '新的正文\n\n' * 30,
                        kind='model_output', message_id='A:later')
        ui.publish()
        assert ui.bottom() and ui.messages()[-1]['text'].startswith('新的正文')
