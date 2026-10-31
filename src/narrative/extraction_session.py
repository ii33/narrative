"""One durable model conversation for a project's extraction and review work."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .harness import effective_runtime_config
from .leases import ProjectLease, is_capacity_finish
from .project_package import layout


class IncompleteExtraction(ValueError):
    """The provider did not complete a turn; never repair or retry automatically."""


@contextmanager
def _review_lease(directory: Path):
    for attempt in range(101):
        try:
            lease = ProjectLease(directory / "review-actions.lock")
            break
        except OSError:
            if attempt == 100:
                raise ValueError("审核记录正被其他操作写入，请稍后再试") from None
            time.sleep(0.01)
    try:
        yield
    finally:
        lease.close()


def session_directory(ops, workdir: Path | None = None) -> Path:
    if ops is not None:
        return layout(ops.project_info().path).extraction / "conversation"
    if workdir is None:
        raise ValueError("Standalone extraction requires a working directory")
    return Path(workdir) / "conversation"


def _save(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def record_review_action(ops, action: dict) -> None:
    """Queue a business/human review outcome for the next turn of this same session."""
    directory = session_directory(ops)
    directory.mkdir(parents=True, exist_ok=True)
    with _review_lease(directory):
        event = {"at": datetime.now(UTC).isoformat(), "action": action}
        with (directory / "review-actions.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")


class _ConversationRunner:
    def __init__(self, runner, directory, manifest, lease):
        self.runner, self.directory, self.manifest, self.lease = runner, directory, manifest, lease
        self.closed = False

    def agent_turn(self, instruction, session):
        if session != self.manifest["session_id"]:
            raise ValueError("提取和审核必须使用同一会话")
        events = self.directory / "review-actions.jsonl"
        with _review_lease(self.directory):
            lines = events.read_text(encoding="utf-8").splitlines() if events.exists() else []
        applied = self.manifest.get("review_actions_applied", 0)
        if len(lines) > applied:
            instruction = (
                "以下为同一提取会话中已完成的业务/人工审核结果。它们是审核记录，不是新原文：\n"
                + "\n".join(lines[applied:]) + "\n\n" + instruction
            )
        turn = self.directory / "turns" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + "-" + uuid4().hex[:8])
        turn.mkdir(parents=True)
        (turn / "prompt.txt").write_text(instruction, encoding="utf-8")
        request = {"session_id": session, "prompt_sha256": hashlib.sha256(instruction.encode()).hexdigest()}
        try:
            reply = self.runner.agent_turn(instruction, session)
        except Exception as exc:
            metadata = self.runner.usage_metadata() if hasattr(self.runner, "usage_metadata") else {}
            metadata.update(request, error_type=type(exc).__name__, status="failed")
            _save(turn / "metadata.json", metadata)
            raw = self.runner.partial_raw() if hasattr(self.runner, "partial_raw") else []
            _save(turn / "partial-raw.json", {"responses": raw})
            raise
        (turn / "raw.txt").write_text(reply.raw, encoding="utf-8")
        reply.metadata["session_id"] = session
        _save(turn / "metadata.json", reply.metadata | request)
        self.manifest["turn_count"] += 1
        self.manifest["review_actions_applied"] = len(lines)
        _save(self.directory / "manifest.json", self.manifest)
        reason = reply.metadata.get("finish_reason")
        if is_capacity_finish(reason) or reason in {"error", "cancelled", "timeout", "interrupted"}:
            raise IncompleteExtraction(f"模型提取/审核未完成（{reason}），已保存原始响应，不自动重试")
        return reply

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.runner.close()
            finally:
                self.lease.close()


def open_extraction_session(ops, settings, api_key: str, workdir: Path, runner_factory):
    directory = session_directory(ops, workdir)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        lease = ProjectLease(directory / "session.lock")
    except OSError as exc:
        raise ValueError("同一工程的提取/审核会话正在使用，请等待当前轮次结束") from exc
    runner = None
    try:
        identity = {key: getattr(settings, key) for key in ("provider", "model", "base_url")}
        project_id = ops.graph_project_info()["project_id"] if ops is not None else None
        manifest_path = directory / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("project_id") != project_id:
                raise ValueError("提取会话属于另一工程，已停止；不会混用工程上下文")
            if manifest["model_identity"] != identity:
                raise ValueError("提取会话的供应商/模型/地址已改变；请恢复原配置以继续保留上下文")
        else:
            manifest = {"version": 1, "session_id": "extraction-" + uuid4().hex,
                        "project_id": project_id,
                        "model_identity": identity, "turn_count": 0, "review_actions_applied": 0}
        policy = effective_runtime_config(settings)
        if manifest.get("context_policy") != policy:
            manifest.setdefault("context_policy_history", []).append(
                {"at": datetime.now(UTC).isoformat(), "policy": policy}
            )
        manifest["context_policy"] = policy
        _save(manifest_path, manifest)
        runner = runner_factory(settings, api_key, directory / "sdk")
        restored = runner.resume(manifest["session_id"])
        if manifest["turn_count"] and not restored:
            raise ValueError("无法恢复既有提取会话，已停止；不会静默丢弃上下文另开会话")
        return _ConversationRunner(runner, directory, manifest, lease), manifest["session_id"]
    except Exception:
        if runner is not None:
            runner.close()
        lease.close()
        raise
