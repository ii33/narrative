"""Program-owned task receipts stay distinct from model prose and other tasks."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click, plain


def test_program_commit_cards_terminal_states_persist_and_do_not_cross_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QUICK_CONTROLS_STYLE", "Fusion")
    with transcript_shell(tmp_path, monkeypatch) as ui:
        receipts = []
        for index, status in enumerate(("completed", "failed", "cancelled")):
            chat = ui.store.create("程序总结 " + status)
            task = "task-receipt-" + str(index)
            summary = {"source": "program", "status": status, "task_id": task, "text": "无入库变更" if status == "completed" else "已保留成功入库的制作元素。", "has_changes": status != "completed", "no_changes": status == "completed", "compact_text": ("完成 · 无入库变更" if status == "completed" else ("失败" if status == "failed" else "停止")+" · 新增元素1"), "committed_operations": 0 if status == "completed" else 1,
                       "changes": [] if status == "completed" else [{"domain": "production", "action": "created", "object_type": "element", "id": "element-7", "name": "窗外雨声", "count": 1, "occurrences": 1}],
                       "diagnostics": [] if status == "completed" else [{"severity": "warning", "message": "任务结束前的成功写入仍保留。"}]}
            if status == "failed":
                summary["changes"][0].update(action="saved", action_label="保存", object_type="scene_card", type_label="场卡")
            ui.store.append(chat["id"], "assistant", "这段是模型的创作解释。", kind="model_output", task_id=task, message_id=task+":model")
            ui.store.append(chat["id"], "system", summary["text"], kind="task_summary", task_id=task, message_id=task+":summary", commit_summary=summary)
            ui.bridge.openChat(chat["id"])
            ui.wait(lambda chat=chat: ui.panel.property("chatId") == chat["id"] and len(ui.messages()) == 2)
            render_ready(ui)
            cards = [item for row in ui.rows() for item in visuals(row) if item.objectName() == "assistantSubmissionSummary" and item.property("visible")]
            assert len(cards) == 1
            card = cards[0]
            assert card.property("taskId") == task
            assert card.findChild(object, "assistantSubmissionText").property("text") == summary["compact_text"]
            assert plain(ui.panel.property("rawMessages"))[1]["commitSummary"] == summary
            if summary["has_changes"]:
                click(ui, card.findChild(object, "assistantSubmissionToggle"))
                detail = next(x for x in visuals(card) if x.objectName() == "assistantSubmissionChange")
                assert ("场卡" if status == "failed" else "制作元素") in detail.property("text")
                assert "窗外雨声" in detail.property("text")
                if status == "failed":
                    assert "保存" in detail.property("text") and "scene_card" not in detail.property("text")
                assert "次操作" not in detail.property("text") and "element-7" not in detail.property("text")
                assert card.property("expanded")
            else:
                assert not card.property("expanded")
            receipts.append((chat, task, summary))
        chat, task, summary = receipts[0]
        ui.bridge.openChat(chat["id"])
        ui.wait(lambda: ui.panel.property("chatId") == chat["id"])
        render_ready(ui)
        assert ui.messages()[1]["commitSummary"] == summary
        assert len(ui.messages()) == 2  # reload never creates an extra receipt
        assert "创作解释" in ui.messages()[0]["text"]
