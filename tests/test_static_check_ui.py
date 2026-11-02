"""Real Main.qml manual static checks, filtering, navigation and stale isolation."""

from __future__ import annotations

import json
import threading

from test_chat_scroll import transcript_shell
from test_item_possession import install


def plain(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


def visual_find(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = visual_find(child, name)
        if found:
            return found
    return None


def click(ui, item):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(
        ui.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint()
    )
    ui.settle()


def test_manual_check_filters_and_navigates_actual_node_record_and_unicode_source(
    tmp_path, monkeypatch
):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        install(ops)
        sid = ops.import_source("定位合成原文", "😀原文片段。")
        revision = ops.graph_project_info()["revision"]
        row = {
            "kind": "property_value",
            "id": "legacy_check_record",
            "node_id": "I1",
            "property_id": "未定义属性",
            "value": "测试值",
            "event_id": "E1",
            "evidence": {"source_id": sid, "quote": "😀原文片段。"},
            "disclosure": {"source_id": sid, "start": 0},
        }
        with ops.project.store.connect(write=True) as db:
            db.execute(
                "INSERT INTO property_records(id,kind,data,accepted_revision) VALUES (?,?,?,?)",
                (row["id"], row["kind"], json.dumps(row, ensure_ascii=False), revision),
            )
        ui.window.setProperty("assistantOpen", False)
        ui.window.setProperty("currentPage", "static_check")
        ui.settle()
        page = ui.window.findChild(object, "staticCheckPage")
        assert not page.property("hasReport")
        click(ui, ui.window.findChild(object, "staticCheckRun"))
        ui.wait(lambda: page.property("hasReport") and not page.property("running"))
        report = plain(page.property("report"))
        expected = ops.check_project()
        assert report["issues"] == expected["issues"]
        assert ops.graph_project_info()["revision"] == revision
        combo = ui.window.findChild(object, "staticCheckSeverity")
        click(ui, combo)
        QTest.keyClick(ui.window, Qt.Key.Key_End)
        QTest.keyClick(ui.window, Qt.Key.Key_Return)
        ui.settle()
        assert page.property("severity") == "info"
        assert all(r["severity"] == "info" for r in plain(page.property("visibleIssues")))
        page.setProperty("severity", "all")
        ui.settle()
        view = ui.window.findChild(object, "staticCheckIssues")
        issues = plain(page.property("visibleIssues"))
        index = next(i for i, r in enumerate(issues) if r.get("record_id") == row["id"])

        def issue_row():
            view.positionViewAtIndex(index, 0)
            ui.settle()
            return next(
                r
                for r in view.property("contentItem").childItems()
                if r.objectName() == "staticCheckIssue"
                and plain(r.property("issue")).get("record_id") == row["id"]
            )

        for action in ("Node", "Record", "Source"):
            ui.window.setProperty("currentPage", "static_check")
            ui.settle()
            click(ui, visual_find(issue_row(), "staticCheck" + action))
            expected_page = "source" if action == "Source" else "browse"
            ui.wait(
                lambda expected_page=expected_page: (
                    ui.window.property("currentPage") == expected_page
                )
            )
            if action == "Record":
                focus = ui.window.findChild(object, "browseFocusedRecord")
                ui.wait(lambda focus=focus: focus.property("recordId") == row["id"])
            elif action == "Source":
                preview = ui.window.findChild(object, "sourcePreview")
                try:
                    ui.wait(
                        lambda preview=preview: preview.property("selectedText") == "😀原文片段。"
                    )
                except AssertionError as exc:
                    raise AssertionError(
                        {
                            key: preview.property(key)
                            for key in ("text", "selectedText", "selectionStart", "selectionEnd")
                        }
                    ) from exc
        assert ops.graph_project_info()["revision"] == revision


def test_checker_marks_stale_snapshot_and_discards_late_result_after_project_switch(
    tmp_path, monkeypatch
):
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        install(ops)
        original = type(ops).check_project
        entered, release = threading.Event(), threading.Event()

        def delayed(self):
            result = original(self)
            entered.set()
            assert release.wait(5)
            return result

        monkeypatch.setattr(type(ops), "check_project", delayed)
        ui.window.setProperty("currentPage", "static_check")
        ui.settle()
        page = ui.window.findChild(object, "staticCheckPage")
        click(ui, ui.window.findChild(object, "staticCheckRun"))
        ui.wait(entered.is_set)
        ops.import_source("扫描后新原文", "新增内容。")
        release.set()
        ui.wait(lambda: page.property("hasReport"))
        assert page.property("stale")
        assert ui.window.findChild(object, "staticCheckStale").property("visible")
        entered.clear()
        release.clear()
        click(ui, ui.window.findChild(object, "staticCheckRun"))
        ui.wait(entered.is_set)
        second = Operations.create_graph_project(tmp_path / "second.nproj")
        ui.bridge.openProject(str(tmp_path / "second.nproj"))
        ui.wait(lambda: ui.bridge._project_path != ops.project_info().path)
        release.set()
        ui.wait(lambda: not ui.window.property("busy"))
        ui.settle()
        assert not page.property("hasReport") and not page.property("running")
        assert second.graph_project_info()["revision"] == 0


def test_checker_failure_is_not_empty_success_and_preserves_graph(tmp_path, monkeypatch):
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        revision = ops.graph_project_info()["revision"]

        def fail(self):
            raise ValueError("隔离扫描故障")

        monkeypatch.setattr(type(ops), "check_project", fail)
        ui.window.setProperty("currentPage", "static_check")
        ui.settle()
        page = ui.window.findChild(object, "staticCheckPage")
        click(ui, ui.window.findChild(object, "staticCheckRun"))
        ui.wait(lambda: not page.property("running") and bool(page.property("failure")))
        assert not page.property("hasReport")
        assert ui.window.findChild(object, "staticCheckFailure").property("visible")
        assert not ui.window.findChild(object, "staticCheckEmpty").property("visible")
        assert ops.graph_project_info()["revision"] == revision


def test_bad_source_does_not_block_node_record_or_definition_raw_navigation(tmp_path, monkeypatch):
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        _sid, ids, _item = install(ops)
        raw = {
            "kind": "property_value",
            "id": "legacy_bad_record_source",
            "node_id": "I1",
            "property_id": "持有者",
            "value": "未知",
            "event_id": ids["甲捡起石头"],
            "evidence": {"source_id": "missing-source", "quote": "历史坏引用"},
            "disclosure": {"source_id": "missing-source", "start": 0},
        }
        with ops.project.store.connect(write=True) as db:
            db.execute(
                "INSERT INTO property_records(id,kind,data,accepted_revision) VALUES (?,?,?,?)",
                (
                    raw["id"],
                    raw["kind"],
                    json.dumps(raw, ensure_ascii=False),
                    ops.graph_project_info()["revision"],
                ),
            )
            missing = raw | {"id": "legacy_missing_owner", "node_id": "missing-node"}
            db.execute(
                "INSERT INTO property_records(id,kind,data,accepted_revision) VALUES (?,?,?,?)",
                (
                    missing["id"],
                    missing["kind"],
                    json.dumps(missing, ensure_ascii=False),
                    ops.graph_project_info()["revision"],
                ),
            )
        cases = [
            ("node", {"node_id": "I1", "source_id": "missing-source"}, None, "I1"),
            (
                "record",
                {
                    "node_id": "P1",
                    "source_id": "missing-source",
                    "record_id": "legacy_bad_record_source",
                },
                "legacy_bad_record_source",
                "I1",
            ),
            ("record", {"source_id": "missing-source", "record_id": "持有者"}, "持有者", ""),
            ("record", {"record_id": "legacy_missing_owner"}, "legacy_missing_owner", ""),
        ]
        for kind, issue, record_id, node_id in cases:
            before = len([p for k, p in ui.results if k == "static_check_target"])
            ui.bridge.resolveStaticIssue(issue, kind, record_id or "")
            ui.wait(
                lambda before=before: (
                    len([p for k, p in ui.results if k == "static_check_target"]) > before
                )
            )
            target = [p for k, p in ui.results if k == "static_check_target"][-1]
            assert target["node_id"] == node_id and target["source_id"] == ""
            ui.wait(lambda: ui.window.property("currentPage") == "browse")
            if record_id:
                focus = ui.window.findChild(object, "browseFocusedRecord")
                ui.wait(
                    lambda focus=focus, record_id=record_id: focus.property("recordId") == record_id
                )
                if record_id in {"持有者", "legacy_missing_owner"}:
                    raw_details = ui.window.findChild(object, "browseFocusedRecordDetails")
                    expected = "property_definition" if record_id == "持有者" else "missing-node"
                    assert expected in raw_details.property("text")


def test_source_jump_resolves_wrong_disclosure_and_selected_second_records_own_source(
    tmp_path, monkeypatch
):
    from narrative.operations import Operations

    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops = Operations.open(ui.bridge._project_path)
        first_sid, ids, _item = install(ops)
        text = "😀前缀。目标引文。"
        second_sid = ops.import_source("第二条记录自己的原文", text)
        quote = "目标引文。"
        row = {
            "kind": "property_value",
            "id": "second_record_own_source",
            "node_id": "I1",
            "property_id": "持有者",
            "value": "未知",
            "event_id": ids["甲捡起石头"],
            "evidence": {"source_id": second_sid, "quote": quote},
            "disclosure": {"source_id": second_sid, "start": 0},
        }
        with ops.project.store.connect(write=True) as db:
            db.execute(
                "INSERT INTO property_records(id,kind,data,accepted_revision) VALUES (?,?,?,?)",
                (
                    row["id"],
                    row["kind"],
                    json.dumps(row, ensure_ascii=False),
                    ops.graph_project_info()["revision"],
                ),
            )
        # A combined issue points to first record; explicit second selection must use its own evidence.
        issue = {
            "node_id": "I1",
            "record_id": "holder0",
            "record_ids": ["holder0", row["id"]],
            "source_id": first_sid,
            "start": 0,
            "end": 8,
            "quote": "一块石头在地上。",
        }
        ui.bridge.resolveStaticIssue(issue, "source", row["id"])
        ui.wait(lambda: any(kind == "static_check_target" for kind, _ in ui.results))
        target = next(p for k, p in ui.results if k == "static_check_target")
        assert target["source_id"] == second_sid
        assert target["start"] == text.index(quote) and target["end"] == text.index(quote) + len(
            quote
        )
        ui.wait(lambda: ui.window.property("currentPage") == "source")
        preview = ui.window.findChild(object, "sourcePreview")
        ui.wait(lambda: preview.property("selectedText") == quote)
