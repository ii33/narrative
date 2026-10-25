"""Graph analysis run control: start, inspect, pause, instruct, cancel.

Runs are persisted under ``<project>_analysis_runs``; this module only shapes
the public view and forwards to ``graph_agent`` / ``analysis_control``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .graph_store import GraphStore


class AnalysisService:
    def __init__(self, ops: Any, store: GraphStore) -> None:
        self.ops = ops
        self.store = store

    def run_dir(self) -> Path:
        return self.store.path.with_name(self.store.path.stem + "_analysis_runs")

    def start(
        self,
        source_ids: list[str],
        *,
        task: str | None = None,
        feedback: str | None = None,
        feedback_origin: str = "user_input",
        seed_candidate_id: str | None = None,
        budget: dict | None = None,
        api_key: str | None = None,
    ) -> str:
        from . import graph_agent

        return graph_agent.start(
            self.ops, source_ids, task=task, feedback=feedback,
            feedback_origin=feedback_origin, seed_candidate_id=seed_candidate_id,
            budget=budget, api_key=api_key,
        )

    def status(self, run_id: str) -> dict:
        from . import graph_agent

        return graph_agent.status(self.ops, run_id)

    def runs(self) -> list[dict]:
        """List persisted graph runs without exposing journals or raw model output."""
        root = self.run_dir()
        if not root.is_dir():
            return []
        rows = []
        for path in root.iterdir():
            if not path.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", path.name):
                continue
            try:
                state = self.status(path.name)
            except (ValueError, OSError):
                continue
            rows.append({
                "run_id": path.name, "status": state.get("status"),
                "phase": state.get("phase"), "request_state": state.get("request_state"),
                "source_ids": state.get("source_ids", []),
                "candidate_ids": state.get("candidate_ids", []),
                "provider": state.get("provider"), "model": state.get("model"),
                "rounds": state.get("rounds", 0), "observer_state": state.get("observer_state"),
                "modified_at": path.stat().st_mtime,
            })
        return sorted(rows, key=lambda item: item["modified_at"], reverse=True)

    def trace(self, run_id: str) -> dict:
        from . import graph_agent

        return graph_agent.trace(self.ops, run_id)

    def response(self, run_id: str, round_number: int) -> dict:
        from . import graph_agent

        return graph_agent.response(self.ops, run_id, round_number)

    def control(self, run_id: str) -> dict:
        from . import analysis_control

        return analysis_control.read(self.ops, run_id)

    def take_instructions(self, run_id: str) -> list[dict]:
        from . import analysis_control

        return analysis_control.take_instructions(self.ops, run_id)

    def pause(self, run_id: str) -> None:
        from . import analysis_control

        analysis_control.set_paused(self.ops, run_id, True)

    def resume(self, run_id: str) -> None:
        from . import analysis_control

        analysis_control.set_paused(self.ops, run_id, False)

    def instruct(self, run_id: str, text: str) -> str:
        from . import analysis_control

        return analysis_control.send_instruction(self.ops, run_id, text)["id"]

    def cancel(self, run_id: str) -> None:
        from . import analysis_control

        analysis_control.mark_cancel_requested(self.ops, run_id)
