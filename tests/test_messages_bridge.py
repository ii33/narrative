"""Messages ↔ chat/completions 的线格式转换：形状要一一对上。"""

from __future__ import annotations

import json

from narrative.messages_bridge import (
    MessagesStream,
    to_chat_request,
    to_messages_response,
)


def _events(raw: bytes) -> list[tuple[str, dict]]:
    """把 SSE 文本拆成 (事件名, payload)。"""
    out = []
    for frame in raw.decode("utf-8").split("\n\n"):
        if not frame.strip():
            continue
        name, data = "", ""
        for line in frame.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data += line[6:]
        if name:
            out.append((name, json.loads(data)))
    return out


def test_a_messages_request_becomes_a_chat_request() -> None:
    request = to_chat_request({
        "model": "m",
        "max_tokens": 100,
        "system": "你是提取器",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "读这一段"}]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "我看看"},
                {"type": "tool_use", "id": "t1", "name": "list_nodes", "input": {"type": "person"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "3 个节点"},
                {"type": "text", "text": "继续"},
            ]},
        ],
        "tools": [{"name": "list_nodes", "description": "列节点",
                   "input_schema": {"type": "object", "properties": {"type": {"type": "string"}}}}],
        "dsh_session_log": {"version": 1},
        "dsh_plugin_packages": {"packages": []},
    })

    assert request["messages"][0] == {"role": "system", "content": "你是提取器"}
    assert request["messages"][1]["role"] == "user"
    assistant = request["messages"][2]
    assert assistant["content"] == "我看看"
    assert assistant["tool_calls"][0]["function"]["name"] == "list_nodes"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"type": "person"}
    # 工具结果紧跟着那条 assistant，然后才是这一轮的新内容。
    assert request["messages"][3] == {
        "role": "tool", "tool_call_id": "t1", "content": "3 个节点",
    }
    assert request["messages"][4]["content"] == [{"type": "text", "text": "继续"}]
    assert request["tools"][0]["function"]["parameters"]["properties"]["type"]["type"] == "string"
    assert request["stream"] is True
    # 运行时带的诊断字段不进上游。
    assert "dsh_session_log" not in request and "dsh_plugin_packages" not in request


def test_an_image_block_becomes_a_data_url() -> None:
    request = to_chat_request({
        "model": "m",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "这是什么"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
        ]}],
    })
    parts = request["messages"][0]["content"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,AAA"


def test_text_deltas_become_one_text_block() -> None:
    stream = MessagesStream(model="m")
    first = stream.feed(
        b'data: {"choices":[{"delta":{"content":"\\u4f60"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"\\u597d"}}]}\n\n'
    )
    events = _events(first)
    assert [name for name, _payload in events] == [
        "message_start", "content_block_start", "content_block_delta", "content_block_delta",
    ]
    assert events[1][1]["content_block"]["type"] == "text"
    assert events[2][1]["delta"] == {"type": "text_delta", "text": "你"}

    tail = _events(stream.finish())
    assert [name for name, _payload in tail] == [
        "content_block_stop", "message_delta", "message_stop",
    ]
    assert tail[1][1]["delta"]["stop_reason"] == "end_turn"


def test_tool_arguments_stream_as_json_deltas() -> None:
    stream = MessagesStream(model="m")
    raw = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
        b'"function":{"name":"get_node","arguments":"{\\"node"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        b'"function":{"arguments":"_id\\":\\"P1\\"}"}}]}}]}\n\n'
        b'data: {"choices":[{"finish_reason":"tool_calls","delta":{}}]}\n\n'
    )
    events = _events(stream.feed(raw))
    starts = [payload for name, payload in events if name == "content_block_start"]
    assert starts[0]["content_block"]["type"] == "tool_use"
    assert starts[0]["content_block"]["name"] == "get_node"
    deltas = [payload["delta"] for name, payload in events if name == "content_block_delta"]
    assert [delta["type"] for delta in deltas] == ["input_json_delta", "input_json_delta"]
    assert json.loads("".join(delta["partial_json"] for delta in deltas)) == {"node_id": "P1"}

    tail = _events(stream.finish())
    stop = next(payload for name, payload in tail if name == "message_delta")
    assert stop["delta"]["stop_reason"] == "tool_use"


def test_usage_and_length_reasons_carry_over() -> None:
    stream = MessagesStream(model="m")
    stream.feed(b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"length"}],'
                b'"usage":{"prompt_tokens":11,"completion_tokens":7}}\n\n')
    tail = _events(stream.finish())
    delta = next(payload for name, payload in tail if name == "message_delta")
    assert delta["delta"]["stop_reason"] == "max_tokens"
    assert delta["usage"]["output_tokens"] == 7
    assert stream.input_tokens == 11


def test_a_plain_response_can_be_returned_whole() -> None:
    message = to_messages_response({
        "choices": [{"message": {"content": "好了", "tool_calls": [
            {"id": "c1", "function": {"name": "list_nodes", "arguments": "{\"a\":1}"}},
        ]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }, model="m")
    assert message["content"][0] == {"type": "text", "text": "好了"}
    assert message["content"][1]["input"] == {"a": 1}
    assert message["stop_reason"] == "tool_use"
    assert message["usage"] == {"input_tokens": 3, "output_tokens": 4}
