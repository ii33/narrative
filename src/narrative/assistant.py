"""常驻协作者：模型提议调工具，程序执行，把结果喂回去。

harness 不开原生工具调用（运行时的 profile 只暴露 `read_image`/`working_directory`），
所以这里沿用同一种桥接：模型每轮回一个 JSON——要么调工具，要么给答案。

工具表直接取自 `tool_api`，而且**只放读操作**：协作者能看、能解释、能对照，
但不能改图。改图仍然是人的动作（接受候选、删除记录、改资料卡）。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path

from . import tool_api

MAX_STEPS = 8
# 图片按 data URI 送进去：运行时走的是 chat-completions，认这个形状。
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp", ".gif": "image/gif"}
# 一次工具结果最多喂回这么多字。超了就让模型自己收窄，而不是悄悄截断 JSON 让它解析失败。
MAX_OBSERVATION = 12000

READ_TOOLS = (
    "project_info",
    "list_moments",
    "list_nodes",
    "get_node",
    "node_properties",
    "scene_state",
    "event_state",
    "statistics",
    "search",
    "list_sources",
    "read_source",
    "property_categories",
    "node_material",
    "audit_properties",
    "list_candidates",
)


def tool_catalog() -> list[dict]:
    """协作者能用的工具：从工具表取，不另写一份。"""
    return [
        {
            "name": name,
            "description": tool_api.TOOLS[name].summary,
            "parameters": tool_api.TOOLS[name].schema(),
        }
        for name in READ_TOOLS
        if name in tool_api.TOOLS
    ]


def _catalog_lines() -> str:
    lines = []
    for tool in tool_catalog():
        schema = tool["parameters"]
        required = set(schema.get("required") or [])
        parts = []
        for key, spec in (schema.get("properties") or {}).items():
            kind = spec.get("type") or "string"
            parts.append(f"{key}{'' if key in required else '?'}:{kind}")
        lines.append(f"- {tool['name']}（{tool['description']}）参数：{', '.join(parts) or '无'}")
    return "\n".join(lines)


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


def _first_prompt(message: str, image: str = "") -> str:
    image_note = (
        f"\n\n（用户附了一张图：{Path(image).name}。图就在这条消息里，你已经看到了——"
        "先看图再回答，不要说自己看不到图，也不要试图用工具去找它。）"
        if image else ""
    )
    return f"""你是这个叙事图工程的协作者。你只能读，不能改：没有任何写工具，也不要假装改过。

可用工具（`?` 表示可选参数）：
{_catalog_lines()}

每一轮只输出一个 JSON，前后不要有别的话：
- 要查东西：{{"tool": "<工具名>", "args": {{...}}}}
- 要回答：{{"answer": "<给用户的话>"}}

规矩：
1. 先查再说。涉及「哪一章、哪一步、谁说了什么」的问题，先 `list_nodes` 或 `search`，不要凭印象。
2. 编号就是编号：`P3` 是第三个人、`E12` 是第十二个事件，每种类型各算各的，不要换算。
3. 引用原文要给 `source_id` 和字符位置（`read_source` 能读一段）。
4. 查不到就说查不到，不要补一个像样的答案。
5. **筛选类问题先定判据**：「谁出现在哪些场景」「哪些事件涉及某物」这类，要用
   `scene_state`（这一场谁在场）或 `list_nodes` 里事件的 `participants` 去筛，
   不要把全部场景列一遍；回答时说明你用的是什么判据。
6. 回答用中文，简短。可以用 Markdown（`-` 列表、**粗体**、`代码`、`>` 引用），
   不要用 HTML 表格或复杂排版——面板按 Markdown 渲染。

用户说：{message}{image_note}"""


def _parse(raw: str) -> dict:
    """模型偶尔会带围栏或解释，尽量把那个 JSON 抠出来。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {"answer": text}
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {"answer": text}
    return payload if isinstance(payload, dict) else {"answer": text}


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
    if name == "read_source":
        return f"{len(str(result.get('text') or ''))} 字"
    if name == "statistics":
        return f"{result.get('node_total')} 个节点"
    if name == "list_sources":
        return f"{len(result.get('sources') or [])} 篇原文"
    return "ok"


def _observation(step: dict) -> str:
    if not step.get("ok"):
        return (
            "工具调用失败：" + str(step.get("error") or "未知原因")
            + "\n换个工具或参数再试，或者直接说明查不到。"
        )
    text = json.dumps(step.get("result"), ensure_ascii=False)
    if len(text) > MAX_OBSERVATION:
        text = text[:MAX_OBSERVATION] + "…（结果太长，已截断；加 limit 或缩小范围再查）"
    return "工具结果：\n" + text + "\n继续：要么再调一个工具，要么给出 answer。"


def _call(project: str | Path, name: str, args: dict) -> dict:
    if name not in READ_TOOLS:
        return {
            "tool": name,
            "args": args,
            "ok": False,
            "error": f"「{name}」不在协作者能用的范围里（只能读）",
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


def run_turn(
    ask: Callable[[str | list[dict]], str],
    message: str,
    *,
    project: str | Path,
    image: str = "",
    max_steps: int = MAX_STEPS,
) -> dict:
    """跑一轮对话：模型可以连着调几次读工具，最后给答案。

    `ask(instruction) -> str` 是模型的一次回复。测试里给个脚本化的假实现就能跑，
    不用联网。
    """
    prompt = _first_prompt(message, image)
    block = image_block(image) if image else None
    # 有图就发内容块（文字 + 图片），没有就发纯文字——运行时两种都收。
    instruction: str | list[dict] = [{"type": "text", "text": prompt}, block] if block else prompt
    steps: list[dict] = []
    for round_index in range(max_steps):
        raw = ask(instruction)
        payload = _parse(raw)
        name = str(payload.get("tool") or "").strip()
        if not name:
            answer = str(payload.get("answer") or raw or "").strip()
            return {"answer": answer, "steps": _public(steps), "rounds": round_index + 1}
        step = _call(project, name, payload.get("args") or {})
        steps.append(step)
        instruction = _observation(step)
    return {
        "answer": f"（已经连着查了 {max_steps} 次，先停在这里。可以让我接着查，或者把问题缩小一点。）",
        "steps": _public(steps),
        "rounds": max_steps,
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
