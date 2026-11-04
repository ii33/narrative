"""Check the native Qt modules before starting the windowless interpreter."""

import importlib
import sys


def check_qt_imports() -> None:
    try:
        for module in ("QtCore", "QtGui", "QtQml", "QtQuick", "QtQuickControls2", "QtWidgets"):
            importlib.import_module(f"PySide6.{module}")
    except ImportError as exc:
        if isinstance(exc, ModuleNotFoundError) and exc.name == "PySide6":
            message = "未安装 PySide6；请在项目目录运行 uv sync --locked --extra desktop。"
        else:
            message = "PySide6 导入失败；请检查下面的原始错误和系统运行库。"
        print(f"{message}\n{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
