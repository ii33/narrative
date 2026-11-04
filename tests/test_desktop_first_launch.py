"""Exercise first-launch preparation in an isolated Windows project directory."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop launcher")


def copy_runtime(source, destination):
    destination.mkdir(parents=True)
    for name in ("python.exe", "pythonw.exe"):
        shutil.copy2(source / name, destination / name)
    for dll in source.glob("*.dll"):
        shutil.copy2(dll, destination / dll.name)
    shutil.copytree(source / "DLLs", destination / "DLLs")
    shutil.copytree(source / "Lib", destination / "Lib", ignore=shutil.ignore_patterns("site-packages", "__pycache__", "test"))


@pytest.fixture
def launcher(tmp_path):
    root = tmp_path / "project"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    repo = Path(__file__).resolve().parents[1]
    shutil.copy2(repo / "start-desktop.ps1", root / "start-desktop.ps1")
    venv = root / ".venv"
    (venv / "Scripts").mkdir(parents=True)
    launchers = Path(sys.base_prefix) / "Lib/venv/scripts/nt"
    shutil.copy2(launchers / "venvlauncher.exe", venv / "Scripts/python.exe")
    shutil.copy2(launchers / "venvwlauncher.exe", venv / "Scripts/pythonw.exe")
    (venv / "pyvenv.cfg").write_text(
        f"home = {sys.base_prefix}\nversion_info = {sys.version_info.major}.{sys.version_info.minor}\n",
        encoding="utf-8",
    )
    (scripts / "check_runtime.py").write_text(
        "from pathlib import Path\n"
        "root = Path(__file__).resolve().parents[1]\n"
        "(root / 'checked.txt').write_text((root / '.venv/pyvenv.cfg').read_text())\n"
        "raise SystemExit(17)\n",
        encoding="utf-8",
    )

    def run(*args):
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "start-desktop.ps1"), *args],
            cwd=root, text=True, capture_output=True, timeout=30,
        )

    return root, run


def test_first_launch_prepares_runtime_then_checks_isolation(launcher):
    root, run = launcher
    (root / "scripts/localize_python.py").write_text(
        "from pathlib import Path\n"
        "import shutil, sys\n"
        "root = Path(__file__).resolve().parents[1]\n"
        "runtime = root / '.runtime/python'\n"
        "runtime.mkdir(parents=True)\n"
        "source = Path(sys.base_prefix)\n"
        "[shutil.copy2(p, runtime / p.name) for p in source.glob('*.dll')]\n"
        "shutil.copy2(source / 'python.exe', runtime / 'python.exe')\n"
        "shutil.copytree(source / 'DLLs', runtime / 'DLLs')\n"
        "shutil.copytree(source / 'Lib', runtime / 'Lib', ignore=shutil.ignore_patterns('site-packages', '__pycache__', 'test'))\n"
        "(root / 'prepared.txt').touch()\n",
        encoding="utf-8",
    )
    result = run()
    assert result.returncode != 0  # The isolation probe deliberately fails.
    assert (root / "prepared.txt").exists()
    assert "Preparing the project-local Python runtime" in result.stdout
    assert (root / "checked.txt").exists(), result.stderr
    assert f"home = {root / '.runtime/python'}" in (root / "checked.txt").read_text()
    assert "Project startup checks failed" in result.stderr


def test_existing_runtime_is_reused_and_moved_venv_repointed(launcher):
    root, run = launcher
    runtime = root / ".runtime/python"
    copy_runtime(Path(sys.base_prefix), runtime)
    (root / "scripts/localize_python.py").write_text("raise SystemExit(99)\n", encoding="utf-8")
    result = run()
    assert result.returncode != 0
    assert "Preparing the project-local Python runtime" not in result.stdout
    assert (root / "checked.txt").exists(), result.stderr
    assert f"home = {runtime}" in (root / "checked.txt").read_text()


def test_partial_runtime_is_not_overwritten(launcher):
    root, run = launcher
    runtime = root / ".runtime/python"
    runtime.mkdir(parents=True)
    (runtime / "keep.txt").write_text("keep", encoding="utf-8")
    (root / "scripts/localize_python.py").write_text("raise SystemExit(99)\n", encoding="utf-8")
    result = run()
    assert result.returncode != 0
    assert "Project runtime is incomplete" in result.stderr
    assert (runtime / "keep.txt").read_text() == "keep"
    assert not (root / "checked.txt").exists()


def test_preparation_failure_stops_startup(launcher):
    root, run = launcher
    (root / "scripts/localize_python.py").write_text("raise SystemExit(23)\n", encoding="utf-8")
    result = run()
    assert result.returncode != 0
    assert "Project runtime preparation failed" in result.stderr
    assert not (root / "checked.txt").exists()


def test_prepare_only_checks_without_starting_ui(launcher):
    root, run = launcher
    copy_runtime(Path(sys.base_prefix), root / ".runtime/python")
    (root / "scripts/check_runtime.py").write_text("print('checks passed')\n", encoding="utf-8")
    result = run("-PrepareOnly")
    assert result.returncode == 0, result.stderr
    assert "Project environment is ready" in result.stdout
    assert "Native desktop started" not in result.stdout


def test_mismatched_runtime_version_is_not_repaired(launcher):
    root, run = launcher
    copy_runtime(Path(sys.base_prefix), root / ".runtime/python")
    cfg = root / ".venv/pyvenv.cfg"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        f"version_info = {sys.version_info.major}.{sys.version_info.minor}",
        "version_info = 1.0",
    ), encoding="utf-8")
    launcher_before = (root / ".venv/Scripts/python.exe").read_bytes()
    result = run("-PrepareOnly")
    assert result.returncode != 0
    assert "versions do not match" in result.stderr
    assert (root / ".venv/Scripts/python.exe").read_bytes() == launcher_before


def test_setup_runs_locked_sync_before_preparation(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    shutil.copy2(repo / "setup-environment.ps1", tmp_path / "setup-environment.ps1")
    (tmp_path / "start-desktop.ps1").write_text(
        "param([switch]$PrepareOnly)\nif (-not $PrepareOnly) { throw 'Expected PrepareOnly' }\n"
        "Set-Content -LiteralPath prepared.txt -Value ready\n", encoding="utf-8"
    )
    (tmp_path / "uv.cmd").write_text("@echo %* > sync-arguments.txt\n@exit /b 0\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp_path / "setup-environment.ps1")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "sync-arguments.txt").read_text().strip() == "sync --locked --all-packages --all-extras --all-groups"
    assert (tmp_path / "prepared.txt").exists()


def test_localization_shows_probe_import_error(tmp_path, monkeypatch, capsys):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / ".venv").mkdir()
    (root / ".venv/pyvenv.cfg").write_text("home = old\n", encoding="utf-8")
    (root / ".venv/Scripts").mkdir()
    site_packages = root / ".venv/Lib/site-packages"
    site_packages.mkdir(parents=True)
    cached_pth = tmp_path / "cached.pth"
    cached_pth.write_text("cached original\n", encoding="utf-8")
    editable = site_packages / "_editable_impl_narrative_core.pth"
    os.link(cached_pth, editable)
    source = tmp_path / "python"
    (source / "Lib/encodings").mkdir(parents=True)
    (source / "DLLs").mkdir()
    launchers = source / "Lib/venv/scripts/nt"
    launchers.mkdir(parents=True)
    for name, target in (("venvlauncher.exe", "python.exe"), ("venvwlauncher.exe", "pythonw.exe")):
        (launchers / name).write_text(name, encoding="utf-8")
        (root / ".venv/Scripts" / target).write_text(name, encoding="utf-8")
    for name in ("python.exe", "pythonw.exe"):
        (source / name).touch()
    path = Path(__file__).resolve().parents[1] / "scripts/localize_python.py"
    spec = importlib.util.spec_from_file_location("localize_python_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "__file__", str(root / "scripts/localize_python.py"))
    monkeypatch.setattr(sys, "argv", ["localize_python.py", "--source", str(source)])
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout="", stderr="ImportError: DLL load failed\n"
    ))
    assert module.main() == 1
    error = capsys.readouterr().err
    assert "ImportError: DLL load failed" in error
    assert "Project runtime verification failed" in error
    assert cached_pth.read_text(encoding="utf-8") == "cached original\n"
    assert editable.read_text(encoding="utf-8") == "../../../src\n"
