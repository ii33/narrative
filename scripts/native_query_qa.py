"""Native Qt visual QA over a new, explicitly manual fixture; no model requests."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge
from narrative.operations import Operations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    project = output / "manual-query-fixture.sqlite3"
    ops = Operations.create_graph_project(project)
    sid = ops.import_source("工程验收人工夹具", "旅人来到河岸，遇见向导。两人随后渡河。")
    quote = ops.locate(sid, "旅人来到河岸")[0] | {"id": "quote"}
    objects = [quote, {"kind": "source_order", "id": "order", "source_id": sid, "ordinal": 0},
        {"kind": "predicate", "id": "participates", "label": "参与", "semantic": "participation", "value_type": "node"},
        {"kind": "predicate", "id": "contains", "label": "包含", "semantic": "containment", "value_type": "node"}]
    for identity, name, kind in (("traveler", "旅人", "person"), ("guide", "向导", "person"),
                                 ("shore", "河岸", "location"), ("journey", "河边旅程", "storyline"),
                                 ("arrival", "抵达河岸", "event"), ("crossing", "结伴渡河", "event"),
                                 ("boat", "登船准备", "event")):
        node = {"kind": "node", "id": identity, "name": name, "node_type": kind}
        if identity in {"arrival", "crossing"}:
            node["occurrence"] = {"kind": "point", "point": {"timeline_id": "story", "form": "number", "number": 10 if identity == "arrival" else 20}}
        objects.append(node)
    for aid, subject, predicate, target in (("p1", "arrival", "participates", "traveler"),
            ("p2", "arrival", "participates", "shore"), ("p3", "arrival", "participates", "guide"),
            ("p4", "crossing", "participates", "traveler"), ("org1", "journey", "contains", "arrival"),
            ("org2", "journey", "contains", "crossing"), ("org3", "crossing", "contains", "boat")):
        objects.append({"kind": "assertion", "id": aid, "subject_id": subject,
                        "predicate_id": predicate, "object_id": target,
                        **({"qualifiers": {"role": "参与者"}} if predicate == "participates" else {})})
        objects.append({"kind": "evidence_link", "id": "proof-" + aid, "assertion_id": aid, "evidence_id": "quote"})
    ops.graph_accept(ops.graph_propose(objects, ops.graph_project_info()["revision"],
        source="manual", note="工程界面夹具，不是真实模型输出，也不是小说验收"), 1)
    app = QApplication([])
    bridge = NativeBridge(output)
    results, errors = [], []
    bridge.resultReady.connect(lambda k, p: results.append((k, p)))
    bridge.errorOccurred.connect(lambda k, p: errors.append((k, p)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    window = engine.rootObjects()[0]
    assert isinstance(window, QQuickWindow)
    window.show()

    def until(condition):
        deadline = time.monotonic() + 15
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(20)
        assert condition(), errors

    def value(name):
        result = window.property(name)
        return result.toVariant() if hasattr(result, "toVariant") else result

    def capture(name):
        QTest.qWait(400)
        grab = window.contentItem().grabToImage()
        until(lambda: not grab.image().isNull())
        assert grab.saveToFile(str(output / name))

    def query(action):
        count = len([k for k, _ in results if k == "graph_view"])
        action()
        until(lambda: len([k for k, _ in results if k == "graph_view"]) > count)

    try:
        window.openProjectPath(str(project))
        until(lambda: bool(value("queryJson")))
        window.setProperty("projectInfo", value("projectInfo") | {"title": "工程界面验收 · 人工夹具（非小说／非模型输出）"})
        window.setProperty("queryNodeTypes", ["person", "event", "location"])
        window.setProperty("queryRelations", ["participation"])
        query(lambda: window.setQueryFocus("traveler", "旅人"))
        window.setProperty("graphMode", "relations")
        window.selectNode("traveler")
        until(lambda: value("currentDetail").get("node", {}).get("id") == "traveler")
        capture("01-person-focus-graph.png")
        window.setProperty("graphMode", "table")
        capture("02-same-query-table.png")
        exported = json.loads(value("queryJson"))
        replay = ops.graph_view(exported)
        (output / "query.json").write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "query-replay.json").write_text(json.dumps(replay, ensure_ascii=False, indent=2), encoding="utf-8")
        query(window.resetQueryFilters)
        query(lambda: window.setQueryFocus("", ""))
        window.setProperty("graphMode", "timeline")
        window.clickTimelineEvent("root", "crossing")
        capture("03-nested-timeline.png")
        window.setProperty("queryNodeTypes", [])
        query(window.runGraphQuery)
        window.setProperty("graphMode", "table")
        capture("04-explicit-empty-filter.png")
        summary = {"kind": "manual engineering fixture, not model or novel acceptance",
                   "revision": ops.graph_project_info()["revision"], "errors": errors,
                   "query_count": len([k for k, _ in results if k == "graph_view"]),
                   "replay_node_ids": [n["id"] for n in replay["nodes"]]}
        (output / "qa-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        assert not errors
        print(json.dumps(summary, ensure_ascii=False))
    finally:
        window.close()
        bridge.close()


if __name__ == "__main__":
    main()
