"""One bounded background extraction; durable state belongs to the project DB."""

import json
import os
import threading
from pathlib import Path

from . import extraction
from .checks import digest
from .harness import HarnessRunner, HarnessSettings, resolve_key
from .service import Service
from .store import Store

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
            # Windows byte locks also reject reads through another open handle.
            # Inspect metadata only, and initialize the byte after acquiring it.
            if os.fstat(self.file.fileno()).st_size == 0:
                self.file.write(b"0")
                self.file.flush()
        except OSError:
            self.file.close()
            raise ValueError("此项目已有模型提取正在执行") from None

    def close(self):
        self.file.close()


def recover(store: Store):
    try:
        lease = ProjectLease(Path(str(store.path) + ".dsh.lock"))
    except ValueError:
        return
    try:
        with store.connect(write=True) as db:
            db.execute("UPDATE runs SET status='interrupted' WHERE status IN ('queued','running')")
            db.execute(
                "UPDATE chunks SET status='interrupted',error='上次进程已中断；不会自动重发模型请求' WHERE status='running'"
            )
    finally:
        lease.close()


class Job:
    def __init__(
        self, store: Store, run_id: str, settings: HarnessSettings, key: str, lease: ProjectLease
    ):
        self.store, self.run_id, self.settings = store, run_id, settings
        self.key, self.lease = key, lease
        self.cancelled = threading.Event()
        self.runner: HarnessRunner | None = None
        self.thread = threading.Thread(
            target=self.execute, daemon=True, name="narrative-extraction"
        )

    def stop(self):
        self.cancelled.set()
        with self.store.connect(write=True) as db:
            db.execute(
                "UPDATE runs SET status='cancelled' WHERE id=? AND status IN ('queued','running')",
                (self.run_id,),
            )
        if self.runner:
            self.runner.close()

    def execute(self):
        chunk_id = None
        try:
            service = Service(self.store)
            pack = extraction.export_run(service, self.run_id)
            with self.store.connect(write=True) as db:
                db.execute(
                    "UPDATE runs SET status='running' WHERE id=? AND status='queued'",
                    (self.run_id,),
                )
            if self.cancelled.is_set():
                return
            self.runner = HarnessRunner(
                self.settings, self.key, self.store.path.parent / "harness" / self.run_id
            )
            self.key = ""
            for index, chunk in enumerate(pack["chunks"]):
                if self.cancelled.is_set():
                    break
                if chunk["status"] == "processed":
                    continue
                chunk_id = chunk["chunk_id"]
                with self.store.connect(write=True) as db:
                    db.execute(
                        "UPDATE chunks SET status='running',error=NULL WHERE id=?", (chunk_id,)
                    )
                payload = {
                    "existing_entities": pack["existing_entities"],
                    "existing_assertions": pack.get("existing_assertions", []),
                    "existing_events": pack.get("existing_events", []),
                    "existing_records": pack.get("existing_records", []),
                    "result_schema": pack.get("chunk_result_schemas", {}).get(
                        chunk_id, pack["result_schema"]
                    ),
                    "chunk": chunk,
                }
                reply = self.runner.extract(
                    pack["instruction"], payload, f"extract-{self.run_id}-{index}"
                )
                metadata = {
                    **reply.metadata,
                    "input_hash": digest(json.dumps(payload, ensure_ascii=False)),
                    "prompt_version": pack["prompt_version"],
                }
                # Save returned usage even when content is empty, rejected, or cancelled.
                # This is one record per chunk, not a new accounting entry per import.
                with self.store.connect(write=True) as db:
                    db.execute(
                        "UPDATE chunks SET raw=?,metadata=? WHERE id=?",
                        (reply.raw, json.dumps(metadata), chunk_id),
                    )
                if self.cancelled.is_set():
                    break
                if reply.metadata["finish_reason"] != "completed":
                    reason = reply.metadata["finish_reason"]
                    raise ValueError(
                        (
                            f"模型输出因容量限制截断（finish_reason={reason}）；结果未导入"
                            if is_capacity_finish(reason)
                            else f"模型未完整结束：{reason}；结果未导入"
                        )
                        + (
                            f"；{reply.metadata['service_error']}"
                            if reply.metadata.get("service_error")
                            else ""
                        )
                    )
                extraction.ingest_result(
                    service, chunk_id, reply.raw, metadata=metadata, active_run_only=True
                )
            if not self.cancelled.is_set():
                with self.store.connect(write=True) as db:
                    db.execute(
                        "UPDATE runs SET status='complete' WHERE id=? AND status='running'",
                        (self.run_id,),
                    )
        except Exception as exc:
            # Runtime diagnostics may contain headers. Never persist the supplied key.
            message = str(exc)
            if self.runner:
                message = self.runner.redact(message)
            elif self.key:
                message = message.replace(self.key, "[REDACTED]")
            message = message[:2000]
            with self.store.connect(write=True) as db:
                if chunk_id and self.runner:
                    usage_metadata = self.runner.usage_metadata()
                    if usage_metadata["usage"] is not None:
                        row = db.execute(
                            "SELECT metadata FROM chunks WHERE id=?", (chunk_id,)
                        ).fetchone()
                        metadata = json.loads(row[0] or "{}")
                        metadata.update(usage_metadata)
                        db.execute(
                            "UPDATE chunks SET metadata=? WHERE id=?",
                            (json.dumps(metadata), chunk_id),
                        )
                if chunk_id and not self.cancelled.is_set():
                    db.execute(
                        "UPDATE chunks SET status='failed',error=? WHERE id=? AND status!='processed'",
                        (message, chunk_id),
                    )
                row = db.execute("SELECT data FROM runs WHERE id=?", (self.run_id,)).fetchone()
                data = json.loads(row[0])
                data["error"] = message
                db.execute(
                    "UPDATE runs SET status=CASE WHEN status='cancelled' THEN status ELSE 'failed' END,data=? WHERE id=?",
                    (json.dumps(data, ensure_ascii=False), self.run_id),
                )
        finally:
            self.key = ""
            try:
                if self.runner:
                    self.runner.close()
            finally:
                try:
                    with self.store.connect(write=True) as db:
                        db.execute(
                            "UPDATE chunks SET status='cancelled',error='提取已停止，未导入结果' WHERE run_id=? AND status='running'",
                            (self.run_id,),
                        )
                finally:
                    self.lease.close()


_lock = threading.Lock()
_jobs: dict[str, Job] = {}


def start(store: Store, run_id: str, settings: HarnessSettings, api_key: str | None):
    key = resolve_key(settings, api_key)
    with _lock:
        identity = str(store.path) + ":" + run_id
        existing = _jobs.get(identity)
        if existing and existing.thread.is_alive():
            return
        if any(job.thread.is_alive() for job in _jobs.values()):
            raise ValueError("已有模型提取正在执行，请完成或停止后再启动")
        lease = ProjectLease(Path(str(store.path) + ".dsh.lock"))
        try:
            with store.connect(write=True) as db:
                row = db.execute("SELECT status,data FROM runs WHERE id=?", (run_id,)).fetchone()
                if not row:
                    raise ValueError("任务不存在")
                if row["status"] != "awaiting_results":
                    raise ValueError("此任务已启动或已有结果；如需重试请新建任务，避免重复计费")
                data = json.loads(row["data"])
                data.update(
                    integration="deepseek-harness",
                    model=settings.model,
                    settings=settings.model_dump(),
                    error=None,
                )
                db.execute(
                    "UPDATE runs SET status='queued',data=? WHERE id=?",
                    (json.dumps(data, ensure_ascii=False), run_id),
                )
            job = Job(store, run_id, settings, key, lease)
            _jobs.clear()
            _jobs[identity] = job
            job.thread.start()
        except Exception:
            lease.close()
            raise


def cancel(store: Store, run_id: str):
    with _lock:
        job = _jobs.get(str(store.path) + ":" + run_id)
    if job and job.thread.is_alive():
        job.stop()
    else:
        raise ValueError("此工作台进程中没有该活动任务；刷新状态后重试")
