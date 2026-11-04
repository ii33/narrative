"""Project assistant with native Operations tools and persistent conversations.

The same agent reads, extracts, validates and submits facts through Operations.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterable
from pathlib import Path

from . import tool_api
from .harness import CONVERSATION_SYSTEM_PROMPT

ASSISTANT_VERSION = "conversation-agent/26"
ASSISTANT_SYSTEM_PROMPT = CONVERSATION_SYSTEM_PROMPT
# 图片按 data URI 送进去：运行时走的是 chat-completions，认这个形状。
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp", ".gif": "image/gif"}
# Every project-local capability uses the same public Operations contract.
# Workspace enumeration and any future non-project capability remain outside.
ASSISTANT_TOOLS = tuple(name for name, tool in tool_api.TOOLS.items() if tool.scope == "project")
READ_TOOLS = tuple(name for name in ASSISTANT_TOOLS if not tool_api.TOOLS[name].writes)


def tool_catalog(names: Iterable[str] | None = None) -> list[dict]:
    """协作者能用的工具：从工具表取，不另写一份。"""
    selected = set(names) if names is not None else None
    return [
        {
            "name": name,
            "description": tool_api.TOOLS[name].summary,
            "parameters": tool_api.TOOLS[name].schema(),
        }
        for name in ASSISTANT_TOOLS
        if name in tool_api.TOOLS and (selected is None or name in selected)
    ]


def image_block(path: str | Path) -> dict | None:
    """把一张图变成内容块；格式不收或太大就返回 None（当作没附图）。"""
    file = Path(path)
    if not file.is_file():
        return None
    mime = IMAGE_SUFFIXES.get(file.suffix.lower())
    if mime is None or file.stat().st_size > MAX_IMAGE_BYTES:
        return None
    data = base64.b64encode(file.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}






def _summary(name: str, result: dict) -> str:
    """给界面的一行人话；完整结果只喂给模型。"""
    if not isinstance(result, dict):
        return "ok"
    if name in {"list_nodes", "search"}:
        nodes = result.get("nodes")
        if isinstance(nodes, list):
            return f"{len(nodes)} 个节点"
        hits = (result.get("result") or {}).get("nodes")
        if isinstance(hits, list):
            return f"{len(hits)} 个节点"
    if name in {"node_properties", "node_material"}:
        rows = result.get("properties") or result.get("cards")
        if isinstance(rows, list):
            return f"{len(rows)} 条"
    if name == "get_node":
        node = result.get("node") or {}
        return f"{node.get('label_id') or node.get('id')} {node.get('name') or ''}".strip()
    if name in {"scene_state", "event_state"}:
        return f"{len(result.get('state') or [])} 个节点有状态"
    if name in {"read_source", "read_source_slice"}:
        return f"{len(str(result.get('text') or ''))} 字"
    if name in {"preview_pattern_slices", "create_pattern_slices"}:
        return f"选取 {result.get('selected_count', 0)} 章 / 共匹配 {result.get('match_count', 0)} 个标题"
    if name in {"list_source_slices", "create_source_slices"}:
        return f"{len(result.get('slices') or [])} 个切片"
    if name == "statistics":
        return f"{result.get('node_total')} 个节点"
    if name == "list_sources":
        return f"{len(result.get('sources') or [])} 篇原文"
    return "ok"




def _call(project: str | Path, name: str, args: dict) -> dict:
    if name not in ASSISTANT_TOOLS:
        return {
            "tool": name,
            "args": args,
            "ok": False,
            "error": f"「{name}」不属于当前工程工具；不开放外部工程管理、shell或rawSQL",
        }
    try:
        result = tool_api.call(name, args, project=project)
    except tool_api.ToolError as exc:
        return {"tool": name, "args": args, "ok": False, "error": str(exc)}
    return {
        "tool": name,
        "args": args,
        "ok": True,
        "summary": _summary(name, result),
        "result": result,
    }






def _public(steps: list[dict]) -> list[dict]:
    """给界面的步骤：不带完整结果，只留一行摘要。"""
    return [
        {
            "tool": step["tool"],
            "args": step.get("args") or {},
            "ok": step.get("ok", False),
            "summary": step.get("summary") or step.get("error") or "",
        }
        for step in steps
    ]


def _first_prompt(message: str, image: str = "", *, extraction_granularity: str = "fine", prompt_config=None, project_style=None) -> str:
    if extraction_granularity not in {"standard", "fine"}:
        raise ValueError("提取粒度须为standard或fine")
    from .prompt_defaults import DEFAULT_PROMPTS
    texts = prompt_config["texts"] if prompt_config is not None else {key: row["text"] for key, row in DEFAULT_PROMPTS.items()}
    image_note = "\n用户附图已随本条输入提供。" if image else ""
    style_note = "\n当前工程风格约定：\n" + json.dumps(project_style, ensure_ascii=False) if project_style is not None else ""
    return (texts["extraction"] + "\n" + texts["review"] + "\n" + texts["event_merge_review"] + "\n当前工程提取粒度：" + extraction_granularity + "\n"
            + texts["granularity_" + extraction_granularity] + style_note + "\n用户说：" + message + image_note)


def run_turn(ask: Callable[[str | list[dict]], str], message: str, *, project: str | Path,
             image: str = "", steps: list[dict] | None = None,
             on_format_error: Callable[[dict], None] | None = None,
             extraction_granularity: str | None = None, prompt_config=None, project_style=None) -> dict:
    """One task, including model-authored repairs, until completion or cancellation."""
    from .conversation_session import IncompleteConversation

    if extraction_granularity is None or prompt_config is None or project_style is None:
        from .operations import Operations
        from .prompt_settings import prompt_snapshot
        ops = Operations.open(project)
        if extraction_granularity is None:
            extraction_granularity = ops.load_project_settings()["extraction_granularity"]
        if prompt_config is None:
            prompt_config = prompt_snapshot(ops)
        if project_style is None:
            project_style = ops.load_project_style()
    if not isinstance(extraction_granularity, str):
        raise ValueError("工程提取粒度须为standard或fine")
    prompt = _first_prompt(message, image, extraction_granularity=extraction_granularity, prompt_config=prompt_config, project_style=project_style)
    block = image_block(image) if image else None
    instruction = [{"type": "text", "text": prompt}, block] if block else prompt
    repairs: list[dict] = []
    while True:
        try:
            raw = ask(instruction)
        except IncompleteConversation as exc:
            if not exc.recoverable:
                raise
            repair = {"attempt": len(repairs) + 1,
                      "diagnostic_path": exc.diagnostic_path, "code": exc.code}
            repairs.append(repair)
            if on_format_error is not None:
                on_format_error(repair | {"feedback": exc.feedback, "metadata": exc.metadata})
            # A new correction instruction in the same session, never a replay
            # of the failed request or a host-edited tool argument.
            instruction = exc.feedback
            continue
        return {"answer": str(raw or ""), "steps": _public(steps or []),
                "task_version": ASSISTANT_VERSION, "format_repairs": repairs, "prompt_config": prompt_config, "project_style": project_style}
