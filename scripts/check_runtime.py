"""Check the desktop Python paths without shell-embedded Python source."""

import sys
import sysconfig
from pathlib import Path

import narrative
from narrative.runtime_security import require_medium_integrity

root = Path(__file__).resolve().parents[1]
assert all(
    Path(path).resolve().is_relative_to(root)
    for path in (sys.prefix, sys.base_prefix, sysconfig.get_path("stdlib"), narrative.__file__)
), "Python environment escapes the project"

try:
    require_medium_integrity()
except RuntimeError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1) from None
