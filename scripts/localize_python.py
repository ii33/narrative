"""Copy an installed CPython distribution into this project's runtime closure.

Run with the existing project Python before distributing or moving the directory.
This command uses local files only; it never downloads or installs packages.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(sys.base_prefix))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = args.source.resolve()
    destination = root / ".runtime" / "python"
    config = root / ".venv" / "pyvenv.cfg"
    if not config.is_file():
        parser.error("The project .venv must already contain its installed dependencies.")
    for required in ("python.exe", "pythonw.exe", "Lib/encodings", "DLLs"):
        if not (source / required).exists():
            parser.error(f"Incomplete CPython distribution: {source / required}")
    if source != destination.resolve():
        if source in destination.resolve().parents:
            parser.error("The destination must not be inside the source distribution.")
        shutil.copytree(source, destination, dirs_exist_ok=True)
    # uv 0.13 launchers can embed the original interpreter path and ignore
    # pyvenv.cfg. Use the matching CPython redirectors, which honor that file.
    # This script must be run by a Python outside .venv so Windows does not
    # keep the launcher being replaced open.
    for launcher, target in (("venvlauncher.exe", "python.exe"), ("venvwlauncher.exe", "pythonw.exe")):
        original = destination / "Lib/venv/scripts/nt" / launcher
        installed = root / ".venv/Scripts" / target
        if not original.is_file() or not installed.is_file():
            raise RuntimeError(f"Missing CPython redirector or virtual environment executable: {original}, {installed}")
        if not filecmp.cmp(original, installed, shallow=False):
            temporary = installed.with_suffix(".exe.narrative-tmp")
            try:
                shutil.copyfile(original, temporary)
                os.replace(temporary, installed)
            except PermissionError as exc:
                raise RuntimeError(
                    f"Cannot replace {installed}; close processes using this virtual environment and start again."
                ) from exc
            finally:
                temporary.unlink(missing_ok=True)
    lines = config.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith(("home =", "executable =", "command ="))]
    lines.insert(0, "home = " + str(destination))
    replacement = config.with_suffix(".cfg.tmp")
    replacement.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(replacement, config)
    # Hatch's editable install otherwise records the original checkout path.
    editable = root / ".venv/Lib/site-packages/_editable_impl_narrative_core.pth"
    if editable.is_file():
        editable_replacement = editable.with_suffix(".pth.tmp")
        editable_replacement.write_text("../../../src\n", encoding="utf-8")
        os.replace(editable_replacement, editable)
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    probe = subprocess.run(
        [str(root / ".venv/Scripts/python.exe"), "-I", "-c",
         "import json,sys,sysconfig; import narrative,pydantic,PySide6,deepseek_harness; "
         "print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,"
         "'stdlib':sysconfig.get_path('stdlib'),'application':narrative.__file__}))"],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    if probe.returncode:
        if probe.stdout:
            print(probe.stdout, end="", file=sys.stderr)
        if probe.stderr:
            print(probe.stderr, end="", file=sys.stderr)
        print("Project runtime verification failed.", file=sys.stderr)
        return probe.returncode
    result = json.loads(probe.stdout)
    for name in ("prefix", "base_prefix", "stdlib", "application"):
        if not Path(result[name]).resolve().is_relative_to(root):
            raise RuntimeError(f"Python still uses an external {name}: {result[name]!r}; project: {str(root)!r}")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
