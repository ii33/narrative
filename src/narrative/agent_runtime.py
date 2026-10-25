"""Shared agent runtime helpers used by legacy and graph agents.

These are deliberately project-agnostic: budget parsing, deterministic hashing,
JSON serialization, journal I/O, compaction and usage aggregation. Agent-specific
run layout remains in the agent modules.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

MAX_OBSERVATION_CHARS = 32000


@dataclass
class Budget:
    research_rounds: int = 3
    max_rounds: int | None = None
    max_tokens_per_round: int | Literal["inherit", "service-default"] = "inherit"
    max_total_tokens: int | None = None
    wall_seconds: int | None = None
    request_seconds: int = 180

    @classmethod
    def parse(cls, supplied: dict | None) -> Budget:
        values = supplied or {}
        if not isinstance(values, dict) or set(values) - set(cls.__dataclass_fields__):
            raise ValueError("分析预算字段无效")
        budget = cls(**values)
        limits = {
            "research_rounds": (1, 100),
            "request_seconds": (5, 600),
        }
        for key, (minimum, maximum) in limits.items():
            value = getattr(budget, key)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"分析预算 {key} 须为 {minimum}–{maximum} 的整数")
        output_limit = budget.max_tokens_per_round
        if not (
            output_limit in {"inherit", "service-default"}
            if isinstance(output_limit, str)
            else type(output_limit) is int and output_limit > 0
        ):
            raise ValueError("分析预算 max_tokens_per_round 须为 inherit、service-default 或正整数")
        for key in ("max_rounds", "max_total_tokens", "wall_seconds"):
            value = getattr(budget, key)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"分析预算 {key} 须为正整数或 None")
        if budget.wall_seconds is not None and budget.request_seconds > budget.wall_seconds:
            raise ValueError("单请求时间不能超过总时间")
        return budget

    def asdict(self) -> dict:
        return {key: getattr(self, key) for key in self.__dataclass_fields__}

    def resolved_max_tokens(self, saved) -> int | None:
        if self.max_tokens_per_round == "inherit":
            return saved.max_tokens
        if self.max_tokens_per_round == "service-default":
            return None
        return self.max_tokens_per_round


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _code_hash() -> str:
    digest = hashlib.sha256()
    directory = Path(__file__).parent
    for path in sorted((*directory.glob("*.py"), directory / "extraction.patch.yml")):
        digest.update(path.name.encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _save(path: Path, state: dict) -> None:
    if "phase" in state:
        state["phase_remaining"] = (
            max(0, state["budget"]["research_rounds"] - state["phase_rounds_used"]["research"])
            if state["phase"] == "research"
            else None
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"journal.{uuid4().hex}.tmp")
    temporary.write_text(_json(state), encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 9:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.01)


def _read(path: Path) -> dict:
    if not path.is_file():
        raise ValueError("分析任务不存在")
    for attempt in range(10):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.01)
    raise AssertionError("unreachable")


def _compact(value, limit: int = MAX_OBSERVATION_CHARS):
    encoded = _json(value)
    if len(encoded) <= limit:
        return value
    return {
        "truncated": True,
        "size": len(encoded),
        "message": "结果过长，请缩小检索范围",
        "preview": encoded[:1000],
    }


def _usage_totals(steps: list[dict]) -> dict:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    unknown = False
    for step in steps:
        reports = step.get("usage") or []
        if not reports or any(not isinstance(report, dict) for report in reports):
            unknown = True
            continue
        for report in reports:
            for key in totals:
                count = report.get(key)
                if type(count) is int:
                    totals[key] += count
                else:
                    unknown = True
    return {**totals, "incomplete": unknown}
