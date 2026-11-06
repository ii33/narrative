"""Completed tool projection cannot interrupt final answers or hide unmatched results."""
from test_chat_scroll import transcript_shell
from test_static_check_ui import plain


def test_completed_tool_decorations_and_two_final_answers_survive_reopen(tmp_path, monkeypatch):
    import json

    from PySide6.QtCore import qInstallMessageHandler

    warnings=[]
    previous=qInstallMessageHandler(lambda kind,context,message:warnings.append(message))
    try:
        with transcript_shell(tmp_path,monkeypatch) as ui:
            chat=ui.store.create('完整工具结果')
            first_answer='第一轮完整答复。'*202
            second_answer='第二轮完整答复。'*139
            ui.store.append(chat['id'],'user','开始',task_id='first-task')
            calls=[{'type':'tool-call','id':'call-'+str(i),'name':'project_info' if i==0 else 'read_screenplay','arguments':json.dumps({'screenplay_id':None}) if i==1 else '{}'} for i in range(2)]
            ui.store.append(chat['id'],'assistant','',kind='model_output',task_id='first-task',display_kind='thought',content=calls,full_output=json.dumps(calls))
            ui.bridge.openChat(chat['id'])
            ui.wait(lambda:ui.panel.property('chatId')==chat['id'])
            assert not any(row.get('text')==second_answer for row in ui.messages())
            steps=[{'tool':call['name'],'args':json.loads(call['arguments']),'display_name':'已完成工具'+str(i),'output_text':'执行结果'+str(i)} for i,call in enumerate(calls)]
            ui.store.append(chat['id'],'assistant',first_answer,kind='model_output',task_id='first-task',display_kind='answer',steps=steps)
            ui.store.append(chat['id'],'system','完成',kind='task_summary',task_id='first-task',commit_summary={'compact_text':'完成：无入库变更'})
            ui.store.append(chat['id'],'user','如何',task_id='second-task')
            ui.store.append(chat['id'],'assistant',second_answer,kind='model_output',task_id='second-task',display_kind='answer')
            ui.bridge.resultReady.emit('conversation_progress',{'event':'task_complete','chat_id':chat['id'],'task_id':'second-task','project_path':str(ui.bridge._project_path),'chat':ui.store.load(chat['id']),'busy':False})
            ui.wait(lambda:any(row.get('text')==second_answer for row in ui.messages()))
            ui.bridge.openChat(chat['id'])
            ui.settle()
            rows=ui.messages()
            assert any(row.get('text')==first_answer for row in rows)
            tools=[tool for row in rows for batch in row.get('toolBatches',[]) for tool in batch.get('tools',[])]
            assert [tool.get('output_text') for tool in tools]==['执行结果0','执行结果1']
            assert not [message for message in warnings if 'AssistantPanel.qml' in message and ('TypeError' in message or 'ReferenceError' in message)], warnings
            assert len(plain(ui.panel.property('rawMessages')))==6
    finally:
        qInstallMessageHandler(previous)
