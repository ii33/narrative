"""GUI 里起的子进程不许弹控制台窗口。

dsh 运行时是个控制台程序。窗口用 `pythonw` 启动时本来没有控制台，子进程一被拉起，
Windows 就给它开一个黑窗口——每问一句就闪一个终端，很难看，也容易误关。

`CREATE_NO_WINDOW` 让它无窗口运行。SDK 起进程时没有传这个标志，也不接受额外参数，
所以这里在进程级别装一次默认值：**只补默认，不覆盖调用方显式给的 flags**。
"""

from __future__ import annotations

import os
import subprocess


def hide_consoles() -> bool:
    """装一次；已经装过就什么都不做。返回是否处于「已生效」状态。"""
    if os.name != "nt":
        return False
    original = subprocess.Popen
    if getattr(original, "_narrative_no_window", False):
        return True

    class NoWindowPopen(original):  # type: ignore[misc, valid-type]
        _narrative_no_window = True

        def __init__(self, *args, **kwargs):
            # 调用方自己指定了 flags（比如要新控制台）就照它的来。
            kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
            super().__init__(*args, **kwargs)

    subprocess.Popen = NoWindowPopen  # type: ignore[misc]
    return True
