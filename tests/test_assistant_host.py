"""Actual local host capabilities are task-bound; no model/service requests."""
import base64
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from narrative.assistant_host import (
    _COMMANDS,
    _StreamRedactor,
    cancel_project_host_commands,
    finish_host_commands,
    host_access_context,
)
from narrative.operations import Operations
from narrative.project_package import layout


@pytest.fixture
def ops(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    entry = tmp_path / 'project' / 'host.nproj'
    entry.parent.mkdir()
    result = Operations.create_graph_project(entry)
    yield result
    cancel_project_host_commands(entry)


def access(ops, task='task-one', mode='full', event=None, secrets=()):
    return host_access_context(mode, task, event or threading.Event(), ops.project_info().path, secrets_to_redact=secrets)


def command(code):
    args = [sys.executable, '-u', '-c', code]
    return subprocess.list2cmdline(args) if os.name == 'nt' else ' '.join(__import__('shlex').quote(arg) for arg in args)


def wait(ops, identifier, timeout=8):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        result = ops.read_host_command(identifier)
        if result['status'] != 'running':
            return result
        time.sleep(.02)
    pytest.fail('local test command did not settle')


def test_standard_and_unbound_cannot_forge_full_tools(ops, tmp_path):
    target = tmp_path / 'external.txt'
    for context in [None, access(ops, mode='standard')]:
        if context:
            context.__enter__()
        try:
            with pytest.raises(ValueError, match='完全访问'):
                ops.write_host_file(str(target), 'should not write')
            with pytest.raises(ValueError, match='完全访问'):
                ops.run_host_command('echo should-not-run', str(tmp_path))
        finally:
            if context:
                context.__exit__(None, None, None)
    assert not target.exists()


def test_external_file_text_character_pages_binary_and_secret_redaction(ops, tmp_path, monkeypatch):
    monkeypatch.setenv('HOST_QA_KEY', 'external-secret-value')
    outside = tmp_path / 'outside'
    outside.mkdir()
    path = outside / '汉字.txt'
    with access(ops):
        ops.write_host_file(str(path), '甲😀乙\r\nexternal-secret-value')
        page = ops.read_host_file(str(path), 1, 2)
        assert page['content'] == '😀乙' and page['next_offset'] == 3 and page['offset_unit'] == 'characters'
        assert ops.read_host_file(str(path))['content'].endswith('[REDACTED]')
        binary = outside / 'binary.bin'
        ops.write_host_file(str(binary), base64.b64encode(b'\x00\xffabc').decode(), encoding='base64')
        assert base64.b64decode(ops.read_host_file(str(binary), 0, 2, 'base64')['content']) == b'\x00\xff'
        assert ops.read_host_file(str(binary), 0, 2, 'base64')['offset_unit'] == 'bytes'
        assert len(ops.list_host_directory(str(outside), limit=1)['entries']) == 1
        with pytest.raises(ValueError):
            ops.write_host_file(str(path), 'not base64!', encoding='base64')
        assert path.read_text(encoding='utf-8').startswith('甲😀乙')
    audit = layout(ops.project_info().path).extraction / 'assistant' / 'tasks' / 'task-one' / 'host'
    assert 'external-secret-value' not in '\n'.join(file.read_text(encoding='utf-8') for file in audit.glob('*.json'))


def test_real_async_command_is_paged_redacted_and_restores_terminal_audit(ops, tmp_path, monkeypatch):
    monkeypatch.setenv('HOST_QA_TOKEN', 'qa-token-secret-long')
    with access(ops):
        result = ops.run_host_command(command("import os; print(os.environ['HOST_QA_TOKEN']); print('x'*30000)"), str(tmp_path))
        identifier = result['command_id']
        assert identifier.startswith('command-')
        finished = wait(ops, identifier)
        assert finished['exit_code'] == 0 and finished['status'] == 'completed'
        page = ops.read_host_command(identifier, 0, 32)
        assert len(page['content']) == 32 and not page['eof'] and '[REDACTED]' in page['content']
        output = Path(finished['output_path']).read_text(encoding='utf-8')
        assert len(output) > 30000 and 'qa-token-secret-long' not in output
        # Simulates a later CLI process with no in-memory registry.
        saved = dict(_COMMANDS)
        _COMMANDS.clear()
        try:
            assert ops.read_host_command(identifier, 0, 32)['content'] == page['content']
        finally:
            _COMMANDS.update(saved)
    with access(ops, task='other-task'):
        with pytest.raises(ValueError, match='不属于'):
            ops.read_host_command(identifier)
    with access(ops, mode='standard'):
        with pytest.raises(ValueError, match='完全访问'):
            ops.read_host_command(identifier)


def test_cancel_only_owned_tree_and_late_stop_cannot_touch_next_task(ops, tmp_path):
    cancelled = threading.Event()
    child_file = tmp_path / 'child.pid'
    code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"
    with access(ops, event=cancelled):
        first = ops.run_host_command(command(code), str(tmp_path))['command_id']
        end = time.monotonic()+5
        while time.monotonic() < end:
            content = ops.read_host_command(first)['content'].strip()
            if content:
                child_file.write_text(content)
                break
            time.sleep(.02)
        assert child_file.exists()
        cancelled.set()
    finish_host_commands(ops.project_info().path, 'task-one', completed=False)
    with access(ops, task='task-two'):
        second = ops.run_host_command(command('print("new-task")'), str(tmp_path))['command_id']
        with pytest.raises(ValueError, match='不属于'):
            ops.stop_host_command(first)
        assert wait(ops, second)['status'] == 'completed'
    with access(ops):
        assert ops.read_host_command(first)['status'] in ('cancelled', 'stopped')
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, int(child_file.read_text()))
        if handle:
            try:
                assert ctypes.windll.kernel32.WaitForSingleObject(handle, 3000) == 0
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)


def test_background_explicit_transfer_survives_late_old_cancel_and_is_project_scoped(ops, tmp_path):
    event = threading.Event()
    with access(ops, event=event):
        identifier = ops.run_host_command(command('import time; print("service",flush=True); time.sleep(60)'), str(tmp_path), background=True)['command_id']
    finish_host_commands(ops.project_info().path, 'task-one', completed=True)
    event.set()
    time.sleep(.1)
    with access(ops, task='later-task'):
        assert ops.read_host_command(identifier)['status'] == 'running'
        assert ops.stop_host_command(identifier)['status'] == 'stopped'
    with access(ops, mode='standard'):
        with pytest.raises(ValueError, match='完全访问'):
            ops.stop_host_command(identifier)


def test_stream_redactor_covers_secrets_split_between_output_chunks():
    secret = 'a-secret-long-token'
    redactor = _StreamRedactor((secret,))
    output = redactor.feed('before a-secret-') + redactor.feed('long-token after') + redactor.feed('', final=True)
    assert output == 'before [REDACTED] after'

def test_binary_and_text_secret_spanning_page_boundaries_never_leak(ops, tmp_path):
    secret = 'page-boundary-key-secret'
    raw = b'prefix-'+secret.encode()+b'-suffix'
    path = tmp_path / 'key.bin'
    path.write_bytes(raw)
    with access(ops, secrets=(secret,)):
        for offset, count in [(8, 3), (0, 10), (15, 3)]:
            binary = ops.read_host_file(str(path), offset, count, 'base64')
            text = ops.read_host_file(str(path), offset, count)
            assert binary['redacted'] and text['redacted']
            assert b'[REDACTED]' in base64.b64decode(binary['content'])
            assert '[REDACTED]' in text['content']
            assert binary['next_offset'] == offset+count
        original = base64.b64encode(raw).decode()
        written = tmp_path / 'written.bin'
        ops.write_host_file(str(written), original, encoding='base64')
        assert written.read_bytes() == raw
        with pytest.raises(ValueError) as error:
            ops.run_host_command('echo safe', str(tmp_path), encoding=secret)
        assert secret not in str(error.value)
    audit = layout(ops.project_info().path).extraction / 'assistant' / 'tasks' / 'task-one' / 'host'
    records = '\n'.join(file.read_text(encoding='utf-8') for file in audit.glob('*.json'))
    assert secret not in records and original not in records


def test_full_context_cannot_be_reused_for_another_project_or_cancelled_task(ops, tmp_path):
    other = Operations.create_graph_project(tmp_path / 'other.nproj')
    with access(ops):
        with pytest.raises(ValueError, match='不属于当前工程'):
            other.list_host_directory(str(tmp_path))
    event = threading.Event()
    event.set()
    with access(ops, event=event):
        with pytest.raises(ValueError, match='已停止'):
            ops.write_host_file(str(tmp_path / 'not-created.txt'), 'x')
    assert not (tmp_path / 'not-created.txt').exists()

def test_registered_tools_are_host_scoped_and_direct_calls_still_require_mode(ops):
    from narrative import assistant_host_tools
    from narrative.tool_api import Tool, ToolError, _validate_adaptation_argument
    registered = []
    assistant_host_tools.register(Tool, registered.append, _validate_adaptation_argument)
    assert len(registered) == 6 and all(tool.scope == 'host' for tool in registered)
    for tool in registered:
        with pytest.raises((ToolError, ValueError)):
            tool.handler(ops, {})

def test_terminal_registry_is_evicted_and_finished_background_still_transfers(ops, tmp_path):
    with access(ops):
        identifier = ops.run_host_command(command('print("already done")'), str(tmp_path), background=True)['command_id']
        wait(ops, identifier)
    end = time.monotonic()+2
    while time.monotonic() < end and any(key[1] == identifier for key in _COMMANDS):
        time.sleep(.01)
    assert not any(key[1] == identifier for key in _COMMANDS)
    receipt = finish_host_commands(ops.project_info().path, 'task-one', completed=True)
    assert len(receipt['commands']) == 1 and receipt['commands'][0]['detached']
    with access(ops, task='later-task'):
        assert ops.read_host_command(identifier)['status'] == 'completed'


def test_project_cleanup_does_not_stop_other_project_commands(ops, tmp_path):
    other = Operations.create_graph_project(tmp_path / 'other-host.nproj')
    try:
        with access(ops):
            own = ops.run_host_command(command('import time; time.sleep(60)'), str(tmp_path), background=True)['command_id']
        finish_host_commands(ops.project_info().path, 'task-one', completed=True)
        with access(other):
            foreign = other.run_host_command(command('import time; time.sleep(60)'), str(tmp_path), background=True)['command_id']
        finish_host_commands(other.project_info().path, 'task-one', completed=True)
        cancel_project_host_commands(ops.project_info().path)
        with access(ops):
            assert ops.read_host_command(own)['status'] == 'stopped'
        with access(other):
            assert other.read_host_command(foreign)['status'] == 'running'
    finally:
        cancel_project_host_commands(other.project_info().path)

def test_secret_in_command_line_and_utf16_binary_is_not_audited(ops, tmp_path):
    secret = 'qa-direct-command-secret'
    with access(ops, secrets=(secret,)):
        identifier = ops.run_host_command(command('print('+repr(secret)+')'), str(tmp_path))['command_id']
        assert wait(ops, identifier)['content'].strip() == '[REDACTED]'
        binary = tmp_path / 'utf16.bin'
        binary.write_bytes(('before '+secret+' after').encode('utf-16-le'))
        page = ops.read_host_file(str(binary), encoding='base64')
        assert page['redacted'] and secret.encode('utf-16-le') not in base64.b64decode(page['content'])
    root = layout(ops.project_info().path).extraction / 'assistant' / 'tasks' / 'task-one' / 'host'
    assert all(secret not in file.read_text(encoding='utf-8') for file in root.iterdir() if file.is_file())

def test_completed_task_records_unfinished_foreground_cleanup_without_fake_success(ops, tmp_path):
    from narrative.assistant_host import read_host_task_cleanup
    with access(ops):
        identifier = ops.run_host_command(command('import time; print("started",flush=True); time.sleep(60)'), str(tmp_path))['command_id']
    cleanup = finish_host_commands(ops.project_info().path, 'task-one', completed=True)
    assert len(cleanup['cleanup_receipts']) == 1
    receipt = cleanup['cleanup_receipts'][0]
    assert receipt['command_id'] == identifier and receipt['reason'] == 'task_completed_before_command_finished'
    assert receipt['status'] == 'stopped' and not receipt['background']
    assert cleanup['diagnostics'][0]['code'] == 'host_command_stopped_at_task_end'
    durable = read_host_task_cleanup(ops.project_info().path, 'task-one')
    assert durable == cleanup
    repeated = finish_host_commands(ops.project_info().path, 'task-one', completed=False)
    assert repeated['cleanup_receipts'] == cleanup['cleanup_receipts']
    assert repeated['diagnostics'] == cleanup['diagnostics']


def test_natural_completion_and_explicit_background_transfer_have_no_cleanup_warning(ops, tmp_path):
    with access(ops):
        done = ops.run_host_command(command('print("done")'), str(tmp_path))['command_id']
        assert wait(ops, done)['status'] == 'completed'
        background = ops.run_host_command(command('import time; time.sleep(60)'), str(tmp_path), background=True)['command_id']
    cleanup = finish_host_commands(ops.project_info().path, 'task-one', completed=True)
    assert cleanup['cleanup_receipts'] == [] and cleanup['diagnostics'] == []
    with access(ops, task='later-task'):
        assert ops.read_host_command(background)['status'] == 'running'
        ops.stop_host_command(background)
