"""OS-level project lease shared by graph and legacy agents."""

from __future__ import annotations

import os
from pathlib import Path

CAPACITY_FINISH_REASONS = frozenset(
    {"max-tokens", "max_tokens", "max_completion_tokens", "max_output_tokens", "length"}
)


def is_capacity_finish(reason: str | None) -> bool:
    return isinstance(reason, str) and reason.lower() in CAPACITY_FINISH_REASONS


class ProjectLease:
    """OS lock survives UI reruns and releases automatically on process exit."""

    def __init__(self, path: Path):
        self.file = path.open("a+b")
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.fstat(self.file.fileno()).st_size == 0:
                self.file.write(b"0")
                self.file.flush()
        except OSError:
            self.file.close()
            raise

    def close(self) -> None:
        self.release()

    def release(self) -> None:
        try:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
