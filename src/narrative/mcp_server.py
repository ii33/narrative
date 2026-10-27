"""MCP server over stdio: the same tools an LLM can call.

Newline-delimited JSON-RPC 2.0, no Content-Length headers — that is what the
Model Context Protocol's stdio transport expects. Nothing but protocol messages
goes to stdout; diagnostics go to stderr, because a stray print breaks the
client's parser.

Start it with ``--project`` and the tools need no project argument:

    narrative-mcp --project data/graph-projects/example.sqlite3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import tool_api

SERVER_NAME = "narrative-graph"
SERVER_VERSION = "1.0"
DEFAULT_PROTOCOL = "2024-11-05"
PROJECT_ARG = "project"


def _tool_list(default_project: str | None) -> list[dict]:
    tools = tool_api.describe()
    if default_project:
        return tools
    for tool in tools:
        schema = tool["inputSchema"]
        schema["properties"][PROJECT_ARG] = {
            "type": "string",
            "description": "工程 .sqlite3 文件（服务端没有默认工程时必须给）",
        }
        schema["required"] = [*schema.get("required", []), PROJECT_ARG]
    return tools


def _result(payload: Any, *, error: bool = False) -> dict:
    text = payload if isinstance(payload, str) else tool_api.dumps(payload)
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle(request: dict, *, default_project: str | None) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def fail(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    if request_id is None and method and method.startswith("notifications/"):
        return None
    if method == "initialize":
        requested = params.get("protocolVersion") or DEFAULT_PROTOCOL
        return ok({
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "叙事图工作台：读节点、场景、事件的状态与统计，也能补录、更正、失效、"
                "修订节点、审核候选、跑提取。cut 是阅读位置（事件编号），省略为最新。"
            ),
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _tool_list(default_project)})
    if method == "tools/call":
        name = params.get("name")
        arguments = dict(params.get("arguments") or {})
        if not isinstance(name, str):
            return fail(-32602, "tools/call 需要 name")
        project = arguments.pop(PROJECT_ARG, None) or default_project
        try:
            payload = tool_api.call(name, arguments, project=project)
        except tool_api.ToolError as exc:
            return ok(_result(str(exc), error=True))
        except Exception as exc:  # surfaced to the caller instead of dying quietly
            return ok(_result(f"{type(exc).__name__}: {exc}", error=True))
        return ok(_result(payload))
    return fail(-32601, f"不支持的方法：{method}")


def serve(*, default_project: str | None, stdin=None, stdout=None) -> int:
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"忽略不是 JSON 的一行：{exc}", file=sys.stderr)
            continue
        if isinstance(request, list):  # batch
            responses = [
                handle(item, default_project=default_project) for item in request
            ]
            responses = [item for item in responses if item is not None]
        else:
            response = handle(request, default_project=default_project)
            responses = [] if response is None else [response]
        for response in responses:
            sink.write(json.dumps(response, ensure_ascii=False) + "\n")
        sink.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="叙事图 MCP 服务（stdio）")
    parser.add_argument(
        "--project", help="默认工程 .sqlite3；给了之后工具不必再传 project"
    )
    args = parser.parse_args(argv)
    project = str(Path(args.project).resolve()) if args.project else None
    if project and not Path(project).is_file():
        print(f"工程文件不存在：{project}", file=sys.stderr)
        return 2
    return serve(default_project=project)


if __name__ == "__main__":
    raise SystemExit(main())
