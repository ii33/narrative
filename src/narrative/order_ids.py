"""Node ids: one sequence per node type, multi-segment integers compared as integers.

**每种节点各有自己的编号序列**（P1…P11、I1…I31、E1…E81），编号之间互不相关：
人是第几个、物品是第几个，两件事没有共同刻度。所以 `P3` 和 `I3` 之间没有先后，
跨类型要比先后只能看披露位置（`browse_view` 就是这么排的）。

同一类型内部仍然是「按读者遇到它的顺序发号」：每个段落是独立整数，逐段比较，
前面的段都相同时短的在前：

    1 < 1.1 < 1.1.1 < 1.2 < 1.10 < 2

点只是分隔符，不表示父子。在两段之间插入时加一段而不是用小数，精度永远够用。
编号可以带类型前缀（`P7`）也可以不带（`7`，老数据和测试夹具），解析时都认。
"""

from __future__ import annotations

from collections.abc import Iterable

# 编号带类型前缀：`P7` 是第七个人，`E1.2` 是第一个事件之后插入的那一个。
# 前缀是**编号的一部分**（存下来的就是 `P7`），所以两个类型各自从 1 开始也不冲突。
TYPE_PREFIX = {
    "person": "P",
    "group": "G",
    "item": "I",
    "location": "L",
    "event": "E",
    "scene": "S",
    "storyline": "T",
}
UNKNOWN_PREFIX = "N"


def prefix_for(node_type: str | None) -> str:
    return TYPE_PREFIX.get((node_type or "").strip(), UNKNOWN_PREFIX)


def label_id(node_id: str, node_type: str | None) -> str:
    """`7` + person → `P7`；`1.2` + event → `E1.2`；`P7` 已经是编号就原样返回。"""
    if not node_id:
        return ""
    if node_id[0].isascii() and node_id[0].isalpha():
        return node_id
    return f"{prefix_for(node_type)}{node_id}"


def _segments(value: str) -> tuple[int, ...] | None:
    """把 `1.2` 这种纯数字编号拆成整数段；不是这个形状就 None。"""
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


def split_id(value: str) -> tuple[str, tuple[int, ...]] | None:
    """`P7` → ("P", (7,))；`7` → ("", (7,))；不是编号就 None。

    前缀只认已知的那几个字母，别的东西（`val_63da…`）仍然不是编号。
    """
    if not isinstance(value, str) or not value:
        return None
    prefix = ""
    rest = value
    head = value[0]
    if head.isascii() and head.isalpha():
        prefix, rest = head, value[1:]
        if prefix not in TYPE_PREFIX.values():
            return None
    segments = _segments(rest)
    if segments is None:
        return None
    return prefix, segments


def order_key(value: str) -> tuple[int, ...] | None:
    """Parse an id into its integer segments, prefix or not; None when it is not an id."""
    parsed = split_id(value)
    return parsed[1] if parsed else None


def format_id(segments: Iterable[int]) -> str:
    return ".".join(str(int(part)) for part in segments)


def allocate(
    existing: list[tuple[tuple[int, int], str]],
    position: tuple[int, int],
    taken: set[str],
    prefix: str = "",
) -> str:
    """Pick the id for a mention at this source position, inside one type's sequence.

    ``existing`` is ``[((chapter, offset), id)]`` for the nodes **of this type**
    that already have one, and ``taken`` is the same type's ids: two types each
    start at 1, so they must not share a counter. A mention after everything
    gets the next top-level number. One that falls between two nodes gets
    ``prev.0.k`` — the only shape that sorts after ``prev`` and still before the
    next id, so nothing is renumbered.
    """
    known = [(offset, key) for offset, value in existing if (key := order_key(value))]
    if not known:
        return _first_free((1,), taken, prefix)

    before = [item for item in known if item[0] <= position]
    after = [item for item in known if item[0] > position]

    if not after:
        top = max(key[0] for _offset, key in known)
        return _first_free((top + 1,), taken, prefix)
    if not before:
        return _first_free((0, 1), taken, prefix)

    previous = max(before, key=lambda item: (item[1], item[0]))[1]
    return _first_free(previous + (0, 1), taken, prefix)


def _first_free(segments: tuple[int, ...], taken: set[str], prefix: str = "") -> str:
    """Bump the last segment until the id is unused (with or without the prefix)."""
    candidate = segments
    while format_id(candidate) in taken or f"{prefix}{format_id(candidate)}" in taken:
        candidate = candidate[:-1] + (candidate[-1] + 1,)
    return f"{prefix}{format_id(candidate)}"
