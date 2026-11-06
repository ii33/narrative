"""Anthropic Messages ↔ Chat Completions 的线格式转换。

0.2.x 的 dsh 运行时改说 Messages（`POST /v1/messages`，`x-api-key`，
`anthropic-version`），而上游供应商（方舟订阅端点）只认 chat/completions。
这一层做两件事：

* `to_chat_request`：把 Messages 请求翻成 chat/completions 请求；
* `MessagesStream`：把 chat/completions 的 SSE 增量翻成 Messages 的 SSE 事件。

转换只做形状搬运，不改内容：文本、图片、工具调用、工具结果、用量、结束原因
一一对应。运行时随请求带的两块诊断字段（`dsh_session_log`、
`dsh_plugin_packages`）不进上游——供应商不认识，只会白占 token。
"""

from __future__ import annotations

import json

STOP_REASONS = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
}


def _text_of(content) -> str:
    """system 既可能是字符串，也可能是 text 块数组。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n\n".join(part for part in parts if part)
    return ""


def _image_part(block: dict) -> dict | None:
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    if source.get("type") == "base64" and source.get("data"):
        media = source.get("media_type") or "image/png"
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{media};base64,{source['data']}"},
        }
    if source.get("type") == "url" and source.get("url"):
        return {"type": "image_url", "image_url": {"url": source["url"]}}
    return None


def _user_content(content) -> tuple[list[dict], list[dict]]:
    """用户消息拆成「给 user 的内容块」和「要单独发的工具结果」。"""
    if isinstance(content, str):
        return [{"type": "text", "text": content}], []
    parts: list[dict] = []
    results: list[dict] = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append({"type": "text", "text": str(block.get("text") or "")})
        elif kind == "image":
            part = _image_part(block)
            if part is not None:
                parts.append(part)
        elif kind == "tool_result":
            body = block.get("content")
            text = body if isinstance(body, str) else _text_of(body)
            results.append({
                "role": "tool",
                "tool_call_id": str(block.get("tool_use_id") or ""),
                "content": text,
            })
    return parts, results


def _assistant_message(content) -> dict:
    if isinstance(content, str):
        return {"role": "assistant", "content": content}
    text: list[str] = []
    calls: list[dict] = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            calls.append({
                "id": str(block.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(block.get("name") or ""),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                },
            })
    message: dict = {"role": "assistant"}
    joined = "".join(text)
    if joined:
        message["content"] = joined
    if calls:
        message["tool_calls"] = calls
        message.setdefault("content", "")
    return message


def _tool(spec: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": str(spec.get("name") or ""),
            "description": str(spec.get("description") or ""),
            "parameters": spec.get("input_schema") or {"type": "object", "properties": {}},
        },
    }


def to_chat_request(body: dict) -> dict:
    """Messages 请求 → chat/completions 请求。"""
    messages: list[dict] = []
    system = body.get("system")
    if system:
        text = _text_of(system)
        if text:
            messages.append({"role": "system", "content": text})

    for item in body.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role == "assistant":
            messages.append(_assistant_message(content))
            continue
        parts, results = _user_content(content)
        # 工具结果必须紧跟着那条 assistant 消息，再放用户这一轮的新内容。
        messages.extend(results)
        if parts:
            messages.append({"role": "user", "content": parts})

    request: dict = {
        "model": body.get("model"),
        "messages": messages,
        "stream": True,
    }
    if body.get("max_tokens"):
        request["max_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        request["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        request["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        request["stop"] = body["stop_sequences"]
    tools = [_tool(spec) for spec in body.get("tools") or [] if isinstance(spec, dict)]
    if tools:
        request["tools"] = tools
    return request


def _event(name: str, payload: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False)
    return f"event: {name}\ndata: {body}\n\n".encode()


class MessagesStream:
    """把 chat/completions 的增量改写成 Messages 的事件流。

    文本合成一个 text 块；每个工具调用一个 tool_use 块，参数按
    `input_json_delta` 分片吐出去。
    """

    def __init__(self, model: str = "") -> None:
        self.model = model
        self.buffer = b""
        self.started = False
        self.stopped = False
        self.text_open = False
        self.text_index = 0
        self.next_index = 1
        self.tools: dict[int, int] = {}
        self.stop_reason = "end_turn"
        self.input_tokens = 0
        self.output_tokens = 0

    def _start(self) -> bytes:
        if self.started:
            return b""
        self.started = True
        return _event("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_gateway",
                "type": "message",
                "role": "assistant",
                "model": self.model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })

    def _open_text(self) -> bytes:
        if self.text_open:
            return b""
        self.text_open = True
        return _event("content_block_start", {
            "type": "content_block_start",
            "index": self.text_index,
            "content_block": {"type": "text", "text": ""},
        })

    def feed(self, chunk: bytes) -> bytes:
        frames = (self.buffer + chunk).split(b"\n\n")
        self.buffer = frames.pop()
        out = bytearray(self._start())
        for frame in frames:
            data = b"\n".join(
                line[5:].lstrip() for line in frame.splitlines() if line.startswith(b"data:")
            )
            if not data.strip() or data.strip() == b"[DONE]":
                continue
            try:
                payload = json.loads(data)
            except (ValueError, UnicodeError):
                continue
            out += self._frame(payload)
        return bytes(out)

    def _frame(self, payload: dict) -> bytes:
        out = bytearray()
        usage = payload.get("usage")
        if isinstance(usage, dict):
            self.input_tokens = int(usage.get("prompt_tokens") or self.input_tokens or 0)
            self.output_tokens = int(usage.get("completion_tokens") or self.output_tokens or 0)
        for choice in payload.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if isinstance(text, str) and text:
                out += self._open_text()
                out += _event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": self.text_index,
                    "delta": {"type": "text_delta", "text": text},
                })
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                out += self._tool_delta(call)
            reason = choice.get("finish_reason")
            if isinstance(reason, str) and reason:
                self.stop_reason = STOP_REASONS.get(reason, "end_turn")
        return bytes(out)

    def _tool_delta(self, call: dict) -> bytes:
        out = bytearray()
        index = int(call.get("index") or 0)
        function = call.get("function") or {}
        if index not in self.tools:
            block = self.next_index
            self.tools[index] = block
            self.next_index += 1
            out += _event("content_block_start", {
                "type": "content_block_start",
                "index": block,
                "content_block": {
                    "type": "tool_use",
                    "id": str(call.get("id") or f"tool_{index}"),
                    "name": str(function.get("name") or ""),
                    "input": {},
                },
            })
            self.stop_reason = "tool_use"
        block = self.tools[index]
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments:
            out += _event("content_block_delta", {
                "type": "content_block_delta",
                "index": block,
                "delta": {"type": "input_json_delta", "partial_json": arguments},
            })
        return bytes(out)

    def finish(self) -> bytes:
        if not self.started:
            self._start()
        out = bytearray()
        # 收尾：先把还开着的块关掉，再报结束原因和用量。
        frames = self.feed(b"\n\n")
        out += frames
        if self.text_open:
            out += _event("content_block_stop", {
                "type": "content_block_stop", "index": self.text_index,
            })
            self.text_open = False
        for block in sorted(self.tools.values()):
            out += _event("content_block_stop", {
                "type": "content_block_stop", "index": block,
            })
        self.tools.clear()
        out += _event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": self.stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": self.output_tokens},
        })
        out += _event("message_stop", {"type": "message_stop"})
        self.stopped = True
        return bytes(out)


def to_messages_response(payload: dict, model: str = "") -> dict:
    """非流式的一次性响应（流式走 `MessagesStream`）。"""
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content: list[dict] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError:
            arguments = {}
        content.append({
            "type": "tool_use",
            "id": str(call.get("id") or f"tool_{index}"),
            "name": str(function.get("name") or ""),
            "input": arguments,
        })
    usage = payload.get("usage") or {}
    return {
        "id": "msg_gateway",
        "type": "message",
        "role": "assistant",
        "model": model or payload.get("model") or "",
        "content": content,
        "stop_reason": STOP_REASONS.get(choice.get("finish_reason") or "", "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }
