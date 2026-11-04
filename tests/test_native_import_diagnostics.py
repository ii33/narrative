import builtins

import pytest

from narrative import native_app, quiet_children


@pytest.mark.parametrize(
    ("error", "expected", "unexpected"),
    [
        (ModuleNotFoundError("No module named 'PySide6'", name="PySide6"),
         "uv sync --locked --extra desktop", "系统运行库"),
        (ImportError("DLL load failed: specified module could not be found"),
         "DLL load failed", "uv sync"),
        (ModuleNotFoundError("No module named 'shiboken6'", name="shiboken6"),
         "shiboken6", "uv sync"),
    ],
)
def test_qt_import_failure_reports_cause(monkeypatch, capsys, error, expected, unexpected):
    original_import = builtins.__import__

    def fail_qt_import(name, *args, **kwargs):
        if name == "PySide6.QtCore":
            raise error
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(quiet_children, "hide_consoles", lambda: None)
    monkeypatch.setattr(builtins, "__import__", fail_qt_import)
    with pytest.raises(SystemExit) as result:
        native_app.main([])
    assert result.value.code == 2
    assert result.value.__cause__ is error
    diagnostic = capsys.readouterr().err
    assert expected in diagnostic
    assert str(error) in diagnostic
    assert unexpected not in diagnostic
