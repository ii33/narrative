"""Host capabilities authorized only by an immutable, program-bound full task."""
from __future__ import annotations

import base64
import codecs
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

from .graph_store import GraphStore
from .project_package import layout
from .readable_ids import allocate_id

MAX_PAGE = 65536
HOST_TOOL_NAMES = frozenset({'read_host_file', 'write_host_file', 'list_host_directory', 'run_host_command', 'read_host_command', 'stop_host_command'})


@dataclass(frozen=True)
class HostAccess:
    access_mode: str
    task_id: str
    cancel_event: threading.Event
    project_path: str
    secrets: tuple[str, ...]


_CONTEXT: ContextVar[HostAccess | None] = ContextVar('assistant_host_access', default=None)
_LOCK = threading.RLock()
_COMMANDS: dict[tuple[str, str], HostCommand] = {}


def _project(path):
    return str(layout(path).db.resolve())


@contextmanager
def host_access_context(access_mode, task_id, cancel_event, project_path, *, secrets_to_redact=()):
    if access_mode not in ('standard', 'full') or not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', task_id):
        raise ValueError('主机访问上下文的模式或任务编号无效')
    if not isinstance(cancel_event, threading.Event):
        raise ValueError('主机访问必须绑定当前任务停止信号')
    secrets = {value for name, value in os.environ.items() if re.search(r'(?:^|_)(?:KEY|TOKEN|SECRET)$', name, re.I) and value}
    secrets.update(value for value in secrets_to_redact if isinstance(value, str) and value)
    bound = HostAccess(access_mode, task_id, cancel_event, _project(project_path), tuple(sorted(secrets, key=len, reverse=True)))
    token = _CONTEXT.set(bound)
    try:
        yield bound
    finally:
        _CONTEXT.reset(token)


def _guard(ops):
    bound = _CONTEXT.get()
    if bound is None or bound.access_mode != 'full':
        raise ValueError('此工具仅限宿主已启用完全访问的当前任务；模型不能更改访问模式')
    if bound.project_path != str(Path(ops._store.path).resolve()):
        raise ValueError('主机能力上下文不属于当前工程')
    if bound.cancel_event.is_set():
        raise ValueError('当前任务已停止，不再执行新的主机操作')
    return bound


def _redact(value: Any, secrets) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_redact(item, secrets) for item in value]
    return value


def _secret_bytes(secrets):
    return tuple({secret.encode(encoding) for secret in secrets
                  for encoding in ('utf-8', 'utf-16-le', 'utf-16-be', 'utf-32-le', 'utf-32-be')})


def _masked_page(data, start, length, secrets):
    spans = []
    for secret in secrets:
        position = data.find(secret)
        while position >= 0:
            left, right = max(0, position-start), min(length, position+len(secret)-start)
            if left < right:
                spans.append((left, right))
            position = data.find(secret, position+1)
    merged = []
    for left, right in sorted(spans):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    page = data[start:start+length]
    marker = b'[REDACTED]' if isinstance(data, bytes) else '[REDACTED]'
    for left, right in reversed(merged):
        page = page[:left]+marker+page[right:]
    return page, bool(merged)


def _safe_host_method(function):
    @wraps(function)
    def invoke(self, *args, **kwargs):
        bound = _guard(self)
        try:
            return function(self, *args, **kwargs)
        except Exception as error:
            raise ValueError(_redact(str(error), bound.secrets)) from None
    return invoke


def _text_page(path, offset, limit, secrets):
    look = max((len(secret) for secret in secrets), default=1)-1
    beginning = max(0, offset-look)
    relative = offset-beginning
    with path.open('r', encoding='utf-8', newline='') if path.exists() else open(os.devnull) as stream:
        remaining = beginning
        while remaining:
            chunk = stream.read(min(remaining, MAX_PAGE))
            if not chunk:
                break
            remaining -= len(chunk)
        data = stream.read(relative+limit+look+1)
    length = len(data[relative:relative+limit])
    content, redacted = _masked_page(data, relative, length, secrets)
    return content, length, len(data) <= relative+limit, redacted


def _absolute(value, directory=False):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('请提供本机绝对路径')
    path = Path(value).resolve()
    if directory and not path.is_dir():
        raise ValueError('目录不存在')
    return path


def _page(offset, limit):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ValueError(f'offset须为非负整数，limit须为1..{MAX_PAGE}')


def _now():
    return datetime.now(UTC).isoformat()


def _root(bound):
    root = layout(bound.project_path).extraction / 'assistant' / 'tasks' / bound.task_id / 'host'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _atomic_json(path, value):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.host-audit-', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _audit(ops, bound, operation, args, result=None, error=None):
    record = _redact({'task_id': bound.task_id, 'access_mode': bound.access_mode, 'operation': operation,
                      'sampled_at': _now(), 'args': args, 'result': result, 'error': str(error) if error else None}, bound.secrets)
    path = _root(bound) / ('operation-' + uuid4().hex + '.json')
    _atomic_json(path, record)
    with ops._store.connect(write=True) as db:
        revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
        db.execute('INSERT INTO actions(id,revision,action,reason,summary) VALUES(?,?,?,?,?)',
                   (uuid4().hex, revision, operation, '完全访问主机操作', json.dumps({'task_id': bound.task_id, 'audit_path': str(path),
                    'path': record['args'].get('path'), 'command_id': (record['result'] or {}).get('command_id'), 'ok': error is None}, ensure_ascii=False)))
    return _redact(result, bound.secrets)


class _StreamRedactor:
    def __init__(self, secrets):
        self.secrets = secrets
        self.buffer = ''
        self.keep = max((len(secret) for secret in secrets), default=1)-1

    def feed(self, text, final=False):
        self.buffer += text
        keep = 0
        if not final:
            for secret in self.secrets:
                for length in range(min(len(secret)-1, len(self.buffer)), 0, -1):
                    if self.buffer.endswith(secret[:length]):
                        keep = max(keep, length)
                        break
        boundary = len(self.buffer)-keep
        while True:
            previous = boundary
            for secret in self.secrets:
                position = self.buffer.find(secret, max(0, boundary-len(secret)+1))
                if position >= 0 and position < boundary < position+len(secret):
                    boundary = position
            if previous == boundary:
                break
        result, self.buffer = self.buffer[:boundary], self.buffer[boundary:]
        return _redact(result, self.secrets)


@dataclass
class HostCommand:
    command_id: str
    bound: HostAccess
    process: subprocess.Popen[bytes]
    job: Any
    path: Path
    metadata_path: Path
    command: str
    cwd: str
    encoding: str
    background: bool
    started_at: str = field(default_factory=_now)
    status: str = 'running'
    exit_code: int | None = None
    detached: bool = False
    stop_requested: bool = False
    error: str | None = None
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)


def _metadata(entry):
    return _redact({'command_id': entry.command_id, 'task_id': entry.bound.task_id, 'status': entry.status, 'exit_code': entry.exit_code,
            'pid': entry.process.pid, 'background': entry.background, 'detached': entry.detached, 'started_at': entry.started_at,
            'command': entry.command, 'cwd': entry.cwd, 'output_path': str(entry.path), 'error': entry.error}, entry.bound.secrets)


def _stop(entry, *, unless_detached=False):
    requested = False
    with entry.lock:
        if entry.done.is_set() or (unless_detached and entry.detached):
            return False
        naturally_finished = entry.process.poll() is not None and os.name == 'nt' and not entry.job.active()
        if not entry.stop_requested and not naturally_finished:
            entry.stop_requested = True
            requested = True
            if os.name == 'nt':
                entry.job.terminate()
            else:
                try:
                    os.killpg(entry.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    entry.done.wait()
    return requested


def _collect(entry):
    decoder = codecs.getincrementaldecoder(entry.encoding)(errors='replace')
    redactor = _StreamRedactor(entry.bound.secrets)
    try:
        assert entry.process.stdout is not None
        with entry.path.open('w', encoding='utf-8', newline='') as output:
            while chunk := os.read(entry.process.stdout.fileno(), 8192):
                output.write(redactor.feed(decoder.decode(chunk)))
                output.flush()
            output.write(redactor.feed(decoder.decode(b'', final=True), final=True))
        entry.process.wait()
        entry.process.args = entry.command
        if os.name == 'nt':
            while entry.job.active():
                time.sleep(.02)
    except Exception as error:
        entry.error = _redact(str(error), entry.bound.secrets)
        try:
            if os.name == 'nt':
                entry.job.terminate()
            elif entry.process.poll() is None:
                os.killpg(entry.process.pid, signal.SIGKILL)
            entry.process.wait()
        except OSError:
            pass
    finally:
        if entry.process.stdout:
            entry.process.stdout.close()
        with entry.lock:
            entry.exit_code = entry.process.poll()
            entry.status = 'cancelled' if entry.stop_requested and entry.bound.cancel_event.is_set() and not entry.detached else 'stopped' if entry.stop_requested else 'failed' if entry.error or entry.exit_code else 'completed'
            if entry.job:
                entry.job.close()
            persisted = False
            try:
                _atomic_json(entry.metadata_path, _metadata(entry))
                persisted = True
            except OSError as error:
                entry.error = _redact('命令终态审计保存失败：'+str(error), entry.bound.secrets)
                entry.status = 'failed'
            finally:
                entry.done.set()
        if persisted:
            with _LOCK:
                if _COMMANDS.get((entry.bound.project_path, entry.command_id)) is entry:
                    _COMMANDS.pop((entry.bound.project_path, entry.command_id), None)


def _watch(entry):
    while not entry.done.wait(.05):
        with entry.lock:
            cancelled = entry.bound.cancel_event.is_set() and not entry.detached
        if cancelled:
            _stop(entry, unless_detached=True)
            return


def _lookup(ops, identifier):
    bound = _guard(ops)
    with _LOCK:
        entry = _COMMANDS.get((bound.project_path, identifier))
    if entry is not None:
        if entry.bound.task_id != bound.task_id and not entry.detached:
            raise ValueError('命令不属于当前任务或本工程已移交的后台服务')
        return bound, entry
    if not isinstance(identifier, str) or not re.fullmatch(r'command-[1-9][0-9]*', identifier):
        raise ValueError('命令编号无效')
    task_root = layout(bound.project_path).extraction / 'assistant' / 'tasks'
    for metadata_path in task_root.glob('*/host/' + identifier + '.json'):
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        expected_output = metadata_path.with_suffix('.stdout.txt')
        if (metadata.get('command_id') == identifier and metadata.get('task_id') == metadata_path.parent.parent.name
                and (metadata['task_id'] == bound.task_id or metadata.get('detached') is True)
                and metadata.get('status') in ('completed', 'failed', 'stopped', 'cancelled')
                and metadata.get('output_path') == str(expected_output)):
            return bound, metadata
    raise ValueError('命令不属于当前任务或本工程已移交的后台服务；跨进程仅可恢复已收束命令')


def finish_host_commands(project_path, task_id, completed):
    """Host lifecycle only: transfer explicit background on success; settle others."""
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', task_id) or type(completed) is not bool:
        raise ValueError('任务终态归属或完成状态无效')
    project = _project(project_path)
    with _LOCK:
        entries = [entry for (owner, _), entry in _COMMANDS.items() if owner == project and entry.bound.task_id == task_id and not entry.detached]
    diagnostics = []
    cleanup_receipts = []
    for entry in entries:
        transfer = False
        with entry.lock:
            if completed and entry.background and not entry.bound.cancel_event.is_set():
                try:
                    _atomic_json(entry.metadata_path, _metadata(entry) | {'detached': True})
                    entry.detached = True
                    transfer = True
                except OSError as error:
                    diagnostics.append({'command_id': entry.command_id, 'message': _redact(str(error), entry.bound.secrets)})
        if not transfer:
            stopped = _stop(entry, unless_detached=True)
            if stopped:
                reason = 'task_completed_before_command_finished' if completed else 'task_cancelled' if entry.bound.cancel_event.is_set() else 'task_failed_or_closed'
                receipt = {'command_id': entry.command_id, 'action': 'stopped_at_task_end', 'previous_status': 'running',
                           'status': entry.status, 'exit_code': entry.exit_code, 'background': entry.background, 'reason': reason}
                cleanup_receipts.append(receipt)
                if completed:
                    diagnostics.append({'severity': 'warning', 'code': 'host_command_stopped_at_task_end', 'command_id': entry.command_id,
                                        'message': f'前台命令{entry.command_id}在任务结束时仍未完成，已由程序停止收束，不能视为执行成功。'})
    # Already-finished processes are intentionally absent from the live registry.
    # Transfer their durable records too; this never revives a process or uses its PID.
    root = layout(project).extraction / 'assistant' / 'tasks' / task_id / 'host'
    terminal = []
    for path in root.glob('command-*.json'):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if record.get('task_id') != task_id or record.get('status') == 'running':
                continue
            if completed and record.get('background') is True and record.get('detached') is not True:
                record['detached'] = True
                _atomic_json(path, record)
            terminal.append(record)
        except (OSError, ValueError) as error:
            diagnostics.append({'message': '命令终态审计无法读取或更新：'+type(error).__name__})
    snapshots = {entry.command_id: _metadata(entry) for entry in entries}
    snapshots.update({record['command_id']: record for record in terminal})
    cleanup_path = root / 'task-cleanup.json'
    prior = {}
    if cleanup_path.is_file():
        try:
            prior = json.loads(cleanup_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            diagnostics.append({'severity': 'warning', 'code': 'host_cleanup_audit_unreadable', 'message': '先前主机收束收据无法读取'})
    receipts = {row['command_id']: row for row in prior.get('cleanup_receipts', [])}
    receipts.update({row['command_id']: row for row in cleanup_receipts})
    prior_diagnostics = prior.get('diagnostics', [])
    unique_diagnostics = {json.dumps(row, ensure_ascii=False, sort_keys=True): row for row in prior_diagnostics + diagnostics}
    result = {'task_id': task_id, 'commands': list(snapshots.values()), 'cleanup_receipts': list(receipts.values()),
              'diagnostics': list(unique_diagnostics.values())}
    if cleanup_receipts or diagnostics:
        root.mkdir(parents=True, exist_ok=True)
        _atomic_json(cleanup_path, result)
    return result


def read_host_task_cleanup(project_path, task_id):
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', task_id):
        raise ValueError('主机收束任务编号无效')
    path = layout(project_path).extraction / 'assistant' / 'tasks' / task_id / 'host' / 'task-cleanup.json'
    if not path.is_file():
        return {'task_id': task_id, 'commands': [], 'cleanup_receipts': [], 'diagnostics': []}
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('task_id') != task_id:
        raise ValueError('主机收束收据不属于指定任务')
    return value


def cancel_host_commands(project_path, task_id):
    return finish_host_commands(project_path, task_id, False)


def cancel_project_host_commands(project_path):
    project = _project(project_path)
    with _LOCK:
        entries = [entry for (owner, _), entry in _COMMANDS.items() if owner == project]
    for entry in entries:
        _stop(entry)
    with _LOCK:
        for key, entry in list(_COMMANDS.items()):
            if key[0] == project and entry.done.is_set():
                _COMMANDS.pop(key, None)
    return {'stopped': [entry.command_id for entry in entries]}


class HostOperationsMixin:
    _store: GraphStore
    @_safe_host_method
    def read_host_file(self, path, offset=0, limit=16000, encoding='utf-8'):
        bound = _guard(self)
        _page(offset, limit)
        target = _absolute(path)
        args = {'path': str(target), 'offset': offset, 'limit': limit, 'encoding': encoding}
        try:
            binary = encoding == 'base64'
            secrets = _secret_bytes(bound.secrets) if binary else bound.secrets
            look = max((len(secret) for secret in secrets), default=1)-1
            beginning = max(0, offset-look)
            relative = offset-beginning
            if binary:
                with target.open('rb') as stream:
                    stream.seek(beginning)
                    data = stream.read(relative+limit+look+1)
                length = len(data[relative:relative+limit])
                page, redacted = _masked_page(data, relative, length, secrets)
                content = base64.b64encode(page).decode('ascii')
                unit = 'bytes'
            else:
                codecs.lookup(encoding)
                with target.open('r', encoding=encoding, newline='') as stream:
                    remaining = beginning
                    while remaining:
                        skipped = stream.read(min(remaining, MAX_PAGE))
                        if not skipped:
                            break
                        remaining -= len(skipped)
                    data = stream.read(relative+limit+look+1)
                length = len(data[relative:relative+limit])
                content, redacted = _masked_page(data, relative, length, secrets)
                unit = 'characters'
            eof = len(data) <= relative+limit
            result = {'path': str(target), 'content': content, 'encoding': encoding, 'offset': offset, 'offset_unit': unit,
                      'next_offset': offset+length, 'eof': eof, 'redacted': redacted}
        except Exception as error:
            _audit(self, bound, 'read_host_file', args, error=error)
            raise ValueError(_redact(str(error), bound.secrets)) from None
        return _audit(self, bound, 'read_host_file', args, result)

    @_safe_host_method
    def write_host_file(self, path, content, encoding='utf-8', append=False):
        bound = _guard(self)
        target = _absolute(path)
        if not isinstance(content, str) or type(append) is not bool:
            raise ValueError('文件内容须为文本，append须为布尔值')
        args = {'path': str(target), 'content': '[binary input omitted]' if encoding == 'base64' else content, 'encoding': encoding, 'append': append}
        temporary = None
        try:
            data = base64.b64decode(content, validate=True) if encoding == 'base64' else content.encode(encoding)
            if encoding == 'base64':
                args.update(byte_count=len(data), checksum_sha256=hashlib.sha256(data).hexdigest(),
                            redacted=any(secret in data for secret in _secret_bytes(bound.secrets)))
            if append:
                with target.open('ab') as stream:
                    stream.write(data)
            else:
                descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix='.host-write-')
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(data)
                os.replace(temporary, target)
            result = {'path': str(target), 'bytes_written': len(data), 'append': append}
        except Exception as error:
            _audit(self, bound, 'write_host_file', args, error=error)
            raise ValueError(_redact(str(error), bound.secrets)) from None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
        return _audit(self, bound, 'write_host_file', args, result)

    @_safe_host_method
    def list_host_directory(self, path, offset=0, limit=100):
        bound = _guard(self)
        _page(offset, limit)
        target = _absolute(path, directory=True)
        entries = []
        eof = True
        with os.scandir(target) as iterator:
            for index, entry in enumerate(iterator):
                if index < offset:
                    continue
                if len(entries) == limit:
                    eof = False
                    break
                stat = entry.stat(follow_symlinks=False)
                entries.append({'name': entry.name, 'path': entry.path, 'is_directory': entry.is_dir(), 'is_symlink': entry.is_symlink(), 'size_bytes': stat.st_size})
        return _audit(self, bound, 'list_host_directory', {'path': str(target), 'offset': offset, 'limit': limit},
                      {'path': str(target), 'entries': entries, 'offset': offset, 'next_offset': offset+len(entries), 'eof': eof})

    @_safe_host_method
    def run_host_command(self, command, cwd, encoding='utf-8', background=False):
        bound = _guard(self)
        directory = _absolute(cwd, directory=True)
        if not isinstance(command, str) or not command.strip() or type(background) is not bool:
            raise ValueError('命令须为非空文本，background须为布尔值')
        codecs.lookup(encoding)
        with self._store.connect(write=True) as db:
            identifier = allocate_id(db, 'assistant_host', 'command-')
        root = _root(bound)
        path, metadata = root / (identifier+'.stdout.txt'), root / (identifier+'.json')
        job = None
        process = None
        try:
            if os.name == 'nt':
                from .assistant_host_process import WindowsCommandJob
                job = WindowsCommandJob()
                process = subprocess.Popen(command, shell=True, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           bufsize=0, creationflags=0x00000004 | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
                job.assign_and_resume(process)
            else:
                process = subprocess.Popen(command, shell=True, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           bufsize=0, start_new_session=True)
            entry = HostCommand(identifier, bound, process, job, path, metadata, _redact(command, bound.secrets), str(directory), encoding, background)
            with _LOCK:
                _COMMANDS[(bound.project_path, identifier)] = entry
            _atomic_json(metadata, _redact(_metadata(entry), bound.secrets))
            threading.Thread(target=_collect, args=(entry,), name=identifier+'-output', daemon=True).start()
            threading.Thread(target=_watch, args=(entry,), name=identifier+'-cancel', daemon=True).start()
            return _audit(self, bound, 'run_host_command', {'command': command, 'cwd': str(directory), 'background': background},
                          {'command_id': identifier, 'task_id': bound.task_id, 'status': 'running', 'background': background})
        except Exception as error:
            if job:
                job.terminate()
                job.close()
            if process:
                process.kill()
                process.wait()
            _audit(self, bound, 'run_host_command', {'command': command, 'cwd': str(directory), 'background': background}, error=error)
            raise ValueError(_redact(str(error), bound.secrets)) from None

    @_safe_host_method
    def read_host_command(self, command_id, offset=0, limit=16000):
        bound, entry = _lookup(self, command_id)
        _page(offset, limit)
        path = Path(entry['output_path']) if isinstance(entry, dict) else entry.path
        content, length, eof, redacted = _text_page(path, offset, limit, bound.secrets)
        if isinstance(entry, dict):
            metadata, done = entry, True
        else:
            with entry.lock:
                metadata, done = _metadata(entry), entry.done.is_set()
        output_available = path.is_file()
        result = metadata | {'content': content, 'offset': offset, 'offset_unit': 'characters',
                             'next_offset': offset+length, 'eof': eof, 'redacted': redacted,
                             'complete': done and eof and output_available, 'output_available': output_available}
        return _redact(result, bound.secrets)

    @_safe_host_method
    def stop_host_command(self, command_id):
        bound, entry = _lookup(self, command_id)
        if not isinstance(entry, dict):
            _stop(entry)
        return _audit(self, bound, 'stop_host_command', {'command_id': command_id}, entry if isinstance(entry, dict) else _metadata(entry))
