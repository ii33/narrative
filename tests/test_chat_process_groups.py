"""Actual QML groups only task-local tool process rows, without rewriting chat."""
from __future__ import annotations

import json

from test_chat_scroll import transcript_shell
from test_static_check_ui import click, plain


def activities(item):
    found = [item] if item.objectName() == "assistantToolActivity" else []
    for child in item.childItems():
        found.extend(activities(child))
    return found


def add_tool(ui, seq, *, task="task-A", text="", role="assistant", image=None):
    content=[{"type":"tool-call","id":f"call-{seq}","name":"read_source",
              "arguments":json.dumps({"marker":str(seq)})}]
    ui.store.append(ui.a["id"],role,text,kind="model_output",message_id=f"group:{seq}",
                    task_id=task,content=content,image=image,full_output=json.dumps(content))


def test_consecutive_calls_are_one_collapsed_block_and_live_growth_retains_expansion_and_anchor(tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtTest import QTest

    with transcript_shell(tmp_path,monkeypatch) as ui:
        for seq in range(3):
            add_tool(ui,seq)
        saved=ui.store.load(ui.a["id"])
        older=saved
        ui.publish()
        assert len(ui.messages()) == 51
        assert len(plain(ui.panel.property("rawMessages"))) == 53
        row=next(r for r in ui.rows() if r.property("messageKey") == "group:0")
        activity=row.findChild(object,"assistantProcessActivity")
        assert not activity.property("expanded")
        assert len(ui.messages()[-1]["toolBatches"]) == 3
        click(ui,row.findChild(object,"assistantProcessToggle"))
        ui.settle()
        assert activity.property("expanded")
        batches=activities(row)
        assert len(batches) == 3 and all(not b.property("expanded") for b in batches)
        assert batches[0].x() >= 12
        click(ui,batches[0].findChild(object,"assistantToolToggle"))
        ui.settle()
        assert batches[0].property("expanded")
        assert not batches[1].property("expanded")
        click(ui,row.findChild(object,"assistantProcessToggle"))
        ui.settle()
        assert not activity.property("expanded")
        click(ui,row.findChild(object,"assistantProcessToggle"))
        ui.settle()
        batches=activities(row)
        assert batches[0].property("expanded") and not batches[1].property("expanded")
        assert ui.store.load(ui.a["id"]) == saved
        add_tool(ui,3)
        saved=ui.store.load(ui.a["id"])
        ui.publish()
        assert len(ui.messages()) == 51 and ui.bottom()
        group=ui.messages()[-1]
        assert group["messageKey"] == "group:0"
        assert group["sourceMessageKeys"] == [f"group:{i}" for i in range(4)]
        row=next(r for r in ui.rows() if r.property("messageKey") == "group:0")
        assert row.findChild(object,"assistantProcessActivity").property("expanded")
        batches=activities(row)
        assert len(batches) == 4 and batches[0].property("expanded")
        assert all(not b.property("expanded") for b in batches[1:])
        visible=sorted([r for r in ui.rows() if r.y()+r.height()>ui.view.property("contentY") and r.y()<ui.view.property("contentY")+ui.view.height()],key=lambda r:r.y())
        assert all(a.y()+a.height() <= b.y()+1 for a,b in zip(visible,visible[1:],strict=False))
        point=ui.view.mapToScene(QPointF(ui.view.width()/2,ui.view.height()/2))
        for _ in range(60):
            QTest.wheelEvent(ui.window,point,QPoint(0,240))
            ui.wait(lambda:not ui.view.property("moving"))
            ui.settle()
            extent=ui.view.property("contentHeight")-ui.view.height()
            fraction=(ui.view.property("contentY")-ui.view.property("originY"))/extent
            if .35 <= fraction <= .7:
                break
        assert .35 <= fraction <= .7 and not ui.panel.property("followTail")
        anchor=ui.anchor()
        add_tool(ui,4)
        saved=ui.store.load(ui.a["id"])
        ui.publish()
        assert ui.anchor()[0] == anchor[0] and abs(ui.anchor()[1]-anchor[1]) < 4
        assert len(ui.messages()) == 51 and sum(len(b["tools"]) for b in ui.messages()[-1]["toolBatches"]) == 5
        ui.publish(event="task_result")
        assert len(ui.messages()) == 51 and sum(len(b["tools"]) for b in ui.messages()[-1]["toolBatches"]) == 5
        assert ui.store.load(ui.a["id"]) == saved
        ui.panel.applyChat(older)
        ui.settle()
        assert len(ui.messages()) == 51 and sum(len(b["tools"]) for b in ui.messages()[-1]["toolBatches"]) == 5
        assert ui.anchor()[0] == anchor[0] and abs(ui.anchor()[1]-anchor[1]) < 4


def test_prose_feedback_images_and_different_or_unknown_tasks_split_process_groups(tmp_path,monkeypatch):
    with transcript_shell(tmp_path,monkeypatch) as ui:
        add_tool(ui,0)
        add_tool(ui,1)
        add_tool(ui,2,text="独立的模型正文。")
        add_tool(ui,3)
        ui.store.append(ui.a["id"],"system","已入库的程序反馈。",task_id="task-A")
        add_tool(ui,4)
        add_tool(ui,5,task="task-B")
        image=tmp_path/"fixture.svg"
        image.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>',encoding="utf-8")
        add_tool(ui,6,task="task-B",image=str(image))
        add_tool(ui,7,task="task-B")
        add_tool(ui,8,task="")
        add_tool(ui,9,task="")
        for seq in range(2):
            ui.store.append(ui.a["id"],"assistant","",task_id="legacy-task",
                            steps=[{"tool":"list_nodes","args":{"part":seq}}])
        saved=ui.store.load(ui.a["id"])
        ui.publish()
        displayed=ui.messages()[50:]
        assert len(displayed) == 11
        assert sum(len(b["tools"]) for b in displayed[0]["toolBatches"]) == 2
        assert displayed[1]["text"] == "独立的模型正文。"
        assert displayed[3]["role"] == "system" and displayed[3]["text"] == "已入库的程序反馈。"
        assert displayed[5]["taskId"] == "task-B" and displayed[6]["image"]
        assert displayed[8]["taskId"] == displayed[9]["taskId"] == ""
        assert sum(len(b["tools"]) for b in displayed[-1]["toolBatches"]) == 2
        assert ui.store.load(ui.a["id"]) == saved


def test_split_process_group_does_not_leak_expanded_state_to_reused_delegate(tmp_path, monkeypatch):
    with transcript_shell(tmp_path,monkeypatch) as ui:
        for seq in range(3):
            add_tool(ui,seq)
        ui.publish()
        first=next(row for row in ui.rows() if row.property("messageKey") == "group:0")
        click(ui,first.findChild(object,"assistantProcessToggle"))
        ui.settle()
        assert first.findChild(object,"assistantProcessActivity").property("expanded")
        add_tool(ui,3,text="新的正文分开显示。")
        add_tool(ui,4)
        ui.publish()
        new=next(row for row in ui.rows() if row.property("messageKey") == "group:4")
        assert not new.findChild(object,"assistantProcessActivity").property("expanded")
        ui.store.annotate_message(ui.a["id"],"group:1",text="补充到原消息的正文。")
        ui.publish()
        ui.view.positionViewAtEnd()
        ui.settle()
        assert [row["messageKey"] for row in ui.messages()[50:]] == [f"group:{i}" for i in range(5)]
        for row in ui.rows():
            if row.property("messageKey") in {"group:1","group:2","group:3","group:4"}:
                assert not row.findChild(object,"assistantProcessActivity").property("expanded")
        assert any(value is True for value in plain(ui.panel.property("expandedProcesses")).values())


def test_identical_message_keys_in_other_chat_have_independent_expansion(tmp_path, monkeypatch):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        add_tool(ui, 0)
        ui.publish()
        row=next(r for r in ui.rows() if r.property("messageKey") == "group:0")
        click(ui,row.findChild(object,"assistantProcessToggle"))
        ui.settle()
        batch = activities(row)[0]
        click(ui,batch.findChild(object,"assistantToolToggle"))
        ui.settle()
        other_chat = ui.store.create("独立展开状态")
        ui.store.append(other_chat["id"],"assistant","",kind="model_output",message_id="group:0",task_id="task-A",
                        content=[{"type":"tool-call","name":"list_nodes","arguments":"{}"}],
                        full_output=json.dumps([{"type":"tool-call","name":"list_nodes","arguments":"{}"}]))
        ui.bridge.openChat(other_chat["id"])
        ui.wait(lambda: ui.panel.property("chatId") == other_chat["id"])
        ui.settle()
        ui.view.positionViewAtEnd()
        ui.settle()
        other=next(r for r in ui.rows() if r.property("messageKey") == "group:0" and r.height() > 0)
        assert not other.findChild(object,"assistantProcessActivity").property("expanded")
        click(ui,other.findChild(object,"assistantProcessToggle"))
        ui.settle()
        assert other.findChild(object,"assistantProcessActivity").property("expanded")
        ui.wait(lambda: bool(activities(other)))
        other_batch = activities(other)[0]
        assert not other_batch.property("expanded")
        ui.bridge.openChat(ui.a["id"])
        ui.wait(lambda: ui.panel.property("chatId") == ui.a["id"])
        ui.settle()
        back=next(r for r in ui.rows() if r.property("messageKey") == "group:0" and r.height() > 0)
        assert back.findChild(object,"assistantProcessActivity").property("expanded")
        assert activities(back)[0].property("expanded")
