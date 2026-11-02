"""CLI door onto the same tool list the MCP server exposes.

Two shapes, on purpose:

- ``narrative tool list`` / ``narrative tool call <name> '<json>'`` — the generic
  pair, always in step with the tool registry.
- ``narrative nodes`` / ``narrative add-property`` … — named aliases with real
  flags, for a person typing at a shell.

Both end in ``tool_api.call``, so they cannot drift apart.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import tool_api
from .errors import OperationError

# name → (tool, [(flag, param, type, help)])
ALIASES: dict[str, tuple[str, list[tuple[str, str, type | str, str]]]] = {
    "projects": ("list_projects", [
        ("--dir", "dir", str, "工程目录，默认 data/graph-projects"),
    ]),
    "info": ("project_info", []),
    "nodes": ("list_nodes", [
        ("--granularity", "granularity", str, "scene / event"),
        ("--cut", "cut", str, "阅读位置（事件编号）"),
        ("--type", "node_type", str, "person/group/item/location/event/scene"),
        ("--query", "query", str, "名称或说明里的关键字"),
        ("--related", "related_only", "flag", "只看这一步相关的节点"),
        ("--limit", "limit", int, "最多返回多少条"),
    ]),
    "node": ("get_node", [
        ("node_id", "node_id", str, "节点编号"),
        ("--cut", "cut", str, "阅读位置"),
    ]),
    "props": ("node_properties", [
        ("node_id", "node_id", str, "节点编号"),
        ("--cut", "cut", str, "阅读位置"),
    ]),
    "scene": ("scene_state", [
        ("scene_id", "scene_id", str, "场景编号"),
    ]),
    "event": ("event_state", [
        ("event_id", "event_id", str, "事件编号"),
    ]),
    "stats": ("statistics", []),
    "search": ("search", [
        ("query", "query", str, "关键字"),
        ("--limit", "limit", int, "默认 50"),
    ]),
    "sources": ("list_sources", []),
    "read-source": ("read_source", [
        ("source_id", "source_id", str, "原文编号"),
        ("--start", "start", int, "起始字符"),
        ("--length", "length", int, "读取多少字"),
    ]),
    "audit": ("audit_properties", [
        ("--node", "node_ids", "list", "只审这些节点（可重复）"),
    ]),
    "add-property": ("add_property", [
        ("node_id", "node_id", str, "节点编号"),
        ("property_id", "property_id", str, "属性名"),
        ("value", "value", str, "值"),
        ("event_id", "event_id", str, "披露事件编号"),
        ("--category", "category", str, "显示大类"),
        ("--source", "source", str, "narration/speech/thought/observation"),
        ("--scope", "scope", str, "world/cognition/claim"),
        ("--holder", "holder_id", str, "持有人"),
        ("--quote", "quote", str, "原文引文"),
        ("--source-id", "source_id", str, "引文所在原文"),
    ]),
    "correct-property": ("correct_property", [
        ("record_id", "record_id", str, "要更正的取值记录编号"),
        ("value", "value", str, "正确的值"),
        ("--event", "event_id", str, "新记录的披露事件"),
        ("--reason", "reason", str, "更正原因"),
        ("--quote", "quote", str, "原文引文"),
        ("--source-id", "source_id", str, "引文所在原文"),
    ]),
    "expire-properties": ("expire_properties", [
        ("--record-ids", "record_ids", "list", "旧取值记录编号"),
        ("source_id", "source_id", str, "有效期结束所在原文"),
        ("start", "start", int, "Unicode字符偏移"),
        ("reason", "reason", str, "状态变化原因"),
        ("--event", "event_id", str, "披露事件"),
    ]),
    "delete-property-records": ("delete_property_records", [
        ("--record-ids", "record_ids", "list", "错误记录编号"),
        ("reason", "reason", str, "删除原因"),
    ]),
    "rollback-preview": ("rollback_preview", [
        ("source_id", "source_id", str, "保留到的原文"),
        ("start", "start", int, "保留到的Unicode字符偏移"),
        ("expected_revision", "expected_revision", int, "当前修订号"),
    ]),
    "rollback-to": ("rollback_to", [
        ("source_id", "source_id", str, "保留到的原文"),
        ("start", "start", int, "保留到的Unicode字符偏移"),
        ("expected_revision", "expected_revision", int, "当前修订号"),
        ("reason", "reason", str, "回退原因"),
    ]),
    "revise-node": ("revise_node", [
        ("node_id", "node_id", str, "节点编号"),
        ("--name", "name", str, "新名称"),
        ("--type", "node_type", str, "新类型"),
        ("--alias", "aliases", "list", "别名（可重复）"),
        ("--note", "identity_note", str, "身份说明"),
    ]),
    "merge-properties": ("merge_properties", [
        ("source_id", "source_id", str, "要合并的属性定义"),
        ("target_id", "target_id", str, "目标属性定义"),
        ("reason", "reason", str, "合并原因"),
        ("expected_revision", "expected_revision", int, "当前修订号"),
    ]),
    "rename-property": ("rename_property", [
        ("from_id", "from_id", str, "原属性名"),
        ("to_id", "to_id", str, "新属性名"),
        ("reason", "reason", str, "改名原因"),
        ("expected_revision", "expected_revision", int, "当前修订号"),
    ]),
    "categorize": ("categorize_records", [
        ("--ids", "ids", "list", "记录编号（可重复）"),
        ("category", "category", str, "大类名"),
    ]),
    "moments": ("list_moments", []),
    "material": ("node_material", [
        ("node_id", "node_id", str, "节点编号"),
        ("--cut", "cut", str, "阅读位置（事件编号）"),
    ]),
    "add-material": ("add_material_card", [
        ("node_id", "node_id", str, "节点编号"),
        ("title", "title", str, "标题"),
        ("--detail", "detail", str, "详情"),
        ("--from", "valid_from", str, "生效时间点（场景或事件编号）"),
        ("--to", "valid_to", str, "失效时间点；空为一直有效"),
    ]),
    "update-material": ("update_material_card", [
        ("node_id", "node_id", str, "节点编号"),
        ("card_id", "card_id", str, "资料编号"),
        ("--title", "title", str, "新标题"),
        ("--detail", "detail", str, "新详情"),
        ("--from", "valid_from", str, "新生效时间点"),
        ("--to", "valid_to", str, "新失效时间点"),
    ]),
    "delete-material": ("delete_material_card", [
        ("node_id", "node_id", str, "节点编号"),
        ("card_id", "card_id", str, "资料编号"),
    ]),
    "add-material-image": ("add_material_image", [
        ("node_id", "node_id", str, "节点编号"),
        ("card_id", "card_id", str, "资料编号"),
        ("path", "path", str, "图片文件"),
        ("--caption", "caption", str, "一句说明"),
    ]),
    "delete-material-image": ("delete_material_image", [
        ("node_id", "node_id", str, "节点编号"),
        ("card_id", "card_id", str, "资料编号"),
        ("image_id", "image_id", str, "图片编号"),
    ]),
    "import-source": ("import_source", [
        ("title", "title", str, "原文标题"),
        ("--text", "text", str, "正文（短文本直接给）"),
        ("--file", "text", "file", "从文件读正文；长文本用这个"),
    ]),
    "categories": ("property_categories", []),
}


def add_commands(subparsers) -> None:
    tool = subparsers.add_parser("tool", help="工具面：列出、直接调用（与 MCP 同一套）")
    tool.add_argument("action", choices=["list", "call"], help="list 列出；call 调用")
    tool.add_argument("name", nargs="?", help="call 时给工具名")
    tool.add_argument("params", nargs="?", help="call 时的 JSON 参数")
    tool.add_argument("--project", help="工程 .nproj")
    tool.add_argument("--json", action="store_true", help="原样输出 JSON")

    for name, (tool_name, spec) in ALIASES.items():
        parser = subparsers.add_parser(name, help=tool_api.TOOLS[tool_name].summary)
        for flag, param, kind, help_text in spec:
            kwargs: dict = {"help": help_text}
            if kind == "flag":
                kwargs["action"] = "store_true"
            elif kind == "list":
                kwargs["action"] = "append"
            elif kind == "file":
                kwargs["type"] = str
            else:
                assert not isinstance(kind, str), kind
                kwargs["type"] = kind
            if flag.startswith("-"):
                parser.add_argument(flag, dest=param, **kwargs)
            else:  # 位置参数：dest 由名字决定，不能再传
                parser.add_argument(flag, **kwargs)
        parser.add_argument("--project", help="工程 .nproj")
        parser.add_argument("--json", action="store_true", help="原样输出 JSON")


def _bind(args, spec) -> dict:
    params: dict = {}
    for _flag, param, kind, _help in spec:
        value = getattr(args, param, None)
        if value in (None, [], False):
            continue
        if kind == "list" and not isinstance(value, list):
            value = [value]
        if kind == "file":
            # 长文本从文件读：命令行塞不下五千字的一章。
            value = Path(str(value)).read_text(encoding="utf-8")
        params[param] = value
    return params


def run(args) -> int:
    if args.command == "tool":
        if args.action == "list":
            rows = tool_api.describe()
            if args.json:
                print(tool_api.dumps(rows))
            else:
                for row in rows:
                    print(f"{row['name']:<22} {row['description']}")
            return 0
        if not args.name:
            raise OperationError("tool call 需要工具名")
        params = json.loads(args.params) if args.params else {}
        if not isinstance(params, dict):
            raise OperationError("参数须为 JSON 对象")
        payload = tool_api.call(args.name, params, project=args.project)
        print(tool_api.dumps(payload))
        return 0

    tool_name, spec = ALIASES[args.command]
    params = _bind(args, spec)
    payload = tool_api.call(tool_name, params, project=args.project)
    if args.json:
        print(tool_api.dumps(payload))
        return 0
    print(_summary(args.command, payload))
    return 0


def _summary(command: str, payload: dict) -> str:
    """Human-first default output; --json gives the whole thing."""
    if command == "projects":
        rows = payload.get("projects", [])
        if not rows:
            return f"{payload.get('directory')}：没有工程"
        return "\n".join(
            f"{row.get('title') or row.get('name')}  {row.get('status')}  "
            f"修订 {row.get('revision')}  {row.get('path')}"
            for row in rows
        )
    if command in {"nodes"}:
        counts = payload.get("counts") or {}
        head = "  ".join(f"{key} {value}" for key, value in counts.items())
        rows = "\n".join(
            f"{node.get('id'):>4}  {node.get('node_type'):<8} {node.get('name')}"
            for node in payload.get("nodes", [])
        )
        return f"{head}\n{rows}" if rows else head
    if command in {"node", "props", "scene", "event"}:
        node = payload.get("node") or payload.get("scene") or payload.get("event") or {}
        lines = [f"{node.get('id')}  {node.get('node_type')}  {node.get('name')}"]
        if payload.get("cut"):
            lines.append(f"读到 {payload['cut']}")
        rows = payload.get("properties") or payload.get("state") or []
        for row in rows:
            if "properties" in row:
                lines.append(f"[{row['name']}]")
                lines.extend(
                    f"  {item['label']} = {item['value']}" for item in row["properties"]
                )
            else:
                lines.append(f"  {row['label']} = {row['value']}")
        return "\n".join(lines)
    if command == "stats":
        return tool_api.dumps(payload)
    return tool_api.dumps(payload)


def main(argv: list[str] | None = None) -> int:
    """Standalone entry point (``narrative-tools``); the main CLI calls run()."""
    parser = argparse.ArgumentParser(description="叙事图工具面")
    commands = parser.add_subparsers(dest="command", required=True)
    add_commands(commands)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OperationError, tool_api.ToolError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
