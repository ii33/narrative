"""GUI 里起的子进程不弹控制台。"""

from __future__ import annotations

import os
import subprocess

import pytest

from narrative import quiet_children


@pytest.mark.skipif(os.name != "nt", reason="只有 Windows 有控制台窗口这回事")
def test_children_get_no_console_by_default(monkeypatch) -> None:
    original = subprocess.Popen
    seen: dict = {}

    class Fake(original):
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("stop before spawning")

    # 先换成假的「原始 Popen」，再让 hide_consoles 包一层：包的就是它。
    monkeypatch.setattr(subprocess, "Popen", Fake)
    assert quiet_children.hide_consoles() is True

    with pytest.raises(RuntimeError):
        subprocess.Popen(["cmd", "/c", "echo", "hi"])
    assert seen["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.skipif(os.name != "nt", reason="只有 Windows 有控制台窗口这回事")
def test_explicit_flags_are_left_alone(monkeypatch) -> None:
    original = subprocess.Popen
    seen: dict = {}

    class Fake(original):
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("stop before spawning")

    monkeypatch.setattr(subprocess, "Popen", Fake)
    quiet_children.hide_consoles()
    with pytest.raises(RuntimeError):
        subprocess.Popen(["cmd"], creationflags=subprocess.CREATE_NEW_CONSOLE)
    assert seen["creationflags"] == subprocess.CREATE_NEW_CONSOLE


def test_installing_twice_is_harmless(monkeypatch) -> None:
    original = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", original)
    first = quiet_children.hide_consoles()
    second = quiet_children.hide_consoles()
    assert first == second
    if os.name == "nt":
        assert getattr(subprocess.Popen, "_narrative_no_window", False)
