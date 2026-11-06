import io
import json

import pytest

from narrative import mcp_server, tool_cli
from narrative.errors import OperationError


def test_cli_waits_pages_and_cleans_exact_owner(monkeypatch):
    calls, cleanup = [], []
    def call(name, args, **kwargs):
        calls.append((name, args))
        if name == 'run_host_command':
            return {'command_id': 'command-1'}
        return {'content': 'a' if len(calls) == 2 else 'b', 'next_offset': len(calls)-1, 'complete': len(calls) == 3}
    monkeypatch.setattr(tool_cli.tool_api, 'call', call)
    monkeypatch.setattr(tool_cli, 'cancel_host_commands', lambda project, owner: cleanup.append((project, owner)))
    result = tool_cli._invoke('run_host_command', {}, 'fixture.nproj', 'full')
    assert result['content'] == 'ab'
    assert calls[2][1]['offset'] == 1
    assert cleanup[0][1].startswith('cli-')


def test_cli_background_rejected_and_interrupt_cancelled(monkeypatch):
    with pytest.raises(OperationError, match='驻留助手'):
        tool_cli._invoke('run_host_command', {'background': True}, 'fixture.nproj', 'full')
    cleanup = []
    monkeypatch.setattr(tool_cli.tool_api, 'call', lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(tool_cli, 'cancel_host_commands', lambda *args: cleanup.append(args))
    with pytest.raises(OperationError, match='已停止当前命令'):
        tool_cli._invoke('run_host_command', {}, 'fixture.nproj', 'full')
    assert len(cleanup) == 1


def test_mcp_standard_cannot_elevate(monkeypatch):
    monkeypatch.setattr(mcp_server.tool_api, 'call', lambda *a, **k: pytest.fail('must not execute'))
    request = {'id': 1, 'method': 'tools/call', 'params': {'name': 'set_assistant_access', 'arguments': {'access_mode': 'full'}}}
    assert mcp_server.handle(request, default_project='fixture.nproj')['result']['isError']
    names = {tool['name'] for tool in mcp_server._tool_list('fixture.nproj')}
    assert 'set_assistant_access' not in names
    assert 'run_host_command' not in names


def test_mcp_stable_owner_and_session_only_cleanup(monkeypatch):
    from narrative.assistant_host import _CONTEXT
    owners, cleanup = [], []
    def call(*args, **kwargs):
        bound = _CONTEXT.get()
        owners.append(bound.task_id)
        assert bound.access_mode == 'full'
        return {'ok': True}
    monkeypatch.setattr(mcp_server.tool_api, 'call', call)
    monkeypatch.setattr(mcp_server, 'cancel_host_commands', lambda *args: cleanup.append(args))
    message = json.dumps({'id': 1, 'method': 'tools/call', 'params': {'name': 'list_host_directory', 'arguments': {}}})
    mcp_server.serve(default_project='fixture.nproj', access_mode='full', stdin=io.StringIO(message+'\n'+message+'\n'), stdout=io.StringIO())
    assert len(owners) == 2 and owners[0] == owners[1]
    assert cleanup == [('fixture.nproj', owners[0])]


def test_cli_real_process_and_standard_guard(tmp_path, monkeypatch, capsys):
    import os
    import shlex
    import subprocess
    import sys

    from narrative.assistant_host import _COMMANDS
    from narrative.operations import Operations

    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    entry = tmp_path / 'host.nproj'
    Operations.create_graph_project(entry)
    command_args = [sys.executable, '-c', "print('transport-smoke')"]
    command = subprocess.list2cmdline(command_args) if os.name == 'nt' else shlex.join(command_args)
    argv = ['host-run', '--project', str(entry), '--command', command, '--cwd', str(tmp_path), '--json']
    assert tool_cli.main(['--access-mode', 'full', *argv]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'completed'
    assert result['content'].strip() == 'transport-smoke'
    assert result['task_id'].startswith('cli-task-')
    assert all(row.done.is_set() for row in _COMMANDS.values() if row.bound.task_id == result['task_id'])
    assert tool_cli.main(argv) == 2
    assert '完全访问' in capsys.readouterr().err


def test_mcp_without_default_project_real_directory(tmp_path, monkeypatch):
    from narrative.operations import Operations

    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    entry = tmp_path / 'host.nproj'
    Operations.create_graph_project(entry)
    (tmp_path / 'visible.txt').write_text('example', encoding='utf-8')
    schema = next(row['inputSchema'] for row in mcp_server._tool_list(None, 'full') if row['name'] == 'list_host_directory')
    assert 'project' in schema['required']
    configuration = next(row['inputSchema'] for row in mcp_server._tool_list(None, 'full') if row['name'] == 'set_assistant_access')
    assert 'project' in configuration['required']
    request = {'id': 1, 'method': 'tools/call', 'params': {'name': 'list_host_directory', 'arguments': {'project': str(entry), 'path': str(tmp_path)}}}
    output = io.StringIO()
    mcp_server.serve(default_project=None, access_mode='full', stdin=io.StringIO(json.dumps(request)+'\n'), stdout=output)
    response = json.loads(output.getvalue())['result']
    assert not response['isError'], response
    assert 'visible.txt' in response['content'][0]['text']
