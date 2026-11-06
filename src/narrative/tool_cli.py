"""CLI door onto the same tool list the MCP server exposes.

Two shapes, on purpose:

- ``narrative tool list`` / ``narrative tool call <name> '<json>'`` — the generic
  pair, always in step with the tool registry.
- ``narrative nodes`` / ``narrative record-property-change`` … — named aliases with real
  flags, for a person typing at a shell.

Both end in ``tool_api.call``, so they cannot drift apart.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from . import tool_api
from .assistant_host import HOST_TOOL_NAMES, cancel_host_commands, host_access_context
from .errors import OperationError

# name → (tool, [(flag, param, type, help)])
ALIASES: dict[str, tuple[str, list[tuple[str, str, type | str, str]]]] = {
    'content-submissions': ('list_content_submissions', [
        ('--operation', 'operation', str, '只列指定业务操作的未完成暂存'),
        ('--target', 'target', 'json', '可选目标过滤JSON'),
        ('--limit', 'limit', int, '最多返回100项，默认20'),
        ('--after', 'after', int, '上一页next_after'),
    ]),
    'content-submission': ('read_content_submission', [
        ('submission_id', 'submission_id', str, '业务暂存编号'),
        ('--paths', 'paths', 'json', '只读取指定字段路径数组，默认摘要'),
    ]),
    'content-patch': ('patch_content_submission', [
        ('submission_id', 'submission_id', str, '业务暂存编号'),
        ('expected_revision', 'expected_revision', int, '暂存submission_revision'),
        ('--patches', 'patches', 'json', 'set/remove/append局部字段操作数组'),
        ('--reason', 'reason', str, '修正依据'),
    ]),
    "project-copy": ("duplicate_project", [
        ("source", "source", str, "源工程.nproj入口"),
        ("destination", "destination", str, "副本.nproj入口（不能覆盖已有文件）"),
    ]),
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
    "record-property-change": ("record_property_change", [
        ("node_id", "node_id", str, "主体编号"),
        ("property_id", "property_id", str, "属性名"),
        ("event_id", "event_id", str, "变更事件编号"),
        ("op", "op", str, "set/add/remove/clear/unknown"),
        ("--value", "value", "json", "set/add/remove的JSON值；clear/unknown省略"),
        ("--value-type", "value_type", str, "新定义的值类型"),
        ("--cardinality", "cardinality", str, "one标量/many集合数组"),
        ("--symmetric", "symmetric", "bool", "是否对称"),
        ("--scope", "scope", str, "world/cognition/claim"),
        ("--holder", "holder_id", str, "认知者或说话人"),
        ("--context", "context_id", str, "语境ID"),
        ("--quote", "quote", str, "准确原文引文"),
        ("--source-id", "source_id", str, "引文原文"),
        ("expected_revision", "expected_revision", int, "当前图修订"),
        ("reason", "reason", str, "变更依据"),
    ]),
    "correct-property": ("correct_property", [
        ("record_id", "record_id", str, "错误变更记录编号"),
        ("--changes", "changes", "json", "要更正的字段JSON对象；sequence保留"),
        ("expected_revision", "expected_revision", int, "当前图修订"),
        ("reason", "reason", str, "更正原因"),
    ]),
    "delete-property-records": ("delete_property_records", [
        ("--record-ids", "record_ids", "list", "错误变更记录编号"),
        ("expected_revision", "expected_revision", int, "当前图修订"),
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

_REVISION: list[tuple[str, str, type | str, str]] = [("--expected-revision", "expected_revision", int, "当前修订号")]
_REASON: list[tuple[str, str, type | str, str]] = [("--reason", "reason", str, "操作原因")]
_PLACEMENT: list[tuple[str, str, type | str, str]] = [("--after", "after_id", str, "放在此节点后；省略或空字符串使用默认位置（移动为开头，复制在原节点后）"),
              ("--parent", "parent_id", str, "归属节点；省略或空字符串保留原归属")]
_NODE: list[tuple[str, str, type | str, str]] = [("node_id", "node_id", str, "改编节点编号")]
ALIASES.update({
    "adaptation-import-candidates": ("import_adaptation_candidates", []),
    "adaptation-import": ("import_adaptation", [
        ("--moment", "moment_ids", "list", "选择提取时间点ID，可重复；含必要结构及相关状态"),
    ] + _REVISION + _REASON),
    "adaptation-browse": ("adaptation_browse_view", [
        ("--granularity", "granularity", str, "scene / event / action"),
        ("--cut", "cut_id", str, "改编阅读时点"), ("--type", "node_type", str, "节点类型"),
        ("--query", "query", str, "名称检索"), ("--related-only", "related_only", "bool", "只看时点相关节点"),
        ("--time-filter", "time_filter", "bool", "按改编时点过滤"),
        ("--include-suppressed", "include_suppressed", "bool", "包含已屏蔽节点以便管理"),
    ]),
    "adaptation-properties": ("adaptation_node_properties", _NODE + [("--cut", "cut", str, "改编时点")]),
    "adaptation-editor": ("adaptation_node_editor", _NODE + [("--cut", "cut", str, "改编时点")]),
    "adaptation-detail": ("adaptation_node_detail", _NODE + [("--cut", "cut", str, "改编时点")]),
    "adaptation-assets": ("adaptation_node_assets", _NODE + [("--cut", "cut", str, "改编时点")]),
    "extraction-settings": ("get_extraction_settings", []),
    "extraction-granularity": ("set_extraction_granularity", [
        ("granularity", "granularity", str, "standard / fine（默认精细）"),
    ]),
    "adaptation": ("adaptation_snapshot", []),
    "adaptation-check": ("check_adaptation", []),
    "adaptation-delete": ("delete_adaptation_node", _NODE + _REVISION + _REASON),
    "adaptation-sync": ("sync_adaptation", _REVISION + _REASON),
    "adaptation-edit": ("edit_adaptation_node", [
        ("--node", "node_id", str, "已有改编节点；省略则新增"),
        ("--changes", "changes", "json", "节点字段 JSON 对象；也可用 --changes-file"),
    ] + _PLACEMENT + _REVISION + _REASON),
    "adaptation-copy": ("copy_adaptation_node", _NODE + _PLACEMENT + _REVISION + _REASON),
    "adaptation-move": ("move_adaptation_node", _NODE + _PLACEMENT + _REVISION + _REASON),
    "adaptation-suppress": ("suppress_adaptation_node", _NODE + [
        ("--suppressed", "suppressed", "bool", "true 屏蔽节点及子树，false 恢复"),
    ] + _REVISION + _REASON),
    "adaptation-property": ("set_adaptation_property", _NODE + [
        ("property_id", "property_id", str, "属性名称"),
        ("--value", "value", "json", "属性值 JSON；字符串需加 JSON 引号；也可 --value-file"),
        ("event_id", "event_id", str, "改编变更事件"),
        ("op", "op", str, "set/add/remove/clear/unknown"),
        ("--record", "record_id", str, "更正错误的变更记录"),
    ] + _REVISION + _REASON),
    "assets": ("list_assets", [
        ("category", "category", str, "appearance / item / location / material"),
        ("--moment", "moment", str, "改编时点；省略查看当前有效设定"),
        ("--node", "node_id", str, "角色、物品或地点编号"),
        ("--domain", "domain", str, "adaptation / extraction"),
    ]),
    "asset": ("get_asset", [("asset_id", "asset_id", str, "资产编号")]),
    "asset-save": ("save_asset", [
        ("--payload", "payload", "json", "资产设定 JSON 对象；也可 --payload-file"),
    ] + _REVISION),
    "asset-delete": ("delete_asset", [("asset_id", "asset_id", str, "资产编号")] + _REVISION),
    "asset-moments": ("asset_moments", [("--domain", "domain", str, "adaptation / extraction")]),
})


# All screenplay flags derive from the shared tool schema, including JSON/file
# inputs, so CLI, MCP and the resident model cannot gain divergent parameters.
_SCREENPLAY_ALIASES = {
    "extraction-submissions": "list_extraction_submissions", "extraction-submission": "read_extraction_submission", "extraction-patch": "patch_extraction_submission",
    "project-style": "load_project_style", "project-style-save": "save_project_style",
    "creative-agreement": "load_creative_agreement", "creative-agreement-save": "save_creative_agreement",
    "prompt-settings": "load_prompt_settings", "prompt-override": "save_prompt_override",
    "screenplay-storage": "inspect_screenplay_storage", "screenplay-storage-reset": "reset_screenplay_storage",
    "screenplay-timeline-candidates": "import_screenplay_timeline_candidates",
    "screenplay-timeline": "read_screenplay_timeline", "screenplay-timeline-import": "import_screenplay_timeline",
    "screenplay-timeline-append": "append_screenplay_timeline",
    "screenplay-timeline-edit": "edit_screenplay_timeline_node",
    "screenplay-timeline-copy": "copy_screenplay_timeline_node", "screenplay-timeline-delete": "delete_screenplay_timeline_node", "screenplay-timeline-move": "move_screenplay_timeline_node",
    "screenplay-timeline-suppress": "suppress_screenplay_timeline_node", "screenplay-timeline-property": "set_screenplay_timeline_property",
    "span-preview": "preview_adaptation_span", "span-create": "create_adaptation_span",
    "span-update": "update_adaptation_span", "span": "read_adaptation_span",
    "screenplay-create": "create_screenplay", "screenplay": "read_screenplay",
    "screenplay-structure": "apply_screenplay_structure", "script-segment-create": "create_script_segment",
    "script-segment-update": "update_script_segment", "script-segment": "read_script_segment",
    "scene-table-context": "read_scene_table_context", "scene-table-submit": "submit_scene_table",
    "scene-table-edit": "apply_scene_table_changes", "scene-table-adopt": "adopt_scene_table",
    "script-unit-context": "read_script_unit_context", "script-unit-submit": "submit_script_unit",
    "script-block-edit": "apply_script_block_changes", "writing-basis-current": "mark_writing_basis_current",
    "script-writing-targets": "prepare_script_writing_targets", "script-writing-targets-check": "read_script_writing_targets",
    "script-writing-task": "prepare_script_writing_task", "screenplay-check": "check_screenplay",
    "screenplay-export": "export_screenplay",
    "screenplay-compile": "compile_screenplay",
    "compile-settings": "load_compile_settings", "compile-settings-save": "save_compile_settings",
    "storyboard-render-style": "render_storyboard_style", "storyboard-sketch": "generate_storyboard_sketch", "storyboard-style-package": "export_storyboard_style_package",
    "storyboard-script-export": "export_storyboard_script",
    "storyboard": "read_storyboard", "storyboard-preview": "preview_storyboard", "storyboard-apply": "apply_storyboard_changes",
    "storyboard-check": "check_storyboard",
    "storyboard-preview-changes": "preview_storyboard_changes",
    "storyboard-camera-presets": "load_storyboard_camera_presets", "storyboard-camera-preset-save": "save_storyboard_camera_preset",
    "storyboard-video-settings": "read_storyboard_video_settings", "storyboard-video-settings-save": "save_storyboard_video_settings",
    "storyboard-video-receipts": "read_storyboard_video_receipts", "storyboard-animation": "set_storyboard_animation",
    "storyboard-animatic-export": "render_storyboard_animatic", "storyboard-video-attach": "attach_storyboard_video",
    "storyboard-video-detach": "detach_storyboard_video", "storyboard-video-prepare": "prepare_storyboard_video",
    "storyboard-video-submit": "submit_storyboard_video", "storyboard-video-refresh": "refresh_storyboard_video",
    "usage-statistics": "read_usage_statistics",
    "task-write-summary": "read_task_write_summary",
    "assistant-access": "read_assistant_access", "assistant-access-set": "set_assistant_access",
    "chat-attachment-import": "import_chat_attachments",
    "chat-attachments": "list_chat_attachments", "chat-attachment-read": "read_chat_attachment",
    "host-read": "read_host_file", "host-write": "write_host_file", "host-list": "list_host_directory",
    "host-run": "run_host_command", "host-output": "read_host_command", "host-stop": "stop_host_command",
}
for _alias, _tool in _SCREENPLAY_ALIASES.items():
    _spec: list[tuple[str, str, type | str, str]] = []
    for _key, _field in tool_api.TOOLS[_tool].params.items():
        _kind = _field.get("type")
        if isinstance(_kind, list):
            _kind = next((k for k in _kind if k != "null"), None)
        _parser_types: dict[str, type | str] = {"string": str, "integer": int, "number": float, "boolean": "bool"}
        _parser_kind = _parser_types.get(str(_kind), "json")
        _spec.append(("--" + _key.replace("_", "-"), _key, _parser_kind, _field.get("description", _key)))
    ALIASES[_alias] = (_tool, _spec)


def _boolean(text: str) -> bool:
    if text.lower() in {"true", "1", "yes"}:
        return True
    if text.lower() in {"false", "0", "no"}:
        return False
    raise argparse.ArgumentTypeError("须为 true 或 false")


def _json_value(text: str, label: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise OperationError(f"{label} 不是合法 JSON：{exc.msg}") from None


def add_commands(subparsers) -> None:
    credential = subparsers.add_parser('project-api-key', help='当前工程本机密钥：查看状态、从stdin保存或清除，不回显密钥')
    credential.add_argument('credential_action', choices=['status', 'set', 'clear', 'check'])
    credential.add_argument('--project', required=True, help='工程 .nproj')
    credential.add_argument('--key-stdin', action='store_true', help='set时从stdin读取一行密钥，不把密钥写进命令参数')
    credential.add_argument('--json', action='store_true')
    tool = subparsers.add_parser("tool", help="工具面：列出、直接调用（与 MCP 同一套）")
    tool.add_argument("action", choices=["list", "call"], help="list 列出；call 调用")
    tool.add_argument("name", nargs="?", help="call 时给工具名")
    tool.add_argument("params", nargs="?", help="call 时的 JSON 参数")
    tool.add_argument("--project", help="工程 .nproj")
    tool.add_argument("--json", action="store_true", help="原样输出 JSON")

    for name, (tool_name, spec) in ALIASES.items():
        parser = subparsers.add_parser(name, help=tool_api.TOOLS[tool_name].summary)
        for flag, param, kind, help_text in spec:
            if kind == "json":
                group = parser.add_mutually_exclusive_group()
                group.add_argument(flag, dest=param, help=help_text)
                group.add_argument(flag + "-file", dest=param + "_json_file", type=Path,
                                   help="UTF-8 JSON 文件")
                continue
            kwargs: dict = {"help": help_text}
            if kind == "flag":
                kwargs["action"] = "store_true"
            elif kind == "list":
                kwargs["action"] = "append"
            elif kind == "file":
                kwargs["type"] = str
            elif kind == "bool":
                kwargs.update(type=_boolean, nargs="?", const=True)
            else:
                assert not isinstance(kind, str), kind
                kwargs["type"] = kind
            if flag.startswith("-"):
                parser.add_argument(flag, dest="host_command" if param == "command" else param, **kwargs)
            else:  # 位置参数：dest 由名字决定，不能再传
                parser.add_argument(flag, **kwargs)
        parser.add_argument("--project", help="工程 .nproj")
        parser.add_argument("--json", action="store_true", help="原样输出 JSON")


def _bind(args, spec) -> dict:
    params: dict = {}
    for _flag, param, kind, _help in spec:
        value = getattr(args, "host_command" if param == "command" else param, None)
        if kind == "json":
            path = getattr(args, param + "_json_file", None)
            if path is not None:
                try:
                    value = path.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeError) as exc:
                    raise OperationError(f"无法读取 {param} JSON 文件：{exc}") from None
            if value is not None:
                params[param] = _json_value(value, param)
            continue
        if value is None or value == [] or (kind == "flag" and value is False):
            continue
        if param == "after_id" and value == "":
            # These Operations use None as their default placement. Omit the
            # transport field so the published string schema stays accurate.
            continue
        if kind == "list" and not isinstance(value, list):
            value = [value]
        if kind == "file":
            # 长文本从文件读：命令行塞不下五千字的一章。
            value = Path(str(value)).read_text(encoding="utf-8")
        params[param] = value
    return params


def _invoke(name, params, project, access_mode="standard"):
    if name not in HOST_TOOL_NAMES:
        return tool_api.call(name, params, project=project)
    if not project:
        raise OperationError("主机命令需要 --project")
    if name == "run_host_command" and params.get("background"):
        raise OperationError("CLI 不支持后台命令；请使用驻留助手或 MCP 服务")
    owner = "cli-task-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + "-" + str(os.getpid())
    stopped = threading.Event()
    with host_access_context(access_mode, owner, stopped, project):
        try:
            result = tool_api.call(name, params, project=project)
            if name == "run_host_command":
                identifier = result["command_id"]
                chunks = []
                offset = 0
                while True:
                    result = tool_api.call("read_host_command", {"command_id": identifier, "offset": offset}, project=project)
                    chunks.append(result.get("content", ""))
                    offset = result["next_offset"]
                    if result["complete"]:
                        result["content"] = "".join(chunks)
                        return result
                    time.sleep(0.05)
            return result
        except KeyboardInterrupt:
            stopped.set()
            raise OperationError("已停止当前命令") from None
        finally:
            cancel_host_commands(project, owner)


def run(args) -> int:
    if args.command == 'project-api-key':
        if args.credential_action == 'status':
            payload = tool_api.call('read_project_api_key_status', {}, project=args.project)
        elif args.credential_action == 'clear':
            payload = tool_api.call('configure_project_api_key', {'clear_api_key': True}, project=args.project)
        else:
            if args.credential_action == 'set' and not args.key_stdin:
                raise OperationError('保存密钥须使用 --key-stdin，从stdin读取一行；不要把密钥放在命令参数中')
            key = None
            if args.key_stdin:
                key = sys.stdin.readline(65538).rstrip('\r\n')
                if not key.strip() or len(key) > 65536:
                    raise OperationError('stdin中的密钥为空或过长，未执行')
            params = {'api_key': key} if key is not None else {}
            payload = tool_api.call('check_project_api_key' if args.credential_action == 'check' else 'configure_project_api_key',
                                    params, project=args.project)
        print(tool_api.dumps(payload))
        return 1 if args.credential_action == 'check' and payload.get('valid') is not True else 0
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
        params = _json_value(args.params, "参数") if args.params else {}
        if not isinstance(params, dict):
            raise OperationError("参数须为 JSON 对象")
        payload = _invoke(args.name, params, args.project, getattr(args, "trusted_access_mode", "standard"))
        print(tool_api.dumps(payload))
        return 0

    tool_name, spec = ALIASES[args.command]
    params = _bind(args, spec)
    payload = _invoke(tool_name, params, args.project, getattr(args, "trusted_access_mode", "standard"))
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
    parser.add_argument("--access-mode", dest="trusted_access_mode", choices=["standard", "full"], default="standard", help="本次进程的可信主机访问模式")
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
