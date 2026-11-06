"""Stopping an absent task settles exact UI identity without touching a successor."""
from test_chat_scroll import transcript_shell
from test_static_check_ui import click, plain


def test_stop_missing_task_settles_ui_and_late_old_end_keeps_successor(tmp_path, monkeypatch):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        current = ui.panel.property('chatId')
        project = str(ui.bridge._project_path)
        def event(task, busy, kind='assistant_started'):
            ui.bridge.resultReady.emit('conversation_progress', {'event':kind,'chat_id':current,'task_id':task,'project_path':project,'busy':busy})
            ui.settle()
        event('no-longer-in-host', True)
        stop = ui.panel.findChild(object, 'assistantStop')
        click(ui, stop)
        ui.wait(lambda:not ui.panel.property('busy'))
        assert ui.panel.property('stoppingTaskId') == ''
        assert current not in plain(ui.panel.property('activeTaskIds'))
        assert ui.panel.findChild(object,'assistantPrompt').property('enabled')
        event('new-real-task', True)
        ui.panel.setProperty('stoppingTaskId', 'old-task')
        ui.bridge.resultReady.emit('assistant_stop', {'chat_id':current,'task_id':'old-task','project_path':project,'status':'not_running'})
        event('old-task', False, 'task_complete')
        assert ui.panel.property('stoppingTaskId') == ''
        assert ui.panel.property('busy')
        assert plain(ui.panel.property('activeTaskIds'))[current] == 'new-real-task'
        ui.panel.setProperty('stoppingTaskId', 'new-real-task')
        event('old-task', False, 'task_complete')
        assert ui.panel.property('stoppingTaskId') == 'new-real-task'
        event('new-real-task', False, 'task_complete')
        assert ui.panel.property('stoppingTaskId') == ''
        assert not ui.panel.property('busy')


def test_activity_snapshot_reopen_and_stale_snapshot_preserve_live_task(tmp_path, monkeypatch):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        current=ui.panel.property('chatId')
        project=str(ui.bridge._project_path)
        # A persisted historical running row is content, never live host state.
        ui.store.append(current,'system','historic running task',kind='task_started',task_id='historic-task',busy=True)
        ui.bridge.openChat(current)
        ui.wait(lambda:any(row.get('task_id')=='historic-task' for row in ui.store.load(current)['messages']))
        ui.settle()
        assert not ui.panel.property('busy')
        sequence=ui.panel.property('activitySequence')+100
        ui.bridge.resultReady.emit('assistant_activity',{'project_path':project,'active_tasks':{current:'live-task'},'activity_sequence':sequence})
        ui.wait(lambda:ui.panel.property('busy'))
        ui.panel.setProperty('stoppingTaskId','live-task')
        ui.bridge.resultReady.emit('assistant_activity',{'project_path':project,'active_tasks':{},'activity_sequence':sequence-1})
        ui.settle()
        assert ui.panel.property('busy')
        assert ui.panel.property('stoppingTaskId')=='live-task'
        assert plain(ui.panel.property('activeTaskIds'))[current]=='live-task'
        ui.bridge.resultReady.emit('assistant_activity',{'project_path':project,'active_tasks':{},'activity_sequence':sequence+1})
        ui.wait(lambda:not ui.panel.property('busy'))
        assert ui.panel.property('stoppingTaskId')==''
        assert plain(ui.panel.property('activeTaskIds'))=={}
