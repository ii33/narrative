"""Local deterministic completion/stop ordering; no provider requests."""
import threading
from contextlib import nullcontext

import pytest

from narrative.conversation_runtime import ProjectConversation
from narrative.harness import HarnessReply, HarnessRunner
from narrative.task_tool_host import TaskToolHost


def test_completed_sdk_call_releases_control_before_waiting_for_stop_barrier():
    entered, release, aborted = threading.Event(), threading.Event(), threading.Event()
    runner = HarnessRunner.__new__(HarnessRunner)
    class Gateway:
        def session(self, session):
            return nullcontext()
    runner._gateway = Gateway()
    runner.active_session = None
    runner.turn_settled = True
    def settled_run(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return HarnessReply('completed before stop settled', {'finish_reason': 'completed'})
    runner._run = settled_run
    runner.interrupt_upstream = aborted.set
    runtime = ProjectConversation.__new__(ProjectConversation)
    runtime.closed = False
    runtime._lock = threading.RLock()
    runtime._interrupt_done = threading.Event()
    runtime._interrupt_done.set()
    runner.interrupt_barrier = runtime._interrupt_done
    binding = {'nonce': 'current-task', 'session_id': 'current-chat', 'cancelled': False,
               'interrupt_sent': False, 'cancel_event': threading.Event()}
    runtime._binding, runtime.runner = binding, runner
    runtime.host = TaskToolHost([], lambda *args: None)
    outcome = {}
    def run():
        try:
            outcome['reply'] = runner.agent_turn('offline', 'current-chat')
        except Exception as exc:
            outcome['run_error'] = exc
    def stop():
        try:
            runtime.interrupt('current-task')
        except Exception as exc:
            outcome['stop_error'] = exc
    worker, stopper = threading.Thread(target=run), threading.Thread(target=stop)
    try:
        worker.start()
        assert entered.wait(1)
        stopper.start()
        queued = runtime.host._controls.get(timeout=1)
        assert queued['kind'] == 'interrupt'
        release.set()
        stopper.join(1)
        worker.join(1)
        assert not worker.is_alive() and not stopper.is_alive(), 'SDK completion and local controller must not wait on one another'
        assert 'run_error' not in outcome and 'stop_error' not in outcome
        assert outcome['reply'].metadata['finish_reason'] == 'completed'
        assert runner.active_session is None and runner.turn_settled and aborted.is_set()
        assert runtime._binding is binding and binding['cancelled']
        with pytest.raises(ValueError, match='任务'):
            runtime.begin_turn('current-task', 'current-chat')
        replacement = dict(binding, nonce='new-task', cancelled=False, interrupt_sent=False)
        runtime._binding = replacement
        runtime.interrupt('current-task')
        assert not replacement['cancelled']
    finally:
        release.set()
        runtime._interrupt_done.set()
        worker.join(2)
        if stopper.ident is not None:
            stopper.join(2)
        runtime.host.close()


def test_admitted_turn_is_armed_even_before_runner_enters_sdk():
    runtime = ProjectConversation.__new__(ProjectConversation)
    runtime.closed = False
    runtime._lock = threading.RLock()
    runtime._binding = {'nonce': 'task', 'session_id': 'chat', 'cancelled': False}
    class Runner:
        active_session = None
        turn_settled = True
    runtime.runner = Runner()
    runtime.begin_turn('task', 'chat')
    assert runtime.runner.active_session == 'chat' and runtime.runner.turn_settled is False
