"""Disclosure-order ids: multi-segment integers compared as integers.

A node's id is where the reader meets it, so ordering needs no separate column.
Each segment is an independent integer; comparing is segment by segment, and a
shorter id comes first when all its segments match:

    1 < 1.1 < 1.1.1 < 1.2 < 1.10 < 2

The dots are separators, not a parent/child relation. Inserting between two ids
adds a segment instead of using decimals, so precision never runs out.
"""

from __future__ import annotations

from collections.abc import Iterable


def order_key(value: str) -> tuple[int, ...] | None:
    """Parse an id into its integer segments; None when it is not an order id."""
    parts = value.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    # "1.01" and "1.1" must not be two spellings of the same id.
    if any(len(part) > 1 and part.startswith("0") for part in parts):
        return None
    segments = tuple(int(part) for part in parts)
    # A trailing zero leaves no room to insert after it, so it is not allowed.
    if segments[-1] == 0:
        return None
    return segments


def is_order_id(value: str) -> bool:
    return order_key(value) is not None


def format_id(segments: Iterable[int]) -> str:
    return ".".join(str(int(part)) for part in segments)


def allocate(
    existing: list[tuple[tuple[int, int], str]], position: tuple[int, int], taken: set[str]
) -> str:
    """Pick the id for a mention at this source position.

    ``existing`` is ``[((chapter, offset), id)]`` for nodes that already have
    one. A mention after everything gets the next top-level number. One that
    falls between two nodes gets ``prev.0.k`` — that is the only shape that sorts
    after ``prev`` and still before the next id, so nothing is renumbered.
    """
    known = [(offset, key) for offset, value in existing if (key := order_key(value))]
    if not known:
        return _first_free((1,), taken)

    before = [item for item in known if item[0] <= position]
    after = [item for item in known if item[0] > position]

    if not after:
        top = max(key[0] for _offset, key in known)
        return _first_free((top + 1,), taken)
    if not before:
        return _first_free((0, 1), taken)

    previous = max(before, key=lambda item: (item[1], item[0]))[1]
    return _first_free(previous + (0, 1), taken)


def _first_free(segments: tuple[int, ...], taken: set[str]) -> str:
    """Bump the last segment until the id is unused."""
    candidate = segments
    while format_id(candidate) in taken:
        candidate = candidate[:-1] + (candidate[-1] + 1,)
    return format_id(candidate)
