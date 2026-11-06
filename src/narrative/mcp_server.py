"""MCP server over stdio: the same tools an LLM can call.

Newline-delimited JSON-RPC 2.0, no Content-Length headers — that is what the
Model Context Protocol's stdio transport expects. Nothing but protocol messages
goes to stdout; diagnostics go to stderr, because a stray print breaks the
client's parser.

Start it with ``--project`` and the tools need no project argument:

    narrative-mcp --project 作品.nproj
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import tool_api
from .assistant_host import HOST_TOOL_NAMES, cancel_host_commands, host_access_context

SERVER_NAME = "narrative-graph"
SERVER_VERSION = "1.0"
DEFAULT_PROTOCOL = "2024-11-05"
PROJECT_ARG = "project"


def _tool_list(default_project: str | None, access_mode="standard") -> list[dict]:
    tools = [row for row in tool_api.describe() if access_mode == "full" or (row["name"] not in HOST_TOOL_NAMES
             and not (tool_api.TOOLS[row['name']].scope == 'configuration' and tool_api.TOOLS[row['name']].writes))]
    if default_project:
        return tools
    for tool in tools:
        if tool_api.TOOLS[tool["name"]].scope == "workspace":
            continue
        schema = tool["inputSchema"]
        schema["properties"][PROJECT_ARG] = {
            "type": "string",
            "description": "工程 .nproj 入口文件（服务端没有默认工程时必须给）",
        }
        schema["required"] = [*schema.get("required", []), PROJECT_ARG]
    return tools


def _result(payload: Any, *, error: bool = False) -> dict:
    text = payload if isinstance(payload, str) else tool_api.dumps(payload)
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle(request: dict, *, default_project: str | None, access_mode="standard", owner=None, cancel_event=None, projects=None) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def fail(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    if not isinstance(params, dict):
        return fail(-32602, "params 须为 JSON 对象")
    if request_id is None and isinstance(method, str) and method.startswith("notifications/"):
        return None
    if method == "initialize":
        requested = params.get("protocolVersion") or DEFAULT_PROTOCOL
        return ok({
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                f"当前访问模式：{access_mode}。完全访问仅由服务启动参数授权，聊天配置不改变本会话权限。"
                "叙事图工作台：读节点、场景、事件的状态与统计，也能补录、更正、失效、"
                "修订节点、自动提交提取；改编与资产使用独立数据和修订。"
                "改编排序用sort_id，写入经Operations校验与审计；cut为提取阅读位置，"
                "改编event_id/moment为改编时点，省略为最新。工程入口是.nproj。"
            ),
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _tool_list(default_project, access_mode)})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return fail(-32602, "arguments 须为 JSON 对象")
        arguments = dict(arguments)
        if not isinstance(name, str):
            return fail(-32602, "tools/call 需要 name")
        project = arguments.pop(PROJECT_ARG, None) or default_project
        try:
            selected_tool = tool_api.TOOLS.get(name)
            if access_mode != "full" and (name in HOST_TOOL_NAMES or selected_tool and selected_tool.scope == 'configuration' and selected_tool.writes):
                raise tool_api.ToolError("当前 MCP 为标准访问；完全访问只能由 --access-mode full 启动授权")
            if name in HOST_TOOL_NAMES:
                if not owner or cancel_event is None or not project:
                    raise tool_api.ToolError("主机调用需要驻留 MCP 会话及工程")
                if projects is not None:
                    projects.add(project)
                with host_access_context(access_mode, owner, cancel_event, project):
                    payload = tool_api.call(name, arguments, project=project)
            else:
                payload = tool_api.call(name, arguments, project=project)
        except tool_api.ToolError as exc:
            return ok(_result(str(exc), error=True))
        except Exception as exc:  # surfaced to the caller instead of dying quietly
            return ok(_result(f"{type(exc).__name__}: {exc}", error=True))
        return ok(_result(payload))
    return fail(-32601, f"不支持的方法：{method}")


def serve(*, default_project: str | None, stdin=None, stdout=None, access_mode="standard") -> int:
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    projects: set[str] = set()
    owner = "mcp-session-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + "-" + str(os.getpid())
    cancel_event = threading.Event()
    session = {"access_mode": access_mode, "owner": owner, "cancel_event": cancel_event, "projects": projects}
    try:
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
                    handle(item, default_project=default_project, **session) for item in request
                ]
                responses = [item for item in responses if item is not None]
            else:
                response = handle(request, default_project=default_project, **session)
                responses = [] if response is None else [response]
            for response in responses:
                sink.write(json.dumps(response, ensure_ascii=False) + "\n")
            sink.flush()
    finally:
        cancel_event.set()
        for project in projects:
            cancel_host_commands(project, owner)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="叙事图 MCP 服务（stdio）")
    parser.add_argument(
        "--project", help="默认工程 .nproj 入口；给了之后工具不必再传 project"
    )
    parser.add_argument("--access-mode", choices=["standard", "full"], default="standard")
    args = parser.parse_args(argv)
    project = str(Path(args.project).resolve()) if args.project else None
    if project and not Path(project).is_file():
        print(f"工程文件不存在：{project}", file=sys.stderr)
        return 2
    return serve(default_project=project, access_mode=args.access_mode)


if __name__ == "__main__":
    raise SystemExit(main())
