"""Capture a review-only native candidate hierarchy without changing project data.

Usage: python scripts/native_candidate_screenshot.py PROJECT.sqlite3 CANDIDATE_ID OUTPUT.png
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
    parser.add_argument("candidate_id")
    parser.add_argument("output", type=Path)
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
        window.setProperty("currentPage", "candidates")
        window.setProperty("selectedCandidateId", args.candidate_id)
        bridge.candidateDetail(args.candidate_id)
        preview = wait_for("candidate")["preview"]
        hierarchy = preview.get("hierarchy", {})
        nodes = {node["id"]: node for node in hierarchy.get("nodes", [])}
        primary = {"storyline", "scene", "event"}
        roots = [identity for identity in hierarchy.get("roots", [])
                 if nodes.get(identity, {}).get("node_type") in primary]
        if roots:
            window.toggleHierarchy(roots[0], True)
            for child_id in nodes[roots[0]].get("child_ids", []):
                child = nodes.get(child_id, {})
                if child.get("node_type") == "event" and child.get("child_ids"):
                    window.toggleHierarchy(child_id, True)
                    break
        QTest.qWait(250)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        grab = window.contentItem().grabToImage()
        deadline = time.monotonic() + 10
        while grab.image().isNull() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(20)
        if grab.image().isNull() or not grab.saveToFile(str(args.output)):
            raise RuntimeError("native screenshot timed out or could not be saved")
        print(f"saved {args.output} | candidate={args.candidate_id} | hierarchy={hierarchy.get('status', 'available')}")
        return 0
    finally:
        window.close()
        bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
