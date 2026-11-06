"""Shared real QML browse/editor surface keeps adaptation writes isolated."""
from __future__ import annotations

from test_chat_scroll import transcript_shell
from test_static_check_ui import click, plain


def test_shared_adaptation_new_editor_suppression_and_real_mouse_reorder(tmp_path, monkeypatch):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        before = ops.graph_snapshot()
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "adaptation")
        page = ui.window.findChild(object, "adaptationBrowsePage")
        ui.settle()
        for name in ("场景甲", "场景乙"):
            click(ui, page.findChild(object, "adaptationNewNode"))
            editor = page.findChild(object, "nodeEditor")
            ui.wait(lambda editor=editor: editor.property("visible"))
            editor.findChild(object, "editorName").setProperty("text", name)
            editor.findChild(object, "editorType").setProperty("currentIndex", 6)
            click(ui, editor.findChild(object, "editorSubmitNode"))
            ui.wait(lambda editor=editor: not editor.property("visible"))
        ui.wait(lambda: len(plain(page.property("nodes"))) == 2)
        view = page.findChild(object, "browseNodeList")
        view.forceLayout()
        rows = sorted((r for r in view.property("contentItem").childItems()
                       if r.objectName() == "browseNodeRow"), key=lambda r: r.y())
        first_id = rows[0].property("id")
        start = rows[0].mapToScene(QPointF(rows[0].width() / 2, rows[0].height() / 2)).toPoint()
        end = rows[1].mapToScene(QPointF(rows[1].width() / 2, rows[1].height() / 2)).toPoint()
        original_revision = page.property("revision")
        QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(ui.window, end, 100)
        QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        ui.wait(lambda: page.property("revision") > original_revision)
        assert ops.adaptation_snapshot()["nodes"][-1]["id"] == first_id
        # Drag it back upward; dropping on the first row must reach the front.
        view.forceLayout()
        rows = sorted((r for r in view.property("contentItem").childItems()
                       if r.objectName() == "browseNodeRow"), key=lambda r: r.y())
        start = rows[1].mapToScene(QPointF(rows[1].width() / 2, rows[1].height() / 2)).toPoint()
        end = rows[0].mapToScene(QPointF(rows[0].width() / 2, rows[0].height() / 2)).toPoint()
        original_revision = page.property("revision")
        QTest.mousePress(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(ui.window, end, 100)
        QTest.mouseRelease(ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        ui.wait(lambda: page.property("revision") > original_revision)
        assert ops.adaptation_snapshot()["nodes"][0]["id"] == first_id
        assert ops.graph_snapshot() == before
        # The same editor now contains adaptation-only suppression.
        view.forceLayout()
        row = next(r for r in view.property("contentItem").childItems()
                   if r.objectName() == "browseNodeRow" and r.property("id") == first_id)
        click(ui, row)
        ui.wait(lambda: plain(page.property("detail")).get("node", {}).get("id") == first_id)
        editor = page.findChild(object, "nodeEditor")
        # Reuse the public editor method, then click its real suppression control.
        from PySide6.QtCore import Q_ARG, QMetaObject
        assert QMetaObject.invokeMethod(editor, "openFor", Q_ARG("QVariant", first_id))
        ui.wait(lambda editor=editor: editor.property("visible"))
        click(ui, editor.findChild(object, "editorSuppressNode"))
        ui.wait(lambda: any(n["id"] == first_id and n.get("suppressed") for n in ops.adaptation_snapshot()["nodes"]))
        assert ops.graph_snapshot() == before
        assert not ui.errors


def test_asset_material_card_ui_stays_independent(tmp_path, monkeypatch):
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "assets")
        assets = ui.window.findChild(object, "assetsPage")
        ui.settle()
        assets.setProperty("category", "material")
        ui.bridge.loadAssets("material", "", "")
        ui.wait(lambda: plain(assets.property("payload"))["category"] == "material")
        click(ui, ui.window.findChild(object, "assetAdd"))
        ui.window.findChild(object, "assetTitle").setProperty("text", "丝绸")
        click(ui, ui.window.findChild(object, "assetSave"))
        ui.wait(lambda: len(plain(assets.property("payload"))["assets"]) == 1)
        assert ops.list_assets("material")["assets"][0]["title"] == "丝绸"
        assert ops.graph_snapshot()["nodes"] == []
        assert not ui.errors



def test_shared_import_dialog_and_adapter_never_write_extraction(tmp_path, monkeypatch):
    from PySide6.QtCore import QMetaObject
    from test_item_possession import setup

    from narrative.graph_operations import GraphOperations
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        setup(ops)
        before = ops.graph_snapshot()
        for method in ("revise_node", "correct_property", "add_property", "expire_properties", "delete_property_records", "rename_property", "merge_properties", "set_property_categories"):
            if hasattr(GraphOperations, method):
                monkeypatch.setattr(GraphOperations, method, lambda *a, **k: (_ for _ in ()).throw(AssertionError("extraction write leaked")))
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "adaptation")
        page = ui.window.findChild(object, "adaptationBrowsePage")
        ui.settle()
        click(ui, page.findChild(object, "adaptationImport"))
        dialog = page.findChild(object, "adaptationImportDialog")
        ui.wait(lambda: dialog.property("visible"))
        candidates = plain(page.property("importCandidates"))
        assert len(candidates) > 1
        page.setProperty("importSelected", [candidates[0]["id"]])
        assert QMetaObject.invokeMethod(dialog, "accept")
        ui.wait(lambda: len(plain(page.property("nodes"))) > 0)
        assert ops.graph_snapshot() == before
        assert not ui.errors


def test_shared_adaptation_editor_preserves_json_values_and_rejects_bad_json(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        snap = ops.edit_adaptation_node(changes={"node_type": "person", "name": "角色"})
        node_id = snap["nodes"][0]["id"]
        snap=ops.edit_adaptation_node(changes={"node_type":"scene","name":"测试场景"})
        scene_id=snap["created_node_id"]
        snap=ops.edit_adaptation_node(changes={"node_type":"event","name":"测试事件"},parent_id=scene_id)
        ops.set_adaptation_property(node_id, "数量", snap["created_node_id"], "set", value=42)
        before = ops.graph_snapshot()
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "adaptation")
        page = ui.window.findChild(object, "adaptationBrowsePage")
        ui.wait(lambda: node_id in {row["id"] for row in plain(page.property("nodes"))})
        editor = page.findChild(object, "nodeEditor")
        assert QMetaObject.invokeMethod(editor, "openFor", Q_ARG("QVariant", node_id))
        ui.wait(lambda: len(plain(editor.property("properties"))) == 1)
        row = plain(editor.property("properties"))[0]
        record = row["records"][0]
        assert QMetaObject.invokeMethod(editor, "startEdit", Q_ARG("QVariant", row), Q_ARG("QVariant", record))
        assert editor.property("editJson")
        revision = ops.adaptation_snapshot()["revision"]
        editor.setProperty("editValue", "{bad")
        assert QMetaObject.invokeMethod(editor, "submitEdit")
        ui.settle()
        assert ops.adaptation_snapshot()["revision"] == revision
        editor.setProperty("editValue", "43")
        assert QMetaObject.invokeMethod(editor, "submitEdit")
        ui.wait(lambda: ops.adaptation_snapshot()["revision"] > revision)
        assert next(p for p in ops.adaptation_snapshot()["properties"] if p.get("node_id") == node_id)["value"] == 43
        assert ops.graph_snapshot() == before
        assert not ui.errors


def test_shared_editor_copy_button_creates_independent_id(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        snap = ops.edit_adaptation_node(changes={"node_type": "person", "name": "角色"})
        node_id = snap["nodes"][0]["id"]
        before = ops.graph_snapshot()
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "adaptation")
        page = ui.window.findChild(object, "adaptationBrowsePage")
        ui.wait(lambda: node_id in {row["id"] for row in plain(page.property("nodes"))})
        editor = page.findChild(object, "nodeEditor")
        assert QMetaObject.invokeMethod(editor, "openFor", Q_ARG("QVariant", node_id))
        ui.wait(lambda: plain(editor.property("node")).get("id") == node_id)
        click(ui, editor.findChild(object, "editorCopyNode"))
        ui.wait(lambda: len(plain(page.property("nodes"))) == 2)
        nodes = ops.adaptation_snapshot()["nodes"]
        assert nodes[0]["id"] == node_id
        assert nodes[1]["id"] != node_id
        assert nodes[1]["name"] == nodes[0]["name"]
        assert nodes[1]["sort_id"] > nodes[0]["sort_id"]
        assert ops.graph_snapshot() == before
        assert not ui.errors


def test_suppressing_current_reading_point_returns_to_latest_and_can_restore(tmp_path, monkeypatch):
    from PySide6.QtCore import Q_ARG, QMetaObject

    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        snap = ops.edit_adaptation_node(changes={"node_type": "scene", "name": "场景"})
        scene_id = snap["nodes"][0]["id"]
        snap = ops.edit_adaptation_node(changes={"node_type": "event", "name": "时间点"}, parent_id=scene_id)
        event_id = next(n["id"] for n in snap["nodes"] if n["node_type"] == "event")
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "adaptation")
        page = ui.window.findChild(object, "adaptationBrowsePage")
        ui.wait(lambda: len(plain(page.property("nodes"))) == 2)
        assert QMetaObject.invokeMethod(page, "pickCutById", Q_ARG("QVariant", event_id))
        ui.wait(lambda: page.property("cutEventId") == event_id)
        editor = page.findChild(object, "nodeEditor")
        assert QMetaObject.invokeMethod(editor, "openFor", Q_ARG("QVariant", event_id))
        ui.wait(lambda: plain(editor.property("node")).get("id") == event_id)
        click(ui, editor.findChild(object, "editorSuppressNode"))
        ui.wait(lambda: page.property("cutId") == "" and page.property("cutEventId") == "")
        ui.wait(lambda: not any(n["id"] == event_id for n in plain(page.property("nodes"))))
        page.setProperty("showSuppressed", True)
        assert QMetaObject.invokeMethod(page, "load")
        ui.wait(lambda: any(n["id"] == event_id for n in plain(page.property("nodes"))))
        assert QMetaObject.invokeMethod(editor, "openFor", Q_ARG("QVariant", event_id))
        ui.wait(lambda: plain(editor.property("node")).get("suppressed") is True)
        click(ui, editor.findChild(object, "editorSuppressNode"))
        ui.wait(lambda: not next(n for n in ops.adaptation_snapshot()["nodes"] if n["id"] == event_id).get("suppressed"))
        ui.settle()
        assert not ui.errors
