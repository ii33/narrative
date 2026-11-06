"""Runtime credentials are scoped to the selected project/provider endpoint."""
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from narrative import harness
from narrative.harness import HarnessSettings
from narrative.model_gateway import ModelGateway
from narrative.operations import Operations


def test_configured_key_uses_only_requested_process_then_registry(monkeypatch):
    looked = []
    monkeypatch.setattr(harness, '_windows_key', lambda name='DEEPSEEK_API_KEY': (looked.append(name) or 'registry-value', 'user_environment'))
    monkeypatch.setenv('NARRATIVE_SELECTED_KEY', ' process-value ')
    assert harness.configured_key('NARRATIVE_SELECTED_KEY') == ('process-value', 'process_environment')
    assert not looked
    monkeypatch.delenv('NARRATIVE_SELECTED_KEY')
    assert harness.configured_key('NARRATIVE_SELECTED_KEY') == ('registry-value', 'user_environment')
    assert looked == ['NARRATIVE_SELECTED_KEY']


def test_harness_env_does_not_cross_provider_fallback_and_local_noauth(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'wrong-provider-key')
    monkeypatch.delenv('NARRATIVE_MISSING_KEY', raising=False)
    monkeypatch.setattr(harness, '_windows_key', lambda name='DEEPSEEK_API_KEY': ('', 'none'))
    ops = Operations.create_graph_project(tmp_path / 'project.nproj')
    settings = HarnessSettings(provider='custom', model='offline', base_url='https://example.invalid/v1', key_env='NARRATIVE_MISSING_KEY')
    ops.save_provider_profile(settings.model_dump())
    with pytest.raises(ValueError, match='NARRATIVE_MISSING_KEY'):
        harness.harness_env(ops)
    local = settings.model_copy(update={'base_url': 'http://127.0.0.1:1/v1'})
    ops.save_provider_profile(local.model_dump())
    assert harness.harness_env(ops)[1] == 'local-no-auth'
    assert harness.resolve_key(settings, 'explicit-key', ops=ops) == 'explicit-key'


def test_real_gateway_header_uses_project_saved_key_without_public_secret(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    monkeypatch.setenv('NARRATIVE_SELECTED_KEY', 'environment-key')
    headers = []
    class LocalService(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            headers.append(self.headers.get('Authorization'))
            frames = [{'choices': [{'index': 0, 'delta': {'content': 'ok'}, 'finish_reason': 'stop'}]}, {'choices': [], 'usage': {'prompt_tokens': 20, 'completion_tokens': 1, 'total_tokens': 21}}]
            raw = (''.join('data: '+json.dumps(f)+'\n\n' for f in frames)+'data: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), LocalService)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    gateway = None
    try:
        ops = Operations.create_graph_project(tmp_path / 'saved.nproj')
        settings = HarnessSettings(provider='custom', model='offline', base_url=f'http://127.0.0.1:{server.server_port}/v1', key_env='NARRATIVE_SELECTED_KEY')
        saved = 'project-local-key'
        result = ops.save_provider_profile(settings.model_dump(), api_key=saved)
        resolved, key, _ = harness.harness_env(ops)
        assert key == saved and harness.resolve_key(settings, 'explicit', ops=ops) == 'explicit'
        assert saved not in json.dumps(result)
        gateway = ModelGateway(resolved, key)
        with gateway.session('local-credential-check'):
            address = urlsplit(gateway.base_url)
            connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
            connection.request('POST', address.path+'/chat/completions', json.dumps({'model': 'offline', 'messages': [{'role': 'user', 'content': 'ok'}], 'stream': True}), {'Authorization': 'Bearer '+gateway.token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            assert response.status == 200
            response.read()
            connection.close()
        assert headers == ['Bearer '+saved]
        assert saved not in json.dumps(gateway.request_snapshot())
        # Same provider, changed URL does not inherit the previous endpoint's key.
        changed = settings.model_copy(update={'base_url': 'http://127.0.0.1:1/other'})
        ops.save_provider_profile(changed.model_dump())
        assert harness.harness_env(ops)[1] == 'environment-key'
    finally:
        if gateway is not None:
            gateway.close()
        server.shutdown()
        server.server_close()


def test_headless_durable_audit_redacts_resolved_secret_without_changing_execution(tmp_path, monkeypatch):
    from test_assistant_access_runtime import ResidentStub

    from narrative.conversation_runtime import close_project_runtime
    from narrative.conversation_session import open_conversation_session
    from narrative.harness import HarnessReply

    secret = 'saved-headless-credential'
    class EchoResident(ResidentStub):
        def redact(self, text):
            return text.replace(secret, '[REDACTED]')
        def agent_turn(self, instruction, session, **kwargs):
            assert secret in json.dumps(instruction)
            return HarnessReply(secret, {'finish_reason': 'completed', 'provider_note': secret})
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    ops = Operations.create_graph_project(tmp_path / 'audit.nproj')
    wrapper = None
    try:
        wrapper, session = open_conversation_session(ops, HarnessSettings(provider='custom', model='offline', base_url='http://127.0.0.1:1/v1'), secret, EchoResident)
        reply = wrapper.agent_turn('echo '+secret, session)
        assert reply.raw == secret
        wrapper.close()
        files = list((wrapper.directory / 'turns').rglob('*'))
        persisted = [p for p in files if p.is_file()]
        assert any(p.name == 'raw.txt' for p in persisted)
        assert all(secret not in p.read_text(encoding='utf-8') for p in persisted)
    finally:
        if wrapper is not None:
            wrapper.close()
        close_project_runtime(ops)
