"""Check the desktop Python paths without shell-embedded Python source."""

import sys
import sysconfig
from pathlib import Path

import narrative
from narrative.qt_preflight import check_qt_imports
from narrative.runtime_security import require_medium_integrity

root = Path(__file__).resolve().parents[1]
for name, path in (
    ("prefix", sys.prefix),
    ("base_prefix", sys.base_prefix),
    ("stdlib", sysconfig.get_path("stdlib")),
    ("application", narrative.__file__),
):
    if not Path(path).resolve().is_relative_to(root):
        raise RuntimeError(f"Python environment escapes the project: {name}={path!r}; project={str(root)!r}")

try:
    require_medium_integrity()
except RuntimeError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1) from None

check_qt_imports()
