"""Copy an installed CPython distribution into this project's runtime closure.

Run with the existing project Python before distributing or moving the directory.
This command uses local files only; it never downloads or installs packages.
"""

from __future__ import annotations

import argparse
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
    lines = config.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith(("home =", "executable =", "command ="))]
    lines.insert(0, "home = " + str(destination))
    replacement = config.with_suffix(".cfg.tmp")
    replacement.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(replacement, config)
    # Hatch's editable install otherwise records the original checkout path.
    editable = root / ".venv/Lib/site-packages/_editable_impl_narrative_core.pth"
    if editable.is_file():
        editable.write_text("../../../src\n", encoding="utf-8")
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
        cwd=root, env=env, capture_output=True, text=True, check=True,
    )
    result = json.loads(probe.stdout)
    for name in ("prefix", "base_prefix", "stdlib", "application"):
        if not Path(result[name]).resolve().is_relative_to(root):
            raise RuntimeError(f"Python still uses an external {name}")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
