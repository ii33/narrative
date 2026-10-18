"""Read-only token accounting over saved chunk metadata, independent of UI and SDK."""

import json

FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
)
ALIASES = {
    "input_tokens": ("inputTokens", "input_tokens", "prompt_tokens"),
    "output_tokens": ("outputTokens", "output_tokens", "completion_tokens"),
    "total_tokens": ("totalTokens", "total_tokens"),
    "cached_input_tokens": ("cachedInputTokens", "cached_input_tokens", "prompt_cache_hit_tokens"),
    "reasoning_tokens": ("reasoningTokens", "reasoning_tokens"),
}


def _object(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _count(values: dict, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = values.get(key)
        if type(value) is int and value >= 0:
            return value
    return None


def _name(*values) -> str:
    return next((value for value in values if isinstance(value, str) and value), "unknown")


def normalize(value) -> dict:
    """Each saved usage entry is a distinct SDK report; never deduplicate by token value."""
    entries = value if isinstance(value, list) else [value]
    if not entries:
        entries = [None]
    records = []
    for entry in entries:
        entry = _object(entry)
        row = {field: _count(entry, ALIASES[field]) for field in FIELDS}
        if row["cached_input_tokens"] is None:
            row["cached_input_tokens"] = _count(
                _object(entry.get("prompt_tokens_details")), ("cached_tokens",)
            )
        if row["reasoning_tokens"] is None:
            row["reasoning_tokens"] = _count(
                _object(entry.get("completion_tokens_details")), ("reasoning_tokens",)
            )
        if row["total_tokens"] is None:
            first, last = row["input_tokens"], row["output_tokens"]
            if first is not None and last is not None:
                row["total_tokens"] = first + last
        records.append(row)
    result = _totals(records)
    result["usage_reports"] = sum(
        any(row[field] is not None for field in FIELDS) for row in records
    )
    return result


def _totals(rows: list[dict]) -> dict:
    result = {}
    partial = []
    for field in FIELDS:
        values = [row[field] for row in rows if row[field] is not None]
        result[field] = sum(values) if values else (0 if not rows and field in FIELDS[:3] else None)
        if any(row[field] is None or field in row.get("partial_fields", []) for row in rows):
            partial.append(field)
    result["partial_fields"] = partial
    return result


def _summary(rows: list[dict]) -> dict:
    return {
        **_totals(rows),
        "counted_chunks": len(rows),
        "reported_chunks": sum(row["usage_reports"] > 0 for row in rows),
        "incomplete_chunks": sum(
            any(field in row["partial_fields"] for field in FIELDS[:3]) for row in rows
        ),
        "usage_reports": sum(row["usage_reports"] for row in rows),
    }


def summarize(chunks: list[dict], runs: list[dict]) -> dict:
    run_map = {run["id"]: _object(run["data"]) for run in runs}
    details = []
    for chunk in chunks:
        metadata = _object(chunk.get("metadata"))
        # Manual correction audit copies the original metadata. Count only the top level,
        # not nested model_response/manual_corrections, nor candidates or run snapshots.
        value = metadata.get("usage")
        if (
            chunk["status"] == "pending"
            and not chunk.get("has_result", chunk.get("raw") is not None)
            and value is None
        ):
            continue
        run = run_map[chunk["run_id"]]
        settings = _object(run.get("settings"))
        amounts = normalize(value)
        if metadata.get("usage_incomplete"):
            amounts["partial_fields"] = list(FIELDS)
        details.append(
            {
                "chunk_id": chunk["id"],
                "run_id": chunk["run_id"],
                "status": chunk["status"],
                "provider": _name(metadata.get("provider"), settings.get("provider")),
                "model": _name(metadata.get("model"), run.get("model"), settings.get("model")),
                **amounts,
            }
        )
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in details:
        groups.setdefault((row["provider"], row["model"]), []).append(row)
    return {
        **_summary(details),
        "by_model": [
            {"provider": provider, "model": model, **_summary(rows)}
            for (provider, model), rows in sorted(groups.items())
        ],
        "chunks": details,
    }
