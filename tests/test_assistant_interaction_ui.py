"""Assistant selectable text and formatted tools use real Qt input."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click


def test_answer_selection_context_copy_and_formatted_tool_output(tmp_path, monkeypatch):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    monkeypatch.setenv('QT_QUICK_CONTROLS_STYLE', 'Fusion')
    with transcript_shell(tmp_path, monkeypatch) as ui:
        chat = ui.store.create('可选择文本')
        ui.store.append(chat['id'], 'assistant', '可以选择复制的回答文本。\n第二行仍然可读。', kind='model_output', message_id='copy-answer')
        ui.bridge.openChat(chat['id'])
        ui.wait(lambda: ui.panel.property('chatId') == chat['id'])
        render_ready(ui)
        answer = next(item for item in visuals(ui.view) if item.objectName() == 'assistantAnswer' and item.property('visible') and item.property('text'))
        point = answer.mapToScene(QPointF(8, answer.height()/2)).toPoint()
        QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        QTest.mouseMove(ui.window, point + __import__('PySide6.QtCore', fromlist=['QPoint']).QPoint(95, 0), 80)
        QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point + __import__('PySide6.QtCore', fromlist=['QPoint']).QPoint(95, 0))
        ui.wait(lambda: bool(answer.property('selectedText')))
        selected = answer.property('selectedText')
        QTest.keyClick(ui.window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert ui.app.clipboard().text() == selected
        QTest.mouseClick(ui.window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
        ui.wait(lambda: any(item.objectName() == 'assistantCopyWhole' and item.property('visible') for item in visuals(ui.window.contentItem())))
        whole = next(item for item in visuals(ui.window.contentItem()) if item.objectName() == 'assistantCopyWhole' and item.property('visible'))
        click(ui, whole)
        assert '第二行仍然可读' in ui.app.clipboard().text()
        # Completed formatter fields are merged into the model tool call.
        ui.store.append(chat['id'], 'assistant', '', kind='model_output', message_id='tool-formatted', task_id='format-task',
                        content=[{'type': 'tool_use', 'id': 'call-1', 'name': 'list_nodes', 'input': {}}])
        ui.store.append(chat['id'], 'assistant', '', kind='assistant_result', task_id='format-task', steps=[{'tool':'list_nodes','args':{},'display_name':'查看结构目录','arguments_text':'{\n  "类型": "事件"\n}','output_text':'{\n  "节点": []\n}','output_truncated':True,'arguments_truncated':True}])
        ui.bridge.openChat(chat['id'])
        ui.settle()
        process = next(item for item in visuals(ui.view) if item.objectName() == 'assistantProcessToggle' and item.property('visible'))
        click(ui, process)
        toggle = next(item for item in visuals(ui.view) if item.objectName() == 'assistantToolToggle' and item.property('visible'))
        assert '查看结构目录' in toggle.property('text')
        click(ui, toggle)
        detail = next(item for item in visuals(ui.view) if item.objectName() == 'assistantToolDetails' and item.property('visible'))
        assert '参数\n{\n  "类型"' in detail.property('text')
        assert '输出\n{\n  "节点"' in detail.property('text')
        assert '参数预览已截断' in detail.property('text')
        assert '输出预览已截断' in detail.property('text')
        assert detail.property('selectByMouse')


def test_multiple_attachments_remove_and_busy_editor_queue_receipts(tmp_path, monkeypatch):
    from PySide6.QtTest import QTest
    from test_static_check_ui import plain

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ui.panel.attach(['file:///C:/a/one.png', 'file:///C:/a/two.txt', 'file:///C:/a/three.pdf'])
        ui.settle()
        assert len(plain(ui.panel.property('attachments'))) == 3
        remove = next(item for item in visuals(ui.panel) if item.objectName() == 'assistantRemoveAttachment' and item.property('visible'))
        click(ui, remove)
        assert len(plain(ui.panel.property('attachments'))) == 2
        current = ui.panel.property('chatId')
        project = str(ui.bridge._project_path)
        ui.bridge.resultReady.emit('conversation_progress', {'event':'assistant_started','chat_id':current,'task_id':'current-steer-task','project_path':project,'busy':True})
        ui.settle()
        prompt = ui.panel.findChild(object, 'assistantPrompt')
        assert prompt.property('enabled')
        click(ui, prompt)
        for character in 'continue this task':
            QTest.keyClick(ui.window, character)
        assert ui.panel.findChild(object, 'assistantSend').property('enabled')
        assert ui.panel.findChild(object, 'assistantSend').property('text') == '插话'
        files = plain(ui.panel.property('attachments'))
        import json
        ui.panel.setProperty('pendingSteering', {'chat_id':current,'task_id':'current-steer-task','text':'continue this task','files':json.dumps(files,separators=(',',':'))})
        ui.panel.setProperty('steeringPending', True)
        ui.bridge.resultReady.emit('assistant_steer', {'chat_id':current,'task_id':'wrong-task','project_path':project,'status':'queued'})
        ui.settle()
        assert ui.panel.property('steeringPending')
        ui.bridge.resultReady.emit('assistant_steer', {'chat_id':current,'task_id':'current-steer-task','project_path':project,'status':'delivered','message_id':'earlier-message'})
        ui.settle()
        assert ui.panel.property('steeringPending')
        assert ui.panel.property('draft') == 'continue this task'
        ui.bridge.resultReady.emit('assistant_steer', {'chat_id':current,'task_id':'current-steer-task','project_path':project,'status':'queued'})
        ui.wait(lambda:not ui.panel.property('steeringPending'))
        assert ui.panel.property('draft') == ''
        assert plain(ui.panel.property('attachments')) == []
        assert '尚未确认送达' in ui.panel.findChild(object,'assistantSteeringNotice').property('text')
        assert ui.panel.property('busy')
        ui.bridge.resultReady.emit('assistant_steer', {'chat_id':current,'task_id':'current-steer-task','project_path':project,'status':'delivered'})
        ui.wait(lambda:'已送达' in ui.panel.findChild(object,'assistantSteeringNotice').property('text'))
        ui.bridge.openChat(ui.b['id'])
        ui.wait(lambda:ui.panel.property('chatId') == ui.b['id'])
        assert not ui.panel.property('steeringNotice')
        assert plain(ui.panel.property('attachments')) == []


def test_busy_send_button_calls_native_same_task_with_multiple_real_files(tmp_path, monkeypatch):
    import threading

    from test_static_check_ui import plain

    with transcript_shell(tmp_path, monkeypatch) as ui:
        current = ui.panel.property('chatId')
        project = str(ui.bridge._project_path)
        first = tmp_path / 'note.txt'
        first.write_text('attachment text', encoding='utf-8')
        second = tmp_path / 'sample.bin'
        second.write_bytes(b'\x00\x01\x02')
        calls = []
        class Runtime:
            def steer(self, chat_id, task_id, nonce, blocks, *, message_id=None, on_status=None):
                calls.append({'chat':chat_id,'task':task_id,'nonce':nonce,'blocks':blocks,'receipt':on_status,'message':message_id})
                return {'status':'queued','message_id':message_id}
        ui.bridge._assistant_jobs[-900] = {'chat_id':current,'task_id':'native-steer-task','binding_nonce':'frozen-nonce','runtime':Runtime(),'cancel':threading.Event()}
        try:
            ui.bridge.resultReady.emit('conversation_progress', {'event':'assistant_started','chat_id':current,'task_id':'native-steer-task','project_path':project,'busy':True})
            ui.settle()
            ui.panel.attach([first.as_uri(), second.as_uri()])
            ui.panel.setProperty('draft', 'additional direction')
            click(ui, ui.panel.findChild(object,'assistantSend'))
            ui.wait(lambda:len(calls)==1 and not ui.panel.property('steeringPending'))
            assert calls[0]['task'] == 'native-steer-task'
            assert calls[0]['nonce'] == 'frozen-nonce'
            assert ui.panel.property('busy')
            assert ui.panel.property('draft') == ''
            assert plain(ui.panel.property('attachments')) == []
            saved = next(row for row in ui.store.load(current)['messages'] if row.get('kind') == 'user_steering')
            assert saved['steering_status'] == 'queued'
            assert len(saved['attachments']) == 2
            assert {row['kind'] for row in saved['attachments']} == {'text','file'}
            calls[0]['receipt']({'status':'delivered'})
            ui.wait(lambda:'已送达' in ui.panel.findChild(object,'assistantSteeringNotice').property('text'))
            assert next(row for row in ui.store.load(current)['messages'] if row.get('kind') == 'user_steering')['steering_status'] == 'delivered'
            # A stale task is not converted into a new task; local input survives.
            ui.bridge._assistant_jobs.pop(-900)
            ui.panel.setProperty('draft', 'keep my unsent direction')
            click(ui, ui.panel.findChild(object,'assistantSend'))
            ui.wait(lambda:not ui.panel.property('steeringPending'))
            assert ui.panel.property('draft') == 'keep my unsent direction'
            assert '未送达' in ui.panel.findChild(object,'assistantSteeringNotice').property('text')
            assert len(calls) == 1
        finally:
            ui.bridge._assistant_jobs.pop(-900, None)
