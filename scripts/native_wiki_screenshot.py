"""Capture the character wiki page, with a seeded bible and appearances.

Usage:
    python scripts/native_wiki_screenshot.py PROJECT.sqlite3 OUTPUT.png [--character NODE_ID]

Runs on a copy of the project in a temp directory: seeding writes wiki files next
to the project, and the real one must stay untouched. A CJK font is registered so
the capture is not full of empty boxes.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPalette
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QStyleFactory

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge
from narrative.wiki_store import WikiStore

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


def _placeholder(path: Path, color: str, label: str) -> Path:
    """一张写着用途的色块：截图里能看出哪张图挂在哪一类。"""
    image = QImage(360, 360, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    painter = QPainter(image)
    painter.setPen(QColor("#ffffff"))
    font = painter.font()
    font.setPixelSize(34)
    painter.setFont(font)
    painter.drawText(image.rect(), 0x0084, label)  # Qt.AlignCenter
    painter.end()
    image.save(str(path))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--character", default="", help="节点编号，默认第一个有人物 wiki 的")
    parser.add_argument("--height", type=int, default=0)
    args = parser.parse_args()
    project = args.project.resolve()
    if not project.is_file():
        parser.error("project file does not exist")

    workdir = Path(tempfile.mkdtemp(prefix="narrative-wiki-"))
    copy = workdir / project.name
    shutil.copyfile(project, copy)
    for suffix in ("-wal", "-shm"):
        side = project.with_name(project.name + suffix)
        if side.is_file():
            shutil.copyfile(side, copy.with_name(copy.name + suffix))

    app = QApplication.instance() or QApplication(sys.argv)
    _apply_style(app)

    bridge = NativeBridge(workdir)
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
    if args.height:
        window.setHeight(args.height)
        app.processEvents()

    def wait_for(kind: str, *, timeout: float = 20) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if errors:
                raise RuntimeError(f"{errors[-1][0]}: {errors[-1][1]}")
            for found_kind, payload in reversed(results):
                if found_kind == kind:
                    return payload
            time.sleep(0.02)
        raise TimeoutError(f"native {kind} did not complete")

    try:
        bridge.openProject(str(copy))
        wait_for("project")
        wait_for("browse")
        index = wait_for("wiki_index")
        characters = index["characters"]
        if not characters:
            raise RuntimeError("这个工程里没有人物节点")
        node_id = args.character or characters[0]["id"]

        # 给这个角色铺一点素材，截图才看得出这一页是干什么的。
        store = WikiStore(copy)
        store.set_bible(
            node_id,
            "嘴硬心软，遇事先笑再算账。出身寒微，最恨别人拿出身说事；"
            "对朋友肯把命交出去，对敌人一分情面都不留。",
        )
        store.add_appearance(node_id, "青衫少年", "洗得发白的青衫，袖口磨破，背着旧布包。", "")
        store.add_appearance(node_id, "白衣客", "换了一身白衣，束发，腰上挂着短剑。", "")
        first = store.view(node_id)["appearances"][0]["id"]
        second = store.view(node_id)["appearances"][1]["id"]
        store.add_image(node_id, first, "concept", _placeholder(workdir / "a.png", "#3c6fb0", "概念图"))
        store.add_image(node_id, first, "turnaround", _placeholder(workdir / "b.png", "#4a8f7b", "三视图"))
        store.add_image(node_id, second, "action", _placeholder(workdir / "c.png", "#8a5fa8", "动作图"))

        results.clear()
        page = window.findChild(object, "wikiPage")
        if page is None:
            raise RuntimeError("wiki page not found")
        window.setProperty("currentPage", "wiki")
        page.setProperty("selectedId", "")
        page.pick(node_id)
        wait_for("wiki")
        time.sleep(0.4)
        app.processEvents()

        args.output.parent.mkdir(parents=True, exist_ok=True)
        grab = window.contentItem().grabToImage()
        deadline = time.monotonic() + 10
        while grab.image().isNull() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        if grab.image().isNull():
            raise RuntimeError("screenshot timed out")
        image = grab.image()
        canvas = QImage(image.size(), QImage.Format.Format_RGB32)
        canvas.fill(QColor("#f4f7fb"))
        painter = QPainter(canvas)
        painter.drawImage(0, 0, image)
        painter.end()
        if not canvas.save(str(args.output)):
            raise RuntimeError("screenshot could not be saved")
        state = page.property("stateRows")
        state = state.toVariant() if hasattr(state, "toVariant") else state
        print(
            f"saved {args.output} | node={node_id} | "
            f"appearances={len(page.property('wiki').get('appearances', []))} | "
            f"state={[row['label'] for row in state]}"
        )
        return 0
    finally:
        window.close()
        bridge.close()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
