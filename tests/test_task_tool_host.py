"""Local HTTP adapter checks; no provider or model calls."""

import http.client
import json
from urllib.parse import urlsplit

import pytest

from narrative.task_tool_host import TaskToolHost


@pytest.fixture
def host():
    calls = []

    def read(name, args):
        calls.append((name, args))
        return {"text": "abcdefghij" * 12000, "start": args.get("start", 0)}

    server = TaskToolHost([{"name": "read_source", "parameters": {"type": "object"}}], read)
    try:
        yield server, calls
    finally:
        server.close()


def post(server, name, *, token=None, args=None, session_id=None):
    address = urlsplit(server.config["url"])
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=2)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    try:
        envelope = {"name": name, "args": args or {}}
        if session_id is not None:
            connection.request("POST", "/authority", json.dumps({"session_id": session_id}), headers)
            authority = connection.getresponse()
            raw = authority.read()
            if authority.status != 200:
                return authority.status, raw
            envelope.update(session_id=session_id, nonce=json.loads(raw)["nonce"])
        connection.request("POST", "/call", json.dumps(envelope), headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_unauthorized_calls_do_not_reach_tool_handler(host):
    server, calls = host
    for token in (None, "wrong-offline-token"):
        status, _ = post(server, "read_source", token=token)
        assert status == 401
    assert calls == []


def test_authenticated_read_is_complete_and_write_tool_is_denied(host):
    server, calls = host
    status, body = post(server, "read_source", token=server.token, args={"start": 50000})
    result = json.loads(body)
    assert status == 200 and result["ok"] is True
    assert len(result["result"]["text"]) == 120000
    assert result["result"]["start"] == 50000
    assert calls == [("read_source", {"start": 50000})]
    status, body = post(server, "import_source", token=server.token, args={"text": "must not write"})
    assert status == 400 and json.loads(body)["ok"] is False
    assert calls == [("read_source", {"start": 50000})]


def test_close_is_idempotent_and_stops_serving_tools(host):
    server, calls = host
    server.close()
    server.close()
    with pytest.raises(OSError):
        post(server, "read_source", token=server.token)
    assert not server._thread.is_alive()
    assert calls == []
