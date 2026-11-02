"""Real settings edits and explicit event-action dispatch; no provider is called."""

from __future__ import annotations

import threading

import pytest
from test_chat_scroll import transcript_shell
from test_granularity_actions import base
from test_static_check_ui import click, plain, visual_find


def edit_text(ui, field, text):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest

    field.forceActiveFocus()
    QTest.keyClick(ui.window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QGuiApplication.clipboard().setText(text)
    QTest.keyClick(ui.window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    ui.settle()


def test_actual_settings_save_persists_project_model_and_granularity_without_model_request(
    tmp_path, monkeypatch
):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from narrative import native_bridge
    from narrative.operations import Operations

    monkeypatch.setattr(
        native_bridge,
        "HarnessRunner",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("settings must not start model")),
    )
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        original = ops.load_project_settings()
        assert original["extraction_granularity"] == "standard"
        other = Operations.create_graph_project(tmp_path / "other-settings.nproj")
        other_before = other.load_project_settings()
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "project_settings")
        ui.settle()
        page = ui.window.findChild(object, "projectSettingsPage")
        ui.wait(lambda: page.property("loaded") and not ui.window.property("busy"))
        combo = ui.window.findChild(object, "projectGranularity")
        click(ui, combo)
        QTest.keyClick(ui.window, Qt.Key.Key_End)
        QTest.keyClick(ui.window, Qt.Key.Key_Return)
        ui.settle()
        assert page.property("granularity") == "fine"
        model = visual_find(page, "projectModel")
        edit_text(ui, model, "offline-verification-model")

        # Saving is below the field list; scroll the real form before clicking.
        def all_visual(item):
            yield item
            for child in item.childItems():
                yield from all_visual(child)

        scroll = next(item for item in all_visual(page) if item.inherits("QQuickFlickable"))
        scroll.setProperty("contentY", max(0, scroll.property("contentHeight") - scroll.height()))
        ui.settle()
        click(ui, ui.window.findChild(object, "projectSettingsSave"))
        ui.wait(lambda: "已保存" in page.property("message"))
        saved = Operations.open(ui.bridge._project_path).load_project_settings()
        assert saved["extraction_granularity"] == "fine"
        assert saved["profile"]["model"] == "offline-verification-model"
        assert saved["profile"]["reasoning_effort"] == original["profile"]["reasoning_effort"]
        assert other.load_project_settings() == other_before
        assert not any(k == "assistant" for k, _ in ui.results)


def test_project_name_rename_uses_settings_ui_and_persists_without_touching_profile(
    tmp_path, monkeypatch
):
    from narrative import native_bridge
    from narrative.operations import Operations
    from narrative.project_package import read_entry

    monkeypatch.setattr(
        native_bridge,
        "HarnessRunner",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("rename must not start model")),
    )
    with transcript_shell(tmp_path, monkeypatch) as ui:
        entry = ui.bridge._project_path
        original_profile = Operations.open(entry).load_project_settings()["profile"]
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "project_settings")
        ui.settle()
        page = ui.window.findChild(object, "projectSettingsPage")
        ui.wait(lambda: page.property("loaded") and not ui.window.property("busy"))
        name = ui.window.findChild(object, "projectName")
        edit_text(ui, name, "  改编工程新名称  ")

        # A pending model-profile edit must survive the title-only project change.
        model = visual_find(page, "projectModel")
        edit_text(ui, model, "unsaved-model-draft")
        click(ui, ui.window.findChild(object, "projectNameSave"))
        ui.wait(lambda: page.property("projectNameDraft") == "改编工程新名称")

        assert ui.window.property("project")["title"] == "改编工程新名称"
        assert "改编工程新名称" in ui.window.property("title")
        assert name.property("text") == "改编工程新名称"
        assert model.property("text") == "unsaved-model-draft"
        assert Operations.open(entry).load_project_settings()["profile"] == original_profile
        assert read_entry(entry)["project"]["title"] == "改编工程新名称"
        launcher = ui.window.findChild(object, "launcherPage")
        ui.wait(lambda: any(
            row["path"] == str(entry) and row["title"] == "改编工程新名称"
            for row in plain(launcher.property("recentItems"))
        ))

        ui.bridge.closeProject()
        ui.wait(lambda: ui.window.property("mode") == "launcher")
        ui.bridge.openProject(str(entry))
        ui.wait(lambda: plain(ui.window.property("project")).get("title") == "改编工程新名称")
        ui.window.setProperty("currentPage", "project_settings")
        ui.settle()
        ui.wait(lambda: page.property("loaded") and page.property("projectNameDraft") == "改编工程新名称")
        assert Operations.open(entry).load_project_settings()["profile"] == original_profile
        assert not any(kind == "assistant" for kind, _ in ui.results)


def dispatch_setup(ui, monkeypatch):
    from narrative.operations import Operations

    ops = Operations.open(ui.bridge._project_path)
    base(ops)
    captured = []
    monkeypatch.setattr(
        ui.bridge, "_assistant_turn", lambda text, image, chat: captured.append((text, image, chat))
    )
    entered, release = threading.Event(), threading.Event()
    original = ui.bridge._project_task

    def intercepted(kind, work, apply=None):
        if kind == "event_actions_task":

            def blocked(ops):
                entered.set()
                assert release.wait(5)
                return work(ops)

            return original(kind, blocked, apply)
        return original(kind, work, apply)

    monkeypatch.setattr(ui.bridge, "_project_task", intercepted)
    ui.window.setProperty("currentPage", "browse")
    ui.window.setProperty("assistantOpen", False)
    ui.settle()
    editor = ui.window.findChild(object, "nodeEditor")
    editor.openFor("E9")
    ui.wait(lambda: plain(editor.property("node")).get("id") == "E9")
    return ops, captured, entered, release, editor


def test_event_editor_action_button_dispatches_into_click_time_chat_and_opens_same_panel(
    tmp_path, monkeypatch
):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops, captured, entered, release, editor = dispatch_setup(ui, monkeypatch)
        before = ops.graph_snapshot()
        assert ui.bridge._assistant_chat == ui.a["id"]
        click(ui, ui.window.findChild(object, "editorExtractActions"))
        ui.wait(entered.is_set)
        assert ui.window.property("assistantOpen") and not editor.property("visible")
        ui.bridge.openChat(ui.b["id"])
        ui.wait(lambda: ui.panel.property("chatId") == ui.b["id"])
        release.set()
        ui.wait(lambda: bool(captured))
        instruction, image, chat = captured[0]
        assert (
            chat == ui.a["id"]
            and image == ""
            and "event_id=E9" in instruction
            and "action" in instruction
        )
        assert ops.graph_snapshot() == before


def test_switching_project_discards_late_event_action_request(tmp_path, monkeypatch):
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        _ops, captured, entered, release, _editor = dispatch_setup(ui, monkeypatch)
        click(ui, ui.window.findChild(object, "editorExtractActions"))
        ui.wait(entered.is_set)
        Operations.create_graph_project(tmp_path / "another-action-project.nproj")
        ui.bridge.openProject(str(tmp_path / "another-action-project.nproj"))
        ui.wait(lambda: str(ui.bridge._project_path).endswith("another-action-project.nproj"))
        release.set()
        ui.wait(lambda: not ui.window.property("busy"))
        ui.settle()
        assert captured == []



@pytest.mark.parametrize(
    "page_key,page_name,prefix",
    [("browse", "browsePage", "browseTimeline"), ("relations", "relationPage", "relationCut")],
)
def test_fifth_action_picker_column_selects_and_restores_real_action_path(
    tmp_path, monkeypatch, page_key, page_name, prefix
):
    from test_granularity_actions import action_batch

    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        _sid, _obj, objects = action_batch(ops)
        assert (
            ops.apply_changes(
                objects, [], ops.graph_project_info()["revision"], reason="动作选择器隔离数据"
            )["status"]
            == "accepted"
        )
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", page_key)
        ui.settle()
        ui.wait(lambda: not ui.window.property("busy"))
        page = ui.window.findChild(object, page_name)
        page.setProperty("moments", ops.browse_view()["moments"])
        page.setProperty("cutId", "A1")
        picker = ui.window.findChild(object, prefix + "Popup")
        picker.open()
        ui.wait(lambda: picker.property("opened"))
        view = visual_find(ui.window.contentItem(), prefix + "Level4")
        assert view is not None
        ui.wait(lambda:view.width()>0 and view.height()>0)

        def rows():
            view.forceLayout()
            return [
                r
                for r in view.property("contentItem").childItems()
                if r.property("modelData") and r.width() > 0
            ]

        ui.wait(lambda: len(rows()) == 2)
        assert next(r for r in rows() if plain(r.property("modelData"))["id"] == "A1").property(
            "highlighted"
        )
        chosen = next(r for r in rows() if plain(r.property("modelData"))["id"] == "A99")
        click(ui, chosen)
        ui.wait(lambda: page.property("cutId") == "A99")
        assert picker.property("opened")
        picker.close()
        ui.settle()
        picker.open()
        ui.wait(lambda: picker.property("opened"))
        assert next(r for r in rows() if plain(r.property("modelData"))["id"] == "A99").property(
            "highlighted"
        )
