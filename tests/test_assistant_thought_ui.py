"""Only explicitly classified task commentary is folded as thought."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click


def test_explicit_thought_groups_default_folded_answers_visible_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        chat = ui.store.create("思考与正文")
        for task, thoughts, answer in (("A", ["先核对本场的声音依据。", "再检查出现范围。"], "这是用户要求保留可见的创作正文。"), ("B", ["这里调整视觉表达。"], "这是普通问答的回答。")):
            for index, text in enumerate(thoughts):
                ui.store.append(chat["id"], "assistant", text, kind="model_output", task_id=task, message_id=task+str(index), display_kind="thought")
            ui.store.append(chat["id"], "assistant", answer, kind="model_output", task_id=task, message_id=task+"answer")
        ui.bridge.openChat(chat["id"])
        ui.wait(lambda: ui.panel.property("chatId") == chat["id"] and len(ui.messages()) == 4)
        render_ready(ui)
        groups = [row for row in ui.rows() if any(x.objectName()=="assistantProcessToggle" and "思考" in x.property("text") for x in visuals(row))]
        assert len(groups) == 2
        first = groups[0].findChild(object, "assistantProcessActivity")
        second = groups[1].findChild(object, "assistantProcessActivity")
        assert not first.property("expanded") and not second.property("expanded")
        thought = first.findChild(object, "assistantThoughtText")
        assert not thought.property("visible")
        answers = [x.property("text") for row in ui.rows() for x in visuals(row) if x.objectName()=="assistantAnswer" and x.property("visible")]
        assert any("创作正文" in text for text in answers) and any("普通问答" in text for text in answers)
        click(ui, first.findChild(object, "assistantProcessToggle"))
        assert thought.property("visible") and "声音依据" in thought.property("text") and "出现范围" in thought.property("text")
        assert not second.property("expanded")
        ui.bridge.openChat(ui.b["id"])
        ui.wait(lambda: ui.panel.property("chatId") == ui.b["id"])
        ui.bridge.openChat(chat["id"])
        ui.wait(lambda: ui.panel.property("chatId") == chat["id"] and len(ui.messages()) == 4)
        render_ready(ui)
        groups = [x for row in ui.rows() for x in visuals(row) if x.objectName()=="assistantProcessActivity" and x.property("visible")]
        assert [group.property("expanded") for group in groups] == [True, False]

def test_live_thought_with_audit_metadata_survives_terminal_snapshot_and_reopen(tmp_path, monkeypatch):
    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        chat = ui.store.create("实时公开说明")
        ui.bridge.openChat(chat["id"])
        ui.wait(lambda: ui.panel.property("chatId") == chat["id"])
        task = "live-thought-task"
        ui.store.append(chat["id"], "assistant", "先核对当前事件线。", kind="model_output", task_id=task,
                        message_id="live-note", display_kind="thought", content=[{"type": "text", "text": "先核对当前事件线。"}])
        ui.bridge._conversation_event(ops, ui.bridge._project_path, {"event": "model_message", "chat_id": chat["id"], "task_id": task})
        ui.wait(lambda: len(ui.messages()) == 1)
        render_ready(ui)

        def groups():
            return [x for row in ui.rows() for x in visuals(row) if x.objectName() == "assistantProcessActivity" and x.property("visible")]

        assert len(groups()) == 1 and not groups()[0].property("expanded")
        first = groups()[0]
        click(ui, first.findChild(object, "assistantProcessToggle"))
        assert "事件线" in groups()[0].findChild(object, "assistantThoughtText").property("text")
        # A real terminal snapshot may attach task/audit metadata. Classification wins.
        ui.store.annotate_message(chat["id"], "live-note", audit_directory=str(tmp_path / "audit"), steps=[])
        ui.store.append(chat["id"], "assistant", "最终答复仍正常可见。", kind="model_output", task_id=task,
                        message_id="live-answer", display_kind="answer")
        ui.bridge._conversation_event(ops, ui.bridge._project_path, {"event": "task_complete", "chat_id": chat["id"], "task_id": task}, busy=False)
        ui.wait(lambda: len(ui.messages()) == 2)
        render_ready(ui)
        assert len(groups()) == 1 and groups()[0].property("expanded")
        assert "事件线" in groups()[0].findChild(object, "assistantThoughtText").property("text")
        assert any(x.property("visible") and "最终答复" in x.property("text") for row in ui.rows() for x in visuals(row) if x.objectName() == "assistantAnswer")
        ui.bridge.openChat(ui.b["id"])
        ui.wait(lambda: ui.panel.property("chatId") == ui.b["id"])
        ui.bridge.openChat(chat["id"])
        ui.wait(lambda: ui.panel.property("chatId") == chat["id"] and len(ui.messages()) == 2)
        render_ready(ui)
        assert len(groups()) == 1 and groups()[0].property("expanded")
        assert "事件线" in groups()[0].findChild(object, "assistantThoughtText").property("text")
        assert not ui.errors


def test_live_tool_only_thought_explains_missing_public_note_without_raw_reasoning(tmp_path, monkeypatch):
    from narrative.operations import Operations

    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        chat = ui.store.create("无公开说明")
        ui.bridge.openChat(chat["id"])
        ui.wait(lambda: ui.panel.property("chatId") == chat["id"])
        ui.store.append(chat["id"], "assistant", "", kind="model_output", task_id="tool-only", message_id="tool-only-message",
                        display_kind="thought", content=[{"type":"reasoning", "text":"不得显示的原始推理"},
                        {"type":"tool_call", "name":"project_info", "arguments":{}}])
        ui.bridge._conversation_event(ops, ui.bridge._project_path, {"event":"model_message", "chat_id":chat["id"], "task_id":"tool-only"})
        ui.wait(lambda: len(ui.messages()) == 1)
        render_ready(ui)
        group = next(x for row in ui.rows() for x in visuals(row) if x.objectName() == "assistantProcessActivity" and x.property("visible"))
        click(ui, group.findChild(object, "assistantProcessToggle"))
        note = group.findChild(object, "assistantThoughtUnavailable")
        assert note.property("visible") and note.property("text") == "本轮模型未提供过程说明"
        assert not group.findChild(object, "assistantThoughtText").property("visible")
        assert "不得显示" not in "\n".join(str(x.property("text") or "") for row in ui.rows() for x in visuals(row))
        assert not ui.errors
