import pytest

from narrative import qt_preflight


@pytest.mark.parametrize(
    ("failure", "diagnostic"),
    [
        (ModuleNotFoundError("No module named 'PySide6'", name="PySide6"), "uv sync"),
        (ImportError("DLL load failed: missing runtime"), "DLL load failed: missing runtime"),
    ],
)
def test_qt_preflight_reports_actual_import_failure(monkeypatch, capsys, failure, diagnostic):
    def fail(_name):
        raise failure

    monkeypatch.setattr(qt_preflight.importlib, "import_module", fail)
    with pytest.raises(SystemExit) as exc:
        qt_preflight.check_qt_imports()
    assert exc.value.code == 2
    assert diagnostic in capsys.readouterr().err
