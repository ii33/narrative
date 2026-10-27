"""Native Qt Quick entry point for graph projects.

Qt chooses its normal RHI backend; this module records what the visible window
actually initialized. An offscreen or software test is never counted as proof
of hardware acceleration.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

QML_PATH = Path(__file__).with_name("qml") / "Main.qml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="叙事图原生工作台（Qt Quick）")
    parser.add_argument("--projects-dir", type=Path, default=Path("data/graph-projects"), help="新图项目目录")
    parser.add_argument("--project", help="启动后打开目录内现有图项目名")
    parser.add_argument("--render-log", type=Path, help="记录真实 Qt Quick 渲染后端及 Qt RHI 适配器日志")
    parser.add_argument("--qa-snapshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--qa-exit-after-seconds", type=float, help=argparse.SUPPRESS)
    return parser


def _render_log_path(supplied: Path | None) -> Path:
    if supplied is not None:
        return supplied.resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (Path("data") / "native-logs" / f"qt-{stamp}-{os.getpid()}.log").resolve()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.qa_exit_after_seconds is not None and args.qa_exit_after_seconds <= 0:
        raise ValueError("QA 窗口时长须为正数")

    # Do not force a backend. Qt Quick selects its RHI API for the platform.
    # 逐帧的 scenegraph / RHI 日志只在 --render-log 时打开：它每帧写若干行到磁盘，
    # 正常使用时纯粹是开销。
    if args.render_log is not None:
        os.environ.setdefault("QSG_INFO", "1")
        os.environ.setdefault("QT_LOGGING_RULES", "qt.scenegraph.general=true;qt.rhi.*=true")
    try:
        from PySide6.QtCore import QTimer, QUrl, qInstallMessageHandler
        from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory
    except ImportError as exc:
        print("桌面界面依赖缺失；请在项目目录运行 uv sync --extra desktop。", file=sys.stderr)
        raise SystemExit(2) from exc

    from .native_bridge import NativeBridge

    app = cast(QApplication, QApplication.instance() or QApplication([sys.argv[0]]))
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        # The offscreen Qt plugin may expose no system fonts on Windows.
        cjk_font = Path("C:/Windows/Fonts/NotoSansSC-VF.ttf")
        if cjk_font.is_file():
            font_id = QFontDatabase.addApplicationFont(str(cjk_font))
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    app.setFont(QFont(families[0]))
    # Fusion with a fixed light palette: without it the controls follow the OS
    # theme, and dark buttons show up as black blocks on the light pages.
    app.setStyle(QStyleFactory.create("Fusion"))
    # Qt Quick Controls need their own style switch: importing the Fusion module
    # in QML does nothing, and the default style leaves controls unthemed.
    QQuickStyle.setStyle("Fusion")
    palette = app.palette()
    for role, color in (
        (QPalette.ColorRole.Window, "#f4f7fb"),
        (QPalette.ColorRole.Base, "#ffffff"),
        (QPalette.ColorRole.AlternateBase, "#f4f7fb"),
        (QPalette.ColorRole.Button, "#ffffff"),
        (QPalette.ColorRole.Text, "#16233a"),
        (QPalette.ColorRole.WindowText, "#16233a"),
        (QPalette.ColorRole.ButtonText, "#16233a"),
        (QPalette.ColorRole.Highlight, "#2b5cd9"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setApplicationName("叙事图工作台")
    render_log = _render_log_path(args.render_log)
    render_log.parent.mkdir(parents=True, exist_ok=True)
    log_lock = threading.Lock()

    def record(message: str) -> None:
        # 写日志永远不能连累调用方：一个满盘或权限问题不该让界面停摆。
        try:
            with log_lock, render_log.open("a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now(UTC).isoformat()} {message}\n")
        except OSError:
            pass

    def qt_message(mode, context, message) -> None:
        category = getattr(context, "category", "qt")
        record(f"qt:{category}:{getattr(mode, 'name', str(mode))}: {message}")

    qInstallMessageHandler(qt_message)
    record(f"startup pid={os.getpid()} qml={QML_PATH} projects_dir={args.projects_dir.resolve()}")
    record(f"requested_backend={os.environ.get('QSG_RHI_BACKEND', '<Qt platform default>')}")
    bridge = NativeBridge(args.projects_dir.resolve())
    # 慢操作写进日志：界面卡的时候，能看出是数据慢、界面慢，还是整体都慢。
    bridge.slowOperation.connect(lambda kind, ms: record(f"slow {kind} {ms}ms"))
    app.aboutToQuit.connect(bridge.close)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    roots = engine.rootObjects()
    if not roots or not isinstance(roots[0], QQuickWindow):
        record("startup_error=QML root is not a QQuickWindow")
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or args.qa_exit_after_seconds is not None:
            print(f"原生界面加载失败；见渲染日志：{render_log}", file=sys.stderr)
        else:
            QMessageBox.critical(None, "叙事图工作台", f"原生界面加载失败。\n渲染日志：{render_log}")
        return 1
    window = roots[0]
    window.show()
    if args.project:
        bridge.openProject(args.project)

    # 看门狗：有操作超过 2 秒没回来就记一行。界面停在「处理中」时，
    # 这行直接说明卡在哪个操作上；同一批只记一次，不刷日志。
    watchdog = QTimer()
    watchdog.setInterval(2000)
    reported: set[tuple[str, ...]] = set()

    def watch_pending() -> None:
        pending = tuple(sorted(bridge.pendingOperations()))
        if pending and pending not in reported:
            reported.add(pending)
            record("stuck " + ",".join(pending))
        elif not pending:
            reported.clear()

    watchdog.timeout.connect(watch_pending)
    watchdog.start()

    probe = QTimer()
    probe.setInterval(100)
    probe_attempts = 0

    def capture_window() -> None:
        if args.qa_snapshot is None:
            return
        target = args.qa_snapshot.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        # grabToImage is asynchronous and captures the actual Qt Quick scene;
        # QQuickWindow.grabWindow() can block with a D3D render thread.
        grab = cast(Any, window.contentItem().grabToImage())
        if grab is None:
            record(f"qa_snapshot={target} saved=false reason=grab_unavailable")
            return

        def save_grab() -> None:
            record(f"qa_snapshot={target} saved={grab.saveToFile(str(target))}")

        grab.ready.connect(save_grab)

    def report_graphics() -> None:
        nonlocal probe_attempts
        probe_attempts += 1
        if not window.isSceneGraphInitialized():
            if probe_attempts >= 150:
                record("actual_graphics_api=unknown scene_graph_initialized=false hardware_acceleration=unverified")
                probe.stop()
            return
        api = window.rendererInterface().graphicsApi()
        rhi_based = QSGRendererInterface.isApiRhiBased(api)
        record(f"actual_graphics_api={api.name} scene_graph_initialized=true rhi_based={rhi_based}")
        record("hardware_adapter=inspect_qt_rhi_adapter_log; D3D11 alone does not prove a physical GPU")
        probe.stop()
        QTimer.singleShot(1000, capture_window)

    probe.timeout.connect(report_graphics)
    probe.start()
    if args.qa_exit_after_seconds is not None:
        QTimer.singleShot(int(args.qa_exit_after_seconds * 1000), app.quit)
    code = app.exec()
    record(f"exit_code={code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
