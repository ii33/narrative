"""The wire observer preserves malformed provider arguments without repairing them."""
import json

from narrative.messages_bridge import MessagesStream
from narrative.model_gateway import ModelGateway, UsageObserver


def frame(delta, *, finish=None, choice=0):
    return ("data: " + json.dumps({"choices": [{"index": choice, "delta": delta,
                                              "finish_reason": finish}]}, ensure_ascii=False)
            + "\n\n").encode()


def test_split_tool_arguments_survive_observation_and_messages_conversion_verbatim():
    raw = '{"items":["原值" as_placeholder]}'
    fragments = [raw[:12], raw[12:21], raw[21:]]
    wire = frame({"tool_calls": [{"index": 0, "id": "broken", "function": {"name": "preview_extraction", "arguments": ""}}]})
    for fragment in fragments:
        wire += frame({"tool_calls": [{"index": 0, "function": {"arguments": fragment}}]})
    wire += frame({}, finish="tool_calls") + b"data: [DONE]\n\n"
    observer, converter = UsageObserver(), MessagesStream(model="offline")
    converted = b""
    # Split both SSE frames and multibyte Unicode characters across reads.
    for offset in range(0, len(wire), 7):
        chunk = wire[offset:offset + 7]
        observer.feed(chunk)
        converted += converter.feed(chunk)
    converted += converter.finish()
    saved = observer.tool_snapshot()[0]
    assert saved["arguments"] == raw
    assert saved["argument_fragments"] == ["", *fragments]
    assert saved["name"] == "preview_extraction" and saved["id"] == "broken"
    assert saved["arguments_json_valid"] is False
    assert saved["arguments_json_error"]["position"] == raw.index(" as_placeholder") + 1
    assert observer.complete and observer.finish_reason == "tool_calls"
    deltas = []
    for line in converted.decode().splitlines():
        if line.startswith("data: "):
            data = json.loads(line[6:])
            delta = data.get("delta", {})
            if delta.get("type") == "input_json_delta":
                deltas.append(delta["partial_json"])
    assert "".join(deltas) == raw


def test_parallel_tool_indices_keep_independent_valid_arguments_and_copy_snapshots():
    observer = UsageObserver()
    observer.feed(frame({"tool_calls": [
        {"index": 1, "id": "two", "function": {"name": "second", "arguments": '{"b":'}},
        {"index": 0, "id": "one", "function": {"name": "first", "arguments": '{"a":'}},
    ]}))
    observer.feed(frame({"tool_calls": [
        {"index": 0, "function": {"arguments": '1}'}},
        {"index": 1, "function": {"arguments": '2}'}},
    ]}))
    saved = observer.tool_snapshot()
    assert [tool["arguments"] for tool in saved] == ['{"a":1}', '{"b":2}']
    assert all(tool["arguments_json_valid"] for tool in saved)
    saved[0]["argument_fragments"].append("corrupted")
    assert observer.tool_snapshot()[0]["arguments"] == '{"a":1}'


def test_audit_redacts_credentials_even_when_they_cross_argument_fragment_boundaries():
    gateway = ModelGateway.__new__(ModelGateway)
    gateway._key, gateway.token = "private-provider-key", "private-gateway-token"
    gateway._extra_secrets = ("private-host-token",)
    raw = '{"value":"private-host-token"}'
    value = {"tool_calls": [{"arguments": raw,
              "argument_fragments": ['{"value":"private-', 'host-token"}']}],
             "raw": "private-provider-key private-gateway-token"}
    safe = gateway._redact(value)
    encoded = json.dumps(safe)
    assert "private-" not in encoded and "host-token" not in encoded
    assert safe["tool_calls"][0]["arguments_redacted"] is True
    assert value["tool_calls"][0]["arguments"] == raw


def test_reasoning_content_is_not_collected_as_a_visible_output_or_tool_argument():
    observer = UsageObserver()
    observer.feed(frame({"reasoning_content": "internal-private-reasoning"}))
    assert observer.content == [] and observer.tool_snapshot() == []
