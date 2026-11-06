"""A completed response always exposes its final tool identity to live readers."""
import threading

from test_gateway_turn_cancel import _frame, _local_request, _upstream, _wait_until

from narrative.harness import HarnessSettings
from narrative.model_gateway import ModelGateway


def test_done_before_socket_eof_publishes_usage_and_tool_identity_atomically():
    done = (
        _frame({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0,
            "id": "quote-location", "function": {"name": "locate_source", "arguments": '{"quotes":["原文"]}'}}]},
            "finish_reason": "tool_calls"}]})
        + _frame({"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 2, "total_tokens": 42}})
        + b"data: [DONE]\n\n"
    )
    # Hold the HTTP connection open after DONE. Final handler cleanup has not
    # happened yet; this is exactly when the external observer reads metadata.
    with _upstream(done) as (port, first_seen, release, _requests):
        settings = HarnessSettings(provider="custom", base_url=f"http://127.0.0.1:{port}/v1", model="offline")
        gateway = ModelGateway(settings, "test-key")
        result = {}
        try:
            with gateway.session("usage-completion-check"):
                client = threading.Thread(target=_local_request, args=(gateway, "offline", result))
                client.start()
                try:
                    assert first_seen.wait(3)
                    _wait_until(lambda: bool(gateway.request_snapshot()) and gateway.request_snapshot()[0]["complete"])
                    record = gateway.request_snapshot()[0]
                    assert record["usage"]["total_tokens"] == 42
                    assert [call["id"] for call in record["tool_calls"]] == ["quote-location"]
                    assert record["tool_calls"][0]["arguments_json_valid"]
                finally:
                    release.set()
                    client.join(4)
                assert not client.is_alive() and result.get("status") == 200
        finally:
            gateway.close()
