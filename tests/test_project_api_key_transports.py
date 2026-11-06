"""Credential configuration accepts stdin, never echoes secrets or grants model access."""
import io
import json

from narrative import assistant, mcp_server, tool_cli
from narrative.harness import HarnessSettings
from narrative.operations import Operations


def test_cli_stdin_key_roundtrip_and_mcp_cannot_self_configure(tmp_path, monkeypatch, capsys):
    database = tmp_path / 'providers.sqlite3'
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(database))
    monkeypatch.delenv('NARRATIVE_TEST_SAVED_KEY', raising=False)
    entry = tmp_path / 'credential.nproj'
    ops = Operations.create_graph_project(entry)
    profile = HarnessSettings(provider='custom', model='offline', base_url='https://example.invalid/v1',
                              key_env='NARRATIVE_TEST_SAVED_KEY').model_dump()
    ops.save_provider_profile(profile)
    secret = 'offline-stdin-secret-for-roundtrip'
    monkeypatch.setattr('sys.stdin', io.StringIO(secret+'\n'))
    assert tool_cli.main(['project-api-key', 'set', '--key-stdin', '--project', str(entry)]) == 0
    output = capsys.readouterr().out
    assert secret not in output
    assert json.loads(output)['local_available']
    assert secret.encode() not in database.read_bytes()
    for mode in ('standard', 'full'):
        assert not {'configure_project_api_key', 'check_project_api_key'} & {row['name'] for row in assistant.tool_catalog(access_mode=mode)}
    response = mcp_server.handle({'id': 1, 'method': 'tools/call', 'params': {
        'name': 'configure_project_api_key', 'arguments': {'clear_api_key': True}}}, default_project=str(entry))
    assert response['result']['isError'] and ops.provider_api_key_status()['local_available']
    assert tool_cli.main(['project-api-key', 'clear', '--project', str(entry)]) == 0
    assert not json.loads(capsys.readouterr().out)['local_available']


def test_cli_requires_explicit_stdin_and_does_not_echo_invalid_input(capsys):
    assert tool_cli.main(['project-api-key', 'set', '--project', 'unused.nproj']) == 2
    assert '--key-stdin' in capsys.readouterr().err


def test_cli_check_is_explicit_non_persisting_and_unsuccessful_check_is_nonzero(monkeypatch, capsys):
    calls = []
    def call(name, args, **kwargs):
        calls.append((name, args))
        return {'valid': None, 'code': 'rate_limited', 'message': '服务限流'}
    monkeypatch.setattr(tool_cli.tool_api, 'call', call)
    monkeypatch.setattr('sys.stdin', io.StringIO('unsaved-test-secret\n'))
    assert tool_cli.main(['project-api-key', 'check', '--key-stdin', '--project', 'unused.nproj']) == 1
    assert calls == [('check_project_api_key', {'api_key': 'unsaved-test-secret'})]
    assert 'unsaved-test-secret' not in capsys.readouterr().out
