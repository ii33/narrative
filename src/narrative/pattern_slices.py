"""Bounded heading-pattern scanning; offsets are calculated by the program."""
from __future__ import annotations

import time

import regex

PATTERN_VERSION = "pattern-slices/2"
SCAN_TIMEOUT_SECONDS = 2.0
MAX_HEADINGS = 100_000


def plan(source_id: str, text: str, pattern: str, start_index: int = 0,
         limit: int | None = None) -> dict:
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("pattern须为非空正则表达式，匹配一个完整标题行")
    if type(start_index) is not int or start_index < 0:
        raise ValueError("start_index须为从0开始的章节索引")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit须为正整数或省略以选择剩余全部章节")
    started = time.monotonic()
    try:
        compiled = regex.compile(pattern, regex.MULTILINE)
        headings = []
        for match in compiled.finditer(text, timeout=SCAN_TIMEOUT_SECONDS):
            title = match.group().strip()
            if match.start() == match.end() or not title or "\n" in title or "\r" in title:
                raise ValueError("pattern须匹配非空的单行标题，不能匹配空位置或多行正文")
            if len(headings) >= MAX_HEADINGS:
                raise ValueError("标题匹配超过100000个，请收窄模式；未创建切片")
            headings.append((match.start(), title))
    except TimeoutError as exc:
        raise ValueError("标题模式扫描超时，请收窄模式后重新预览；未创建切片") from exc
    except regex.error as exc:
        raise ValueError(f"标题模式无效：{exc}") from exc
    if not headings:
        raise ValueError("标题模式未匹配到任何标题")
    if start_index >= len(headings):
        raise ValueError("start_index超出已匹配标题范围")
    stop = len(headings) if limit is None else min(start_index + limit, len(headings))
    slices = [{"title": headings[i][1], "start": headings[i][0],
               "end": headings[i + 1][0] if i + 1 < len(headings) else len(text)}
              for i in range(start_index, stop)]
    result = {"version": PATTERN_VERSION, "source_id": source_id,
              "pattern": pattern, "start_index": start_index, "limit": limit,
              "match_count": len(headings), "selected_count": len(slices),
              "prefix": {"start": 0, "end": headings[0][0], "policy": "excluded"},
              "slices": slices}
    return result | {"scan_seconds": time.monotonic() - started}


def compact(result: dict) -> dict:
    """Explicit first/last samples, rather than returning thousands of offsets."""
    rows = result["slices"]
    sample = rows if len(rows) <= 6 else rows[:3] + rows[-3:]
    return {key: value for key, value in result.items() if key != "slices"} | {
        "sampled": len(sample) < len(rows), "sample_count": len(sample), "slice_samples": sample,
    }
