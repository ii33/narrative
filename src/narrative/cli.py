import argparse
import json
from pathlib import Path

from . import tool_cli
from .errors import OperationError
from .project_catalog import create_graph_project, open_graph_project

CONTEXT_FIELDS = ("context_window", "output_reserve", "compaction_reserve", "compaction_max_tokens")


def _records_arguments(parser):
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--records", "--records-json", dest="records", help="属性记录 JSON 数组")
    group.add_argument("--records-file", type=Path, help="UTF-8 属性记录 JSON 数组文件")


def _load_records(args):
    text = (
        args.records_file.read_text(encoding="utf-8-sig")
        if args.records_file
        else args.records
    )
    try:
        records = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OperationError(f"属性记录不是合法 JSON：{exc}") from None
    if not isinstance(records, list):
        raise OperationError("属性记录须为 JSON 数组")
    return records


def _at_argument(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def main():
    parser = argparse.ArgumentParser(description="叙事证据工作台")
    parser.add_argument("--db", default="data/demo.sqlite3", help="项目数据库；新图项目使用独立 .sqlite3 文件")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("graph-create", help="创建工程包（节点 + 属性记录 + 原文）")
    graph_import = commands.add_parser("graph-import", help="向图项目导入 UTF-8 原文")
    graph_import.add_argument("path", type=Path)
    graph_import.add_argument("--title")
    graph_query = commands.add_parser("graph-query", help="检索已审核的节点与属性记录")
    graph_query.add_argument("--query", default="")
    graph_query.add_argument("--node-id")
    graph_view = commands.add_parser("graph-view", help="通过结构化 JSON 查询导图、时间轴、森林或分类表")
    view_input = graph_view.add_mutually_exclusive_group(required=True)
    view_input.add_argument("--spec", help="结构查询 JSON 对象")
    view_input.add_argument("--spec-file", type=Path, help="UTF-8 结构查询 JSON 文件")
    graph_event_compare = commands.add_parser(
        "graph-event-compare",
        help="比较两个事件：默认按披露顺序，--axis story 按故事先后",
    )
    graph_event_compare.add_argument("left_id")
    graph_event_compare.add_argument("right_id")
    graph_event_compare.add_argument("--context-id", default="canon")
    graph_event_compare.add_argument("--as-of-revision", type=int)
    graph_event_compare.add_argument(
        "--axis", choices=["disclosure", "story"], default="disclosure",
        help="disclosure=读者先读到谁（线性）；story=故事里谁先发生（可分支）",
    )
    graph_event_lines = commands.add_parser("graph-event-lines", help="按阅读顺序列出事件")
    graph_event_lines.add_argument("--context-id", default="canon")
    graph_event_lines.add_argument("--as-of-revision", type=int)
    graph_candidate = commands.add_parser("graph-candidate", help="查看待审候选原始对象及诊断")
    graph_candidate.add_argument("candidate_id")
    for name in ("graph-accept", "graph-reject"):
        review_command = commands.add_parser(name, help="人工明确审核图候选")
        review_command.add_argument("candidate_id")
        review_command.add_argument("version", type=int)
    property_precheck = commands.add_parser(
        "property-precheck", aliases=["graph-property-precheck"], help="只读预检 v5 属性追加记录"
    )
    _records_arguments(property_precheck)
    property_precheck.add_argument("--expected-revision", type=int)
    property_propose = commands.add_parser(
        "property-propose", aliases=["graph-property-propose"], help="提交 v5 属性待审候选"
    )
    _records_arguments(property_propose)
    property_propose.add_argument("--expected-revision", type=int)
    property_propose.add_argument("--source", choices=("manual", "model", "correction"), default="manual")
    property_propose.add_argument("--note", default="")
    for name in ("property-accept", "property-reject"):
        review_command = commands.add_parser(
            name, aliases=["graph-" + name], help="人工审核 v5 属性候选"
        )
        review_command.add_argument("candidate_id")
        review_command.add_argument("version", type=int)
    property_state = commands.add_parser(
        "property-state", aliases=["graph-property-state"], help="查询节点属性在故事时点的投影"
    )
    property_state.add_argument("node_id")
    property_state.add_argument("property_id")
    property_state.add_argument("at_positional", nargs="?", help="可选故事 TimeRef 值")
    property_state.add_argument("--at", help="故事 TimeRef：JSON 或 number/datetime 值；省略为当前视图")
    property_state.add_argument("--as-of-revision", type=int)
    property_state.add_argument(
        "--scope", default="world", choices=["world", "cognition", "claim"]
    )
    property_state.add_argument("--holder-id", help="认知持有者；scope 非 world 时必填")
    property_state.add_argument("--disclosure-source", help="披露切点的来源 ID")
    property_state.add_argument("--disclosure-start", type=int, help="披露切点的原文偏移")
    property_state.add_argument(
        "--diagnostics", action="store_true",
        help="作者/编辑器诊断：返回被过滤的记录 ID 与原因",
    )
    property_disclosure = commands.add_parser(
        "property-disclosure-state",
        aliases=["graph-property-disclosure-state"],
        help="按原文披露切点查询可见状态（忽略故事时间）",
    )
    property_disclosure.add_argument("node_id")
    property_disclosure.add_argument("property_id")
    property_disclosure.add_argument("--source", required=True, help="来源 ID")
    property_disclosure.add_argument("--start", required=True, type=int, help="原文偏移")
    property_disclosure.add_argument(
        "--scope", default="world", choices=["world", "cognition", "claim"]
    )
    property_disclosure.add_argument("--holder-id")
    change_precheck = commands.add_parser(
        "change-precheck", aliases=["graph-change-precheck"], help="预检混合图/属性批次"
    )
    change_precheck.add_argument("--graph", type=Path, help="图对象 JSON 文件")
    change_precheck.add_argument("--properties", type=Path, help="属性记录 JSON 文件")
    change_precheck.add_argument("--expected-revision", type=int)
    change_propose = commands.add_parser(
        "change-propose", aliases=["graph-change-propose"], help="建立统一变更候选"
    )
    change_propose.add_argument("--graph", type=Path)
    change_propose.add_argument("--properties", type=Path)
    change_propose.add_argument("--expected-revision", type=int)
    change_propose.add_argument("--source", default="manual", choices=["manual", "model", "correction"])
    change_propose.add_argument("--note", default="")
    change_candidates = commands.add_parser(
        "change-candidates", aliases=["graph-change-candidates"], help="列出统一变更候选"
    )
    change_candidates.add_argument("--status")
    change_candidate = commands.add_parser(
        "change-candidate", aliases=["graph-change-candidate"], help="查看统一变更候选"
    )
    change_candidate.add_argument("candidate_id")
    change_accept = commands.add_parser(
        "change-accept", aliases=["graph-change-accept"], help="接受统一变更候选"
    )
    change_accept.add_argument("candidate_id")
    change_accept.add_argument("--version", type=int, default=1)
    change_reject = commands.add_parser(
        "change-reject", aliases=["graph-change-reject"], help="拒绝统一变更候选"
    )
    change_reject.add_argument("candidate_id")
    change_reject.add_argument("--version", type=int, default=1)
    property_history = commands.add_parser(
        "property-history", aliases=["graph-property-history"], help="查看节点属性的已审记录与撤回"
    )
    property_history.add_argument("node_id")
    property_history.add_argument("property_id")
    property_history.add_argument(
        "--scope", default="world", choices=["world", "cognition", "claim"]
    )
    property_history.add_argument("--holder-id")
    property_world = commands.add_parser(
        "property-world-state", aliases=["graph-property-world-state"], help="按节点分组投影全部属性状态"
    )
    property_world.add_argument("at_positional", nargs="?", help="可选故事 TimeRef 值")
    property_world.add_argument("--at", help="故事 TimeRef：JSON 或 number/datetime 值；省略为当前视图")
    property_world.add_argument("--as-of-revision", type=int)
    tool_cli.add_commands(commands)
    args = parser.parse_args()
    if args.command.startswith("graph-property-"):
        args.command = "property-" + args.command[len("graph-property-"):]
    if args.command == "tool" or args.command in tool_cli.ALIASES:
        try:
            return tool_cli.run(args)
        except (OperationError, tool_cli.tool_api.ToolError) as exc:
            parser.exit(1, f"错误：{exc}\n")
    try:
        if args.command.startswith(("graph-", "property-", "change-")):
            graph = create_graph_project(args.db) if args.command == "graph-create" else open_graph_project(args.db)
            if args.command == "graph-create":
                result = graph.graph_project_info()
            elif args.command == "graph-import":
                result = {"source_id": graph.import_source(args.title or args.path.stem, args.path.read_text(encoding="utf-8"))}
            elif args.command == "graph-query":
                result = graph.graph_query(query=args.query, node_id=args.node_id)
            elif args.command == "graph-view":
                spec_text = args.spec_file.read_text(encoding="utf-8-sig") if args.spec_file else args.spec
                result = graph.graph_view(json.loads(spec_text))
            elif args.command == "graph-event-compare":
                result = graph.graph_event_compare(
                    args.left_id, args.right_id,
                    context_id=args.context_id, as_of_revision=args.as_of_revision,
                    axis=args.axis,
                )
            elif args.command == "graph-event-lines":
                result = graph.graph_event_lines(
                    context_id=args.context_id, as_of_revision=args.as_of_revision,
                )
            elif args.command == "graph-candidate":
                result = graph.graph_candidate(args.candidate_id)
            elif args.command == "graph-accept":
                result = {"revision": graph.graph_accept(args.candidate_id, args.version)}
            elif args.command == "graph-reject":
                graph.graph_reject(args.candidate_id, args.version)
                result = {"status": "rejected"}
            elif args.command == "property-precheck":
                revision = (
                    args.expected_revision
                    if args.expected_revision is not None
                    else graph.graph_project_info()["revision"]
                )
                result = graph.property_precheck(_load_records(args), revision)
            elif args.command == "property-propose":
                revision = (
                    args.expected_revision
                    if args.expected_revision is not None
                    else graph.graph_project_info()["revision"]
                )
                candidate_id = graph.property_propose(
                    _load_records(args), revision, source=args.source, note=args.note
                )
                try:
                    candidate = graph.property_candidate(candidate_id)
                except (AttributeError, ValueError):
                    candidate = {"status": "pending", "version": 1, "diagnostics": []}
                result = {
                    "candidate_id": candidate_id,
                    "status": candidate["status"],
                    "version": candidate["version"],
                    "expected_revision": revision,
                    "diagnostics": candidate["diagnostics"],
                }
            elif args.command == "property-accept":
                result = {
                    "revision": graph.property_accept(args.candidate_id, args.version)
                }
            elif args.command == "property-reject":
                graph.property_reject(args.candidate_id, args.version)
                result = {"status": "rejected"}
            elif args.command == "property-state":
                at = args.at if args.at is not None else args.at_positional
                disclosure_cut = None
                if args.disclosure_source is not None or args.disclosure_start is not None:
                    if args.disclosure_source is None or args.disclosure_start is None:
                        raise OperationError("披露切点须同时提供 --disclosure-source 和 --disclosure-start")
                    disclosure_cut = {
                        "source_id": args.disclosure_source,
                        "start": args.disclosure_start,
                    }
                result = graph.property_state(
                    args.node_id,
                    args.property_id,
                    _at_argument(at),
                    as_of_revision=args.as_of_revision,
                    scope=args.scope,
                    holder_id=args.holder_id,
                    disclosure_cut=disclosure_cut,
                    include_diagnostics=args.diagnostics,
                )
            elif args.command == "property-disclosure-state":
                result = graph.property_disclosure_state(
                    args.node_id,
                    args.property_id,
                    {"source_id": args.source, "start": args.start},
                    scope=args.scope,
                    holder_id=args.holder_id,
                )
            elif args.command in {"change-precheck", "change-propose"}:
                graph_objects = (
                    json.loads(args.graph.read_text(encoding="utf-8"))
                    if getattr(args, "graph", None) else []
                )
                property_records = (
                    json.loads(args.properties.read_text(encoding="utf-8"))
                    if getattr(args, "properties", None) else []
                )
                expected = args.expected_revision
                if expected is None:
                    expected = graph.graph_project_info()["revision"]
                if args.command == "change-precheck":
                    result = graph.change_precheck(graph_objects, property_records, expected)
                else:
                    result = {
                        "candidate_id": graph.change_propose(
                            graph_objects, property_records, expected,
                            source=args.source, note=args.note,
                        )
                    }
            elif args.command == "change-candidates":
                result = graph.change_candidates(args.status)
            elif args.command == "change-candidate":
                result = graph.change_candidate(args.candidate_id)
            elif args.command == "change-accept":
                result = {"revision": graph.change_accept(args.candidate_id, args.version)}
            elif args.command == "change-reject":
                graph.change_reject(args.candidate_id, args.version)
                result = {"status": "rejected"}
            elif args.command == "property-history":
                result = graph.property_history(
                    args.node_id, args.property_id,
                    scope=args.scope, holder_id=args.holder_id,
                )
            elif args.command == "property-world-state":
                at = args.at if args.at is not None else args.at_positional
                result = graph.property_world_state(
                    _at_argument(at), as_of_revision=args.as_of_revision
                )
            else:
                raise AssertionError("unreachable")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        raise OperationError(f"未知命令：{args.command}")
    except (OperationError, ValueError, OSError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
