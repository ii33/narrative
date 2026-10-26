"""Capture the browsing page for one node, properties grouped by category.

Usage:
    python scripts/native_browse_screenshot.py PROJECT.sqlite3 OUTPUT.png NODE_ID

Runs offscreen-safe: the window is shown but never needs a desktop session, and a
CJK font is registered so the capture is not full of empty boxes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPalette
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleFactory

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge

FONTS = ("C:/Windows/Fonts/NotoSansSC-VF.ttf", "C:/Windows/Fonts/msyh.ttc")
PALETTE = (
    (QPalette.ColorRole.Window, "#f4f7fb"),
    (QPalette.ColorRole.Base, "#ffffff"),
    (QPalette.ColorRole.AlternateBase, "#f4f7fb"),
    (QPalette.ColorRole.Button, "#ffffff"),
    (QPalette.ColorRole.Text, "#16233a"),
    (QPalette.ColorRole.WindowText, "#16233a"),
    (QPalette.ColorRole.ButtonText, "#16233a"),
    (QPalette.ColorRole.Highlight, "#2b5cd9"),
    (QPalette.ColorRole.HighlightedText, "#ffffff"),
)


def _apply_style(app: QApplication) -> None:
    """Match the real window: without Fusion the controls render as black blocks."""
    app.setStyle(QStyleFactory.create("Fusion"))
    QQuickStyle.setStyle("Fusion")
    palette = app.palette()
    for role, color in PALETTE:
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    for path in FONTS:
        if Path(path).is_file():
            font_id = QFontDatabase.addApplicationFont(path)
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            if families:
                app.setFont(QFont(families[0]))
                break


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("node", help="节点编号，例如 3")
    parser.add_argument("--expand", default="", help="要展开的属性名，默认展开第一条有历史的")
    parser.add_argument("--height", type=int, default=0, help="窗口高度；属性多时调高，少滚一点")
    parser.add_argument("--cut", default="", help="阅读位置：步骤编号（场景或事件），只显示读到这里时已知的属性")
    parser.add_argument("--granularity", default="scene", choices=("scene", "event"),
                        help="事件等级：按场景还是按事件切")
    parser.add_argument("--time", action="store_true", help="同时把节点列表也收窄到该阅读位置")
    parser.add_argument("--related", action="store_true",
                        help="只显示当前步骤的相关节点（界面默认就是开的，这里只保证是开的）")
    parser.add_argument("--all-nodes", action="store_true", help="关掉相关节点，列出全书")
    parser.add_argument("--editor", action="store_true", help="打开节点编辑器再截图")
    parser.add_argument("--scope", default="latest", choices=("latest", "all"),
                        help="属性范围：latest 这一刻的状态，all 历次披露的并集")
    parser.add_argument("--rename", default=None, metavar="NAME",
                        help="在节点编辑器里打开某行的改名表单再截图（需 --editor）")
    args = parser.parse_args()
    project = args.project.resolve()
    if not project.is_file():
        parser.error("project file does not exist")

    app = QApplication.instance() or QApplication(sys.argv)
    _apply_style(app)

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
    if args.height:
        window.setHeight(args.height)
    window.show()

    def wait_for(kind: str, *, timeout: float = 15) -> dict:
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
        bridge.openProject(str(project))
        wait_for("project")
        wait_for("browse")
        results.clear()
        page = window.findChild(object, "browsePage")
        if page is None:
            raise RuntimeError("browse page not found")
        if args.granularity != "scene":
            page.pickGranularity(args.granularity)
            wait_for("browse")
        if args.cut:
            options = page.property("steps")
            options = options.toVariant() if hasattr(options, "toVariant") else options
            index = next(
                (i for i, item in enumerate(options) if item["id"] == args.cut), None
            )
            if index is None:
                raise RuntimeError(f"没有编号为 {args.cut} 的步骤")
            # Index 0 is "最新", so the cut sits one past the step's position.
            page.pickCut(index + 1)
            wait_for("browse")
        if args.time:
            page.toggleTimeFilter()
            wait_for("browse")
        if args.related and not page.property("relatedOnly"):
            page.toggleRelated()
            wait_for("browse")
        if args.all_nodes and page.property("relatedOnly"):
            page.toggleRelated()
            wait_for("browse")
        app.processEvents()
        if args.scope == "all":
            page.togglePropertyScope("all")
            wait_for("property_scope")
        page.select(args.node)
        wait_for("node_properties")
        if args.editor:
            editor = window.findChild(object, "nodeEditor")
            if editor is None:
                raise RuntimeError("节点编辑器没有加载")
            editor.openFor(args.node)
            wait_for("node_editor")
            QTest.qWait(300)
        app.processEvents()

        rows = page.property("propertyRows")
        rows = rows.toVariant() if hasattr(rows, "toVariant") else rows
        target = next(
            (row for row in rows if row["label"].startswith(args.expand)), None
        ) if args.expand else None
        target = target or next((row for row in rows if len(row["records"]) > 1), None)
        if target is not None:
            page.toggleProperty(target["key"])
        QTest.qWait(400)
        # 改名在节点编辑器里：打开某一行（默认是 --expand 指的那行）的改名表单。
        if args.rename is not None:
            if not args.editor:
                raise RuntimeError("--rename 需要同时给 --editor：改名在节点编辑器里")
            items = editor.property("properties")
            items = items.toVariant() if hasattr(items, "toVariant") else items
            wanted = args.expand or (target or {}).get("label", "")
            item = next(
                (row for row in items
                 if (row["label"] or "").startswith(wanted)), None
            )
            if item is None:
                raise RuntimeError("编辑器里没有可改名的属性行")
            editor.startRename(item)
            QTest.qWait(200)
            if args.rename:
                editor.setProperty("renameName", args.rename)
            QTest.qWait(200)

        args.output.parent.mkdir(parents=True, exist_ok=True)
        grab = window.contentItem().grabToImage()
        deadline = time.monotonic() + 10
        while grab.image().isNull() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(20)
        if grab.image().isNull():
            raise RuntimeError("screenshot timed out")
        # The window background is not part of the grabbed item, so the page
        # margins come back transparent; flatten onto paper instead of letting a
        # viewer show them as black.
        image = grab.image()
        canvas = QImage(image.size(), QImage.Format.Format_RGB32)
        canvas.fill(QColor("#f4f7fb"))
        painter = QPainter(canvas)
        painter.drawImage(0, 0, image)
        painter.end()
        if not canvas.save(str(args.output)):
            raise RuntimeError("screenshot could not be saved")
        groups = page.property("categoryGroups")
        groups = groups.toVariant() if hasattr(groups, "toVariant") else groups
        print(
            f"saved {args.output} | node={args.node} | "
            f"groups={[group['key'] for group in groups]}"
        )
        return 0
    finally:
        window.close()
        bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
