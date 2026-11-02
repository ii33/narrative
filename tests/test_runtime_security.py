"""Startup must diagnose a Low runtime before ordinary file writes fail."""

import runpy
import sys
from pathlib import Path

import pytest

from narrative import runtime_security


def test_low_runtime_stops_startup_with_clear_diagnostic(monkeypatch, capsys):
    monkeypatch.setattr(runtime_security, "current_integrity_level", lambda: 0x1000)
    script = Path(__file__).resolve().parents[1] / "scripts/check_runtime.py"
    with pytest.raises(SystemExit) as failed:
        runpy.run_path(str(script), run_name="__main__")
    assert failed.value.code == 1
    message = capsys.readouterr().err
    assert "Low" in message and "Medium" in message
    assert "普通工程目录和配置库" in message


@pytest.mark.parametrize("level", [None, 0x2000, 0x3000])
def test_normal_integrity_is_allowed(monkeypatch, level):
    monkeypatch.setattr(runtime_security, "current_integrity_level", lambda: level)
    runtime_security.require_medium_integrity()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process token")
def test_current_windows_process_has_normal_user_integrity():
    assert runtime_security.current_integrity_level() >= runtime_security.MEDIUM_INTEGRITY
