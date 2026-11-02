"""Exact batched Unicode coordinates, bounded in C without evidence IDs."""
from __future__ import annotations


def locate_text(text: str, quotes: list[str], limit_per_quote: int = 20) -> list[dict]:
    if not isinstance(quotes, list) or not quotes or len(quotes) > 1000:
        raise ValueError("quotes须为1到1000条引文的数组")
    if any(not isinstance(quote, str) or not quote for quote in quotes):
        raise ValueError("每条引文须为非空字符串")
    if sum(map(len, quotes)) > 200000:
        raise ValueError("本批引文总长度不能超过200000字符")
    if type(limit_per_quote) is not int or not 1 <= limit_per_quote <= 1000:
        raise ValueError("limit_per_quote须为1到1000的整数")
    cache = {}
    result = []
    for index, quote in enumerate(quotes):
        if quote not in cache:
            matches, cursor = [], 0
            while len(matches) <= limit_per_quote:
                start = text.find(quote, cursor)
                if start < 0:
                    break
                matches.append({"start": start, "end": start+len(quote)})
                cursor = start+1
            observed = len(matches)
            truncated = observed > limit_per_quote
            cache[quote] = {"matches": matches[:limit_per_quote], "match_count": observed,
                            "count_is_exact": not truncated, "truncated": truncated,
                            "diagnostic": "not_found" if observed == 0 else "unique" if observed == 1 else "ambiguous"}
        result.append({"index": index, **cache[quote]})
    return result
