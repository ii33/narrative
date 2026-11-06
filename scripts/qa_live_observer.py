"""Observation-only QA hooks; never alter model requests, responses or tools."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime


class LiveObserver:
    def __init__(self):
        self.lock = threading.RLock()
        self.started_at = None
        self.last_progress = 0.0
        self.tools = []
        self.requests = []
        self.active = {}
        self.first_accepted = None
        self.directory = None

    def _elapsed(self):
        return round(time.monotonic() - self.started_at, 3) if self.started_at else None

    def emit(self, event, **fields):
        print(json.dumps({"event": event, "elapsed_seconds": self._elapsed(), **fields}, ensure_ascii=False), flush=True)

    def started(self, ops, fixture, report, directory, began):
        with self.lock:
            self.started_at, self.directory = began, directory
            self.emit("task_started", source_chars=fixture.get("source_chars"), target_chars=fixture.get("target_chars"))

    def tool_finished(self, step):
        with self.lock:
            row = deepcopy(step)
            row["qa_completed_elapsed_seconds"] = self._elapsed()
            self.tools.append(row)
            result = step.get("result") or {}
            if step["tool"] == "submit_extraction":
                accepted = step.get("ok") and result.get("status") == "accepted"
                if accepted and self.first_accepted is None:
                    self.first_accepted = {"elapsed_seconds": self._elapsed(), "duration_ms": step.get("duration_ms"),
                        "revision": result.get("revision"), "node_count": result.get("node_count"), "property_count": result.get("property_count")}
                self.emit("extraction_submission", accepted=bool(accepted), status=result.get("status"),
                    duration_ms=step.get("duration_ms"), node_count=result.get("node_count"),
                    property_count=result.get("property_count"), diagnostics=result.get("diagnostics", []),
                    first_accepted=self.first_accepted)
            self._save()

    def request_started(self, record):
        with self.lock:
            row = {"index": len(self.requests) + 1, "purpose": record.get("purpose"),
                "started_at": datetime.now(UTC).isoformat(), "started_elapsed_seconds": self._elapsed(),
                "completed": False, "duration_ms": None, "_start": time.monotonic(), "_record": record}
            self.requests.append(row)
            self.active[threading.get_ident()] = row

    def stream_completed(self):
        with self.lock:
            row = self.active.get(threading.get_ident())
            if row is not None and not row["completed"]:
                row.update(completed=True, completed_at=datetime.now(UTC).isoformat(),
                    duration_ms=round((time.monotonic() - row["_start"]) * 1000, 3))

    def snapshot(self):
        with self.lock:
            requests = []
            for row in self.requests:
                value = {key: deepcopy(item) for key, item in row.items() if not key.startswith("_")}
                value["usage"] = deepcopy(row["_record"].get("usage"))
                value["finish_reason"] = row["_record"].get("finish_reason")
                requests.append(value)
            return {"first_accepted_submission": deepcopy(self.first_accepted), "tool_calls": deepcopy(self.tools),
                "provider_requests": requests,
                "request_timing_boundary": "Gateway registers the genuine request before TCP connect; end is the first observed upstream SSE [DONE]. Includes connection/network/model streaming, excludes subsequent local business tool callbacks. Incomplete requests have no invented end time."}

    def _save(self):
        if self.directory is not None:
            path = self.directory / "live-observation.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)

    def progress(self, ops, fixture, report, directory, began, observed):
        now = time.monotonic()
        if now - self.last_progress < 30:
            return
        with self.lock:
            self.last_progress = now
            state = self.snapshot()
            self.emit("progress", completed_provider_requests=sum(row["completed"] for row in state["provider_requests"]),
                tool_calls=len(self.tools), locate_calls=sum(row["tool"] == "locate_source" for row in self.tools),
                preview_calls=sum(row["tool"] == "preview_extraction" for row in self.tools),
                observed_tokens=observed, first_accepted=self.first_accepted)
            self._save()


@contextmanager
def observe_real_calls(observer):
    """Instrument timing only, while delegating every call to original code."""
    from narrative import assistant, harness, model_gateway

    original_call, original_gateway, original_feed = assistant._call, harness.ModelGateway, model_gateway.UsageObserver.feed

    class RequestList(list):
        def append(self, record):
            observer.request_started(record)
            super().append(record)

    class ObservedGateway(original_gateway):
        def __setattr__(self, key, value):
            if key == "_usage_records" and isinstance(value, list):
                value = RequestList(value)
            super().__setattr__(key, value)

    def call(*args, **kwargs):
        step = original_call(*args, **kwargs)
        observer.tool_finished(step)
        return step

    def feed(self, chunk):
        original_feed(self, chunk)
        if self.complete:
            observer.stream_completed()

    assistant._call, harness.ModelGateway, model_gateway.UsageObserver.feed = call, ObservedGateway, feed
    try:
        yield observer
    finally:
        assistant._call, harness.ModelGateway, model_gateway.UsageObserver.feed = original_call, original_gateway, original_feed
        with observer.lock:
            observer._save()
