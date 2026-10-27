"""Capture the node material section in the browsing detail pane.

Usage:
    python scripts/native_material_screenshot.py PROJECT.sqlite3 OUTPUT.png --node 7

Runs on a copy of the project in a temp directory: seeding writes material files
next to the project, and the real one must stay untouched. The detail pane is
scrolled to the end, where the material block sits.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPalette
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QStyleFactory

from narrative.native_app import QML_PATH
from narrative.native_bridge import NativeBridge
from narrative.node_material import MaterialStore

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
    parser.add_argument("--node", default="", help="节点编号，默认第一个有属性的节点")
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--no-scroll", action="store_true", help="不滚到资料块，看详情顶部")
    args = parser.parse_args()
    project = args.project.resolve()
    if not project.is_file():
        parser.error("project file does not exist")

    workdir = Path(tempfile.mkdtemp(prefix="narrative-material-"))
    copy = workdir / project.name
    shutil.copyfile(project, copy)
    for suffix in ("-wal", "-shm"):
        side = project.with_name(project.name + suffix)
        if side.is_file():
            shutil.copyfile(side, copy.with_name(copy.name + suffix))

    app = QApplication.instance() or QApplication(sys.argv)
    assert isinstance(app, QApplication)
    _apply_style(app)

    bridge = NativeBridge(workdir)
    results: list[tuple[str, dict]] = []
    errors: list[tuple[str, str]] = []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, message: errors.append((kind, message)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    root = engine.rootObjects()[0] if engine.rootObjects() else None
    if not isinstance(root, QQuickWindow):
        raise RuntimeError("native QML did not load")
    window = root
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
        listing = wait_for("browse")
        node_id = args.node
        if not node_id:
            if not listing["nodes"]:
                raise RuntimeError("这个工程里没有节点")
            node_id = listing["nodes"][0]["id"]

        # 铺一点素材，截图才看得出这一块是干什么的。人物和别的东西写的不一样。
        node_type = next(
            (item.get("node_type") for item in listing["nodes"] if item["id"] == node_id),
            "other",
        )
        person = node_type == "person"
        store = MaterialStore(copy)
        store.save_text(
            node_id,
            "洗得发白的青衫，袖口磨破，背着旧布包。" if person
            else "一枚拇指大的紫色珠子，入手温热。",
            "嘴硬心软，遇事先笑再算账。出身寒微，最恨别人拿出身说事；"
            "对朋友肯把命交出去，对敌人一分情面都不留。" if person
            else "来历不明，认主之后只在危急时发烫；越用力催动，紫光越盛，"
                 "反噬也越重。谁拿着它，谁就得替它担着。",
        )
        if person:
            store.add_form(node_id, "青衫少年", "洗得发白的青衫，束发，赤脚。", "")
            store.add_form(node_id, "白衣客", "换了一身白衣，腰上挂着短剑。", "")
        else:
            store.add_form(node_id, "未认主", "紫光内敛，纹路暗沉，像块普通的珠子。", "")
            store.add_form(node_id, "认主之后", "紫光大盛，纹路亮起，摸上去烫手。", "")
        forms = store.view(node_id)["forms"]
        store.add_image(
            node_id, forms[0]["id"], "concept", _placeholder(workdir / "a.png", "#3c6fb0", "概念图")
        )
        store.add_image(
            node_id, forms[0]["id"], "setting", _placeholder(workdir / "b.png", "#4a8f7b", "设定图")
        )
        store.add_image(
            node_id, forms[1]["id"], "action", _placeholder(workdir / "c.png", "#8a5fa8", "动作图")
        )

        page: Any = window.findChild(QObject, "browsePage")
        if page is None:
            raise RuntimeError("browse page not found")
        results.clear()
        page.select(node_id)
        wait_for("node_properties")
        wait_for("material")
        time.sleep(0.4)
        app.processEvents()

        # 资料块在详情内容的最下面：滚到底才看得见。
        scroll: Any = page.findChild(QObject, "browseDetailScroll")
        if scroll is not None and not args.no_scroll:
            content = scroll.property("contentItem")
            if content is not None:
                # 滚到底：contentY 要的是「内容高 - 视口高」，给多了会被夹回去，
                # 而夹的过程不重绘，抓到的就是一片空白。
                total = float(content.property("contentHeight") or 0)
                viewport = float(content.property("height") or 0)
                content.setProperty("contentY", max(0.0, total - viewport))
        time.sleep(0.3)
        app.processEvents()

        args.output.parent.mkdir(parents=True, exist_ok=True)
        grab: Any = window.contentItem().grabToImage()
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
        material = page.property("material")
        material = material.toVariant() if hasattr(material, "toVariant") else material
        print(
            f"saved {args.output} | node={node_id} | "
            f"forms={len(material.get('forms', []))} | "
            f"summary={material.get('summary', '')!r}"
        )
        return 0
    finally:
        window.close()
        bridge.close()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
