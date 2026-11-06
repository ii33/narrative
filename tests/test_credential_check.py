"""Credential probes are one bounded HTTP request and never mutate configuration."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from narrative.credential_check import check_model_credential
from narrative.harness import HarnessSettings


@pytest.mark.parametrize(('status', 'payload', 'expected', 'code'), [
    (200, {'choices': [{'message': {'content': 'OK'}}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 1}}, True, 'valid'),
    (200, {'choices': [{'message': {'content': None, 'reasoning_content': 'brief'}, 'finish_reason': 'length'}]}, True, 'valid'),
    (200, {'secret_echo': 'saved-key'}, None, 'invalid_response'),
    (401, {'error': 'saved-key'}, False, 'authentication_failed'),
    (403, {'error': 'saved-key'}, False, 'permission_denied'),
    (400, {}, None, 'configuration_error'),
    (404, {}, None, 'configuration_error'),
    (429, {}, None, 'rate_limited'),
    (500, {}, None, 'service_error'),
])
def test_one_probe_http_classification_and_saved_key(status, payload, expected, code):
    seen = []
    class Endpoint(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            seen.append((self.headers.get('Authorization'), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    class ReadOnlyOps:
        def _saved_provider_api_key(self, profile):
            return 'saved-key'
    try:
        settings = HarnessSettings(provider='custom', base_url=f'http://127.0.0.1:{server.server_port}/v1', model='offline', token_parameter='max_completion_tokens')
        result = check_model_credential(ReadOnlyOps(), settings.model_dump())
        assert result['valid'] is expected and result['code'] == code
        assert result['http_status'] == status
        assert len(seen) == 1 and seen[0][0] == 'Bearer saved-key'
        body = seen[0][1]
        assert body['max_completion_tokens'] == 64 and 'max_tokens' not in body
        assert body['stream'] is False and 'tools' not in body
        assert 'saved-key' not in json.dumps(result)
        assert result['usage'] == payload.get('usage')
    finally:
        server.shutdown()
        server.server_close()


def test_missing_credential_makes_zero_connections(monkeypatch):
    from narrative import credential_check
    class MissingOps:
        def _saved_provider_api_key(self, profile):
            return None
    monkeypatch.setattr('narrative.harness.configured_key', lambda _: ('', 'none'))
    monkeypatch.setattr(credential_check, '_UpstreamHTTPSConnection', lambda *args, **kwargs: pytest.fail('must not connect'))
    result = check_model_credential(MissingOps(), HarnessSettings().model_dump())
    assert result['valid'] is None and result['http_status'] is None


def test_unsaved_key_and_clear_saved_selection_do_not_mutate_profile(monkeypatch):
    from narrative import credential_check
    used = []
    class Ops:
        def _saved_provider_api_key(self, profile):
            return 'saved-key'
    class Response:
        status = 200
        def read(self, limit):
            return b'{"choices":[{"message":{"content":"OK"}}]}'
    class Connection:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, method, path, body, headers):
            used.append(headers['Authorization'])
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(credential_check, '_UpstreamHTTPSConnection', Connection)
    monkeypatch.setattr('narrative.harness.configured_key', lambda _: ('environment-key', 'process_environment'))
    profile = HarnessSettings().model_dump()
    before = dict(profile)
    assert check_model_credential(Ops(), profile, 'unsaved-key')['valid'] is True
    assert check_model_credential(Ops(), profile, ignore_saved_key=True)['valid'] is True
    assert used == ['Bearer unsaved-key', 'Bearer environment-key'] and profile == before
