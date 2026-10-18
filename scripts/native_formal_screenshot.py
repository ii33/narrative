"""Capture the accepted native hierarchy through NativeBridge without editing it.

Usage: python scripts/native_formal_screenshot.py PROJECT.sqlite3 OUTPUT.png [--keep-open]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--keep-open", action="store_true", help="leave the window visible until closed")
    args = parser.parse_args()
    project = args.project.resolve()
    if not project.is_file():
        parser.error("project file does not exist")
    app = QApplication.instance() or QApplication(sys.argv)
    bridge = NativeBridge(project.parent)
    results: list[tuple[str, dict]] = []
    errors: list[tuple[str, str]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    if not engine.rootObjects() or not isinstance(engine.rootObjects()[0], QQuickWindow):
        raise RuntimeError("native QML did not load")
    window = engine.rootObjects()[0]
    window.show()

    def wait_for(kind: str, *, timeout: float = 12) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if errors:
                raise RuntimeError(f"{errors[-1][0]}: {errors[-1][1]}")
            for found_kind, payload in reversed(results):
                if found_kind == kind:
                    return payload
            QTest.qWait(20)
        raise TimeoutError(f"native {kind} did not complete")

    try:
        window.openProjectPath(str(project))
        wait_for("project")
        results.clear()
        window.setProperty("currentPage", "graph")
        bridge.loadHierarchy()
        hierarchy = wait_for("hierarchy")
        assert hierarchy.get("candidate_id") is None, "formal graph unexpectedly returned candidate data"
        nodes = {node["id"]: node for node in hierarchy.get("nodes", [])}
        roots = [identity for identity in hierarchy.get("roots", [])
                 if nodes.get(identity, {}).get("node_type") == "storyline"]
        if not roots:
            raise RuntimeError("accepted graph has no visible storyline root")
        storyline = roots[0]
        main_events = [identity for identity in nodes[storyline].get("child_ids", [])
                       if nodes.get(identity, {}).get("node_type") == "event"]
        if len(main_events) != 5:
            raise RuntimeError(f"expected five accepted main events, found {len(main_events)}")
        window.toggleHierarchy(storyline, False)
        nested = next((identity for identity in main_events if nodes[identity].get("child_ids")), None)
        if nested:
            window.toggleHierarchy(nested, False)
        detail_id = next((node["id"] for node in hierarchy["nodes"]
                          if node.get("node_type") == "person" and "屠人" in node.get("name", "")), None)
        detail_id = detail_id or nested or main_events[0]
        results.clear()
        window.selectNode(detail_id)
        detail = wait_for("detail")
        if detail.get("node", {}).get("id") != detail_id:
            raise RuntimeError("node detail did not match selected node")
        QTest.qWait(250)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        grab = window.contentItem().grabToImage()
        deadline = time.monotonic() + 10
        while grab.image().isNull() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(20)
        if grab.image().isNull() or not grab.saveToFile(str(args.output)):
            raise RuntimeError("native screenshot timed out or could not be saved")
        print(f"saved {args.output} | revision={hierarchy.get('revision')} | main_events={len(main_events)} | detail={detail_id}")
        if args.keep_open:
            print("Native window remains open until you close it.")
            return app.exec()
        return 0
    finally:
        window.close()
        bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
