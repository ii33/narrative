"""Slice extraction rounds, anchored to the immutable complete source text."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .project_package import layout

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _directory(ops, source_id: str, slice_id: str) -> Path:
    # IDs originate in Operations; never interpolate a caller-supplied path.
    ops.source_slice(source_id, slice_id)
    token = hashlib.sha256(f"{source_id}:{slice_id}".encode()).hexdigest()
    return layout(ops.project_info().path).extraction / "slices" / token


def _lock(path: Path):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path.resolve()), threading.RLock())


@contextmanager
def _operation_lock(directory: Path):
    from .leases import ProjectLease

    directory.mkdir(parents=True, exist_ok=True)
    with _lock(directory):
        try:
            lease = ProjectLease(directory / "workflow.lock")
        except OSError as exc:
            raise ValueError("当前切片正在提取、复核或提交，请等待本轮完成") from exc
        try:
            yield
        finally:
            lease.close()


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def load(ops, source_id: str, slice_id: str) -> dict:
    directory = _directory(ops, source_id, slice_id)
    index = directory / "latest.json"
    if not index.is_file():
        return {"source_id": source_id, "slice_id": slice_id, "missing": True,
                "nodes": [], "properties": [], "counts": {}, "round": 0}
    pointer = json.loads(index.read_text(encoding="utf-8"))
    data = json.loads((directory / pointer["result"]).read_text(encoding="utf-8"))
    source, part = ops.source(source_id), ops.source_slice(source_id, slice_id)
    if (data.get("source_hash") != source["hash"]
        or data.get("source_hash") != hashlib.sha256(source["text"].encode()).hexdigest()
        or data.get("slice_start") != part["start"] or data.get("slice_end") != part["end"]):
        raise ValueError("原文或切片范围已变化，不能复用历史提取结果")
    return data


def _offset(data: dict, delta: int) -> dict:
    data = copy.deepcopy(data)
    for key in ("nodes", "properties"):
        for row in data.get(key) or []:
            if isinstance(row.get("start"), int):
                row["start"] += delta
    return _group(data)


def _group(data: dict) -> dict:
    from .extraction import NODE_TYPES

    data["grouped"] = {kind: [] for kind in NODE_TYPES}
    for row in data.get("nodes") or []:
        data["grouped"].setdefault(row["type"], []).append(row)
    data["counts"] = {kind: len(rows) for kind, rows in data["grouped"].items()}
    return data


def _bound(data: dict, part: dict) -> dict:
    """Audit additions may only cite this slice, even while the session knows more."""
    data = copy.deepcopy(data)
    rejected = list(data.get("rejected") or [])
    for key in ("nodes", "properties"):
        kept = []
        for row in data.get(key) or []:
            quotes = row.get("quotes") or []
            positions = [part["text"].find(quote) for quote in quotes if isinstance(quote, str) and quote]
            if not positions or len(positions) != len(quotes) or min(positions) < 0:
                rejected.append({"name": row.get("name") or row.get("node"),
                                 "reason": "引文不在当前切片范围内"})
                continue
            row["start"] = part["start"] + min(positions)
            kept.append(row)
        data[key] = kept
    data["rejected"] = rejected
    return _group(data)


def _merge(previous: dict, current: dict) -> dict:
    result = dict(current)
    for key, fields in (
        ("nodes", ("type", "name", "start")),
        ("properties", ("node", "property", "value", "value_type", "scope", "holder", "at_event", "start")),
    ):
        rows, seen = [], set()
        for row in (previous.get(key) or []) + (current.get(key) or []):
            token = json.dumps([row.get(field) for field in fields], ensure_ascii=False, sort_keys=True)
            if token in seen:
                continue
            seen.add(token)
            rows.append(copy.deepcopy(row))
        result[key] = sorted(rows, key=lambda row: (row.get("start", 0), str(row.get("name") or row.get("node") or "")))
    # A completeness pass adds evidence; it never erases a previous valid result.
    for key in ("supersede", "retire"):
        seen, rows = set(), []
        for row in (previous.get(key) or []) + (current.get(key) or []):
            token = json.dumps(row, ensure_ascii=False, sort_keys=True)
            if token not in seen:
                seen.add(token)
                rows.append(row)
        if rows:
            result[key] = rows
    return _group(result)


def extract(ops, source_id: str, slice_id: str, *, review: bool = False) -> dict:
    from .extraction import read_chapter, review_chapter
    from .harness import harness_env

    directory = _directory(ops, source_id, slice_id)
    with _operation_lock(directory):
        part = ops.source_slice(source_id, slice_id)
        source = ops.source(source_id)
        previous = load(ops, source_id, slice_id)
        if review and previous["missing"]:
            raise ValueError("该切片尚未提取，请先提取再复核")
        number = int(previous.get("round") or 0) + 1
        run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}"
        run_dir = directory / "rounds" / run_id
        run_dir.mkdir(parents=True)
        request = {
            "source_id": source_id, "slice_id": slice_id, "round": number,
            "mode": "review" if review else "extract", "source_hash": source["hash"],
            "slice_sha256": hashlib.sha256(part["text"].encode()).hexdigest(),
            "start": part["start"], "end": part["end"],
        }
        _write(run_dir / "request.json", request)
        prior = None if previous["missing"] else _offset(previous, -part["start"])
        try:
            settings, key, _ = harness_env(ops)
            request["settings"] = settings.model_dump(exclude={"key_env"})
            _write(run_dir / "request.json", request)
            data = read_chapter(
                part["title"], part["text"], settings=settings, api_key=key,
                workdir=run_dir / "read", ops=ops, prior=prior, source_id=source_id,
            )
            data = _offset(data, part["start"])
            data.update(source_id=source_id, source_hash=source["hash"], slice_id=slice_id,
                        slice_text=part["text"], slice_start=part["start"], slice_end=part["end"])
            _write(run_dir / "read-result.json", data)
            data = review_chapter(ops, data, settings=settings, api_key=key, workdir=run_dir / "audit")
            if (data.get("review") or {}).get("error"):
                _write(run_dir / "audit-incomplete.json", data)
                raise ValueError("切片属性审核未完成，本轮结果未替换上一轮")
            data = _bound(data, part)
            if not previous["missing"]:
                data = _merge(previous, data)
            data.update({
                "source_id": source_id, "slice_id": slice_id, "missing": False,
                "source_hash": source["hash"], "slice_start": part["start"], "slice_end": part["end"],
                "round": number, "run_id": run_id, "mode": "review" if review else "extract",
                "extracted_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "added_nodes": len(data.get("nodes") or []) - len(previous.get("nodes") or []),
                "added_properties": len(data.get("properties") or []) - len(previous.get("properties") or []),
            })
            data.pop("raw", None)
            _write(run_dir / "result.json", data)
            _write(directory / "latest.json", {"result": f"rounds/{run_id}/result.json"})
            return data
        except Exception as exc:
            # Raw replies and usage remain in the round's harness output directory.
            _write(run_dir / "failure.json", {"status": "failed", "error_type": type(exc).__name__,
                                               "round": number, "run_id": run_id})
            raise


def commit(ops, source_id: str, slice_id: str, *, review: str = "doubtful") -> dict:
    from .extraction import commit_extraction
    from .extraction_session import record_review_action

    directory = _directory(ops, source_id, slice_id)
    with _operation_lock(directory):
        data = load(ops, source_id, slice_id)
        if data["missing"]:
            raise ValueError("该切片尚未提取")
        if review not in {"doubtful", "all"}:
            raise ValueError("审核模式须为 doubtful 或 all")
        token = hashlib.sha256(f"{data['run_id']}:{review}".encode()).hexdigest()
        cached = directory / "commits" / f"{token}.json"
        if cached.is_file():
            return json.loads(cached.read_text(encoding="utf-8"))
        result = commit_extraction(ops, data, review=review)
        result = {"source_id": source_id, "slice_id": slice_id, "round": data["round"], **result}
        # Persist the outcome before journalling, so a repeated click cannot create
        # another pending candidate if journalling fails after the graph write.
        _write(cached, result)
        action = {"action": "commit_slice_extraction", "source_id": source_id, "slice_id": slice_id,
                  "round": data["round"], "review": review, "result": result}
        record_review_action(ops, action)
        _write(directory / "actions" / f"{uuid4().hex}.json", action)
        return result
