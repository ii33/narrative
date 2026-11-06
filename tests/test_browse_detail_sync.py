"""Actual browse clicks keep headers and properties on the same selected node."""
import threading

from agent_fixtures import TEXT, draft_result
from test_chat_scroll import transcript_shell
from test_static_check_ui import click, plain, visual_find

from narrative.graph_operations import GraphOperations
from narrative.operations import Operations


def prepare(ui):
    ops = Operations.open(ui.bridge._project_path)
    sid = ops.import_source("详情同步原文", TEXT)
    payload = draft_result()
    payload["nodes"] += [
        {"type": "action", "name": "完成接人", "event": "@6", "quotes": ["甲接到乙后离开。"]},
        {"type": "storyline", "name": "接人篇章", "closing_quote": "甲接到乙后离开。", "children": ["@3"]},
    ]
    assert ops.submit_extraction(sid, payload)["status"] == "accepted"
    ui.window.setProperty("assistantOpen", False)
    ui.window.setProperty("currentPage", "browse")
    page = ui.window.findChild(object, "browsePage")
    page.load()
    ui.wait(lambda: not ui.window.property("busy"))
    ui.settle()
    return ops, page


def choose(ui, node_id):
    view = visual_find(ui.window.contentItem(), "browseNodeList")
    view.forceLayout()
    row = next(item for item in view.property("contentItem").childItems() if item.property("id") == node_id)
    click(ui, row)


def test_big_event_graph_allows_person_header_and_refresh_reloads_matching_detail(tmp_path, monkeypatch):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        ops, page = prepare(ui)
        errors = []
        ui.bridge.errorOccurred.connect(lambda kind, text: errors.append((kind, text)))
        choose(ui, "P2")
        ui.wait(lambda: plain(page.property("detail")).get("node", {}).get("id") == "P2")
        ui.wait(lambda: len(plain(page.property("propertyRows"))) > 0)
        title = ui.window.findChild(object, "browseDetailTitle")
        identity = ui.window.findChild(object, "browseDetailIdentity")
        assert title.property("text") == "甲" and "P2" in identity.property("text")
        # Conversation/graph updates enter the same existing refresh path.
        ui.bridge.resultReady.emit("node_edit", {"graph_reset_required": True, "status": "accepted"})
        ui.wait(lambda: not ui.window.property("busy"))
        assert title.property("text") == "甲" and plain(page.property("detail"))["node"]["id"] == "P2"
        assert not errors
        labels = ops.graph_display_map()["items"]
        phase = next(n for n in ops.graph_snapshot()["nodes"] if n["node_type"] == "big_event")
        assert "大事件" in labels[phase["id"]]["summary"]
        for node in ops.graph_snapshot()["nodes"]:
            if node["node_type"] not in {"big_event", "storyline", "scene", "event", "action"}:
                continue
            choose(ui, node["id"])
            ui.wait(lambda node=node: plain(page.property("detail")).get("node", {}).get("id") == node["id"])
            ui.wait(lambda: not ui.window.property("busy"))
            assert title.property("text") == node["name"]
            assert node["id"] in identity.property("text")
            assert labels[node["id"]]["kind_label"] in identity.property("text")
            assert plain(page.property("detail"))["node"]["evidence"]["quote"] == node["evidence"]["quote"]



def test_browse_epoch_refresh_and_late_previous_selection_cannot_clear_or_replace_header(tmp_path, monkeypatch):
    with transcript_shell(tmp_path, monkeypatch) as ui:
        _ops, page = prepare(ui)
        original_query = GraphOperations.graph_query
        entered, release = threading.Event(), threading.Event()
        first = True
        def query(ops, *args, **kwargs):
            nonlocal first
            if kwargs.get("node_id") == "P2" and first:
                first = False
                entered.set()
                assert release.wait(5)
            return original_query(ops, *args, **kwargs)
        monkeypatch.setattr(GraphOperations, "graph_query", query)
        try:
            choose(ui, "P2")
            ui.wait(entered.is_set)
            page.load()  # invalidates the first detail query's epoch
            ui.wait(lambda: plain(page.property("detail")).get("node", {}).get("id") == "P2")
            choose(ui, "P1")
            ui.wait(lambda: plain(page.property("detail")).get("node", {}).get("id") == "P1")
            release.set()
            ui.wait(lambda: not ui.window.property("busy"))
            ui.settle()
            assert ui.window.findChild(object, "browseDetailTitle").property("text") == "乙"
            assert plain(page.property("detail"))["node"]["id"] == "P1"
            # An old node's properties must not attach below the new heading.
            ui.bridge.resultReady.emit("node_properties", {"node_id": "P2", "categories": [{"key": "旧节点", "items": [{"property_id": "不应出现", "value": "错误"}]}]})
            ui.settle()
            assert not plain(page.property("propertyRows"))
        finally:
            release.set()


def test_public_display_map_covers_every_schema_node_type(tmp_path):
    from narrative.graph_models import Node
    ops = Operations.create_graph_project(tmp_path / "all-types.nproj")
    if not hasattr(ops, "import_source"):
        ops = Operations.open(tmp_path / "all-types.nproj")
    sid = ops.import_source("类型显示原文", TEXT)
    payload = draft_result()
    payload["nodes"] += [
        {"type": "action", "name": "完成接人", "event": "@6", "quotes": ["甲接到乙后离开。"]},
        {"type": "storyline", "name": "接人篇章", "closing_quote": "甲接到乙后离开。", "children": ["@3"]},
    ]
    assert ops.submit_extraction(sid, payload)["status"] == "accepted"
    existing_types = {node["node_type"] for node in ops.graph_snapshot()["nodes"]}
    types = set(ops.get_schema()["node_types"])
    objects = []
    for index, kind in enumerate(sorted(types - existing_types)):
        objects.append(Node.model_validate({"kind": "node", "id": f"extra-{index}", "node_type": kind,
                       "name": kind, "disclosure": {"source_id": sid, "start": 0},
                       "evidence": {"source_id": sid, "quote": TEXT}}).model_dump())
    item = next(row for row in objects if row["node_type"] == "item")
    base = next(row for row in ops.property_records() if row.get("kind") == "property_change")
    record = dict(base, node_id=item["id"], property_id="持有者", value="未知", scope="world", holder_id=None, op="set")
    record.pop("id")
    records = [{"kind": "property_definition", "id": "持有者", "value_type": "either", "cardinality": "one", "semantic": "item_holder"}, record]
    result = ops.apply_changes(objects, records, ops.graph_project_info()["revision"], "覆盖全部合法节点类型显示")
    assert result["status"] == "accepted", result
    labels = ops.graph_display_map()["items"]
    nodes = ops.graph_snapshot()["nodes"]
    assert {node["node_type"] for node in nodes} == types
    for node in nodes:
        assert labels[node["id"]]["label"] == node["name"]
        assert labels[node["id"]]["kind_label"]
