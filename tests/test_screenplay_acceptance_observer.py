"""External QA observation stays independent of a busy Qt main thread."""
import runpy
import threading
from pathlib import Path


def test_external_usage_watchdog_stops_without_main_thread_polling():
    module = runpy.run_path(str(Path(__file__).parents[1] / "scripts/accept_screenplay.py"), run_name="acceptance_observer_test")
    stopped = threading.Event()
    watcher = module["ExternalBudgetWatchdog"](lambda: 101, stopped.set, 10, 100).start()
    try:
        assert stopped.wait(2)
        assert watcher.reason == "usage_budget" and watcher.observed_tokens == 101
    finally:
        watcher.close()


def test_external_observer_error_is_not_silently_ignored():
    module = runpy.run_path(str(Path(__file__).parents[1] / "scripts/accept_screenplay.py"), run_name="acceptance_observer_test")
    stopped = threading.Event()
    def unavailable():
        raise RuntimeError("usage unavailable")
    watcher = module["ExternalBudgetWatchdog"](unavailable, stopped.set, 10, 100).start()
    try:
        assert stopped.wait(2)
        assert watcher.reason == "observer_error" and "unavailable" in watcher.observer_error
    finally:
        watcher.close()


def test_usage_is_cumulative_across_sdk_argument_repairs_and_deduplicates_archives(tmp_path):
    import json

    module = runpy.run_path(str(Path(__file__).parents[1] / "scripts/accept_screenplay.py"), run_name="acceptance_observer_test")
    first = {"complete": True, "raw": "provider-response-one", "usage": {"total_tokens": 60}}
    second = {"complete": True, "raw": "provider-response-two", "usage": {"total_tokens": 40}}
    current = {"model_requests": [first]}
    observer = module["TaskUsageObserver"](lambda: current, tmp_path)
    assert observer.total() == 60
    task = tmp_path / "task"
    task.mkdir()
    (task / "metadata-1.json").write_text(json.dumps(current), encoding="utf-8")
    assert observer.total() == 60
    current = {"model_requests": [second]}
    assert observer.total() == 100
    (task / "metadata-2.json").write_text(json.dumps(current), encoding="utf-8")
    assert observer.total() == 100
