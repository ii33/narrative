import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .operations import OperationError, Operations

CONTEXT_FIELDS = ("context_window", "output_reserve", "compaction_reserve", "compaction_max_tokens")


def _context_arguments(parser):
    for field, description in (
        ("context_window", "模型上下文窗口token数"),
        ("output_reserve", "普通输出预留token数"),
        ("compaction_reserve", "压缩操作预留token数"),
        ("compaction_max_tokens", "单次压缩摘要输出上限token数"),
    ):
        parser.add_argument("--" + field.replace("_", "-"), type=int, help=description)


def _save_context_arguments(operations, args):
    changes = {field: getattr(args, field) for field in CONTEXT_FIELDS if getattr(args, field) is not None}
    if changes:
        operations.save_harness_settings(operations.harness_settings() | changes)


def main():
    parser = argparse.ArgumentParser(description="叙事证据工作台")
    parser.add_argument("--db", default="data/demo.sqlite3", help="项目数据库；新图项目使用独立 .sqlite3 文件")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("graph-create", help="创建全新节点—断言—时序图项目")
    graph_import = commands.add_parser("graph-import", help="向图项目导入 UTF-8 原文")
    graph_import.add_argument("path", type=Path)
    graph_import.add_argument("--title")
    graph_analyze = commands.add_parser("graph-analyze", help="启动可观察的多步图代理，产出待审候选")
    graph_analyze.add_argument("source_id")
    graph_analyze.add_argument("--task", help="本轮原文分析目标")
    graph_analyze.add_argument("--feedback", help="修订待审候选时的具体反馈")
    graph_analyze.add_argument("--seed-candidate-id", help="只读继承待审候选到私有草稿")
    graph_analyze.add_argument("--feedback-origin", choices=("user_input", "review_agent_guided"), default="user_input")
    graph_analyze.add_argument("--output-limit", default="inherit", help="inherit、service-default 或本任务正整数输出容量")
    graph_analyze.add_argument("--research-rounds", type=int, default=3)
    graph_analyze.add_argument("--request-seconds", type=int, default=180)
    graph_analyze.add_argument("--max-rounds", type=int)
    graph_analyze.add_argument("--max-total-tokens", type=int)
    graph_analyze.add_argument("--wall-seconds", type=int)
    _context_arguments(graph_analyze)
    graph_status = commands.add_parser("graph-status", help="查看图项目或代理运行状态")
    graph_status.add_argument("--run-id")
    graph_trace = commands.add_parser("graph-trace", help="查看模型回合、工具观察、草稿及预检轨迹")
    graph_trace.add_argument("run_id")
    graph_query = commands.add_parser("graph-query", help="检索已审核图节点及断言")
    graph_query.add_argument("--query", default="")
    graph_query.add_argument("--node-id")
    graph_view = commands.add_parser("graph-view", help="通过结构化 JSON 查询导图、时间轴、森林或分类表")
    view_input = graph_view.add_mutually_exclusive_group(required=True)
    view_input.add_argument("--spec", help="结构查询 JSON 对象")
    view_input.add_argument("--spec-file", type=Path, help="UTF-8 结构查询 JSON 文件")
    graph_candidate = commands.add_parser("graph-candidate", help="查看待审候选原始对象及诊断")
    graph_candidate.add_argument("candidate_id")
    for name in ("graph-accept", "graph-reject"):
        review_command = commands.add_parser(name, help="人工明确审核图候选")
        review_command.add_argument("candidate_id")
        review_command.add_argument("version", type=int)
    for name in ("graph-pause", "graph-resume", "graph-cancel"):
        control_command = commands.add_parser(name, help="协作式控制图代理")
        control_command.add_argument("run_id")
    graph_instruct = commands.add_parser("graph-instruct", help="在下一模型请求边界追加任务指令")
    graph_instruct.add_argument("run_id")
    graph_instruct.add_argument("text")
    ui = commands.add_parser("ui", help="启动工作台")
    ui.add_argument("--port", default="8501")
    commands.add_parser("demo", help="在空项目载入自编演示")
    imp = commands.add_parser("import", help="导入 UTF-8 章节")
    imp.add_argument("path", type=Path)
    imp.add_argument("--title")
    commands.add_parser("status", help="查看项目状态")
    character = commands.add_parser("character", help="查看人物档案")
    character.add_argument("entity_id")
    event_profile = commands.add_parser("event-profile", help="查看事件及其关联")
    event_profile.add_argument("event_id")
    search = commands.add_parser("search-analysis", help="按关键词与对象筛选故事资料")
    search.add_argument("--query", default="")
    search.add_argument("--source-id", action="append", default=[])
    search.add_argument("--entity-id", action="append", default=[])
    search.add_argument("--kind", action="append", default=[])
    search.add_argument("--narrative-type", action="append", default=[])
    search.add_argument("--category", action="append", default=[])
    search.add_argument("--status", action="append", default=[])
    search.add_argument("--storyline-id", action="append", default=[])
    search.add_argument("--limit", type=int, default=100)
    classification = commands.add_parser("classification-table", help="查看按章节汇总的叙事事件分类")
    classification.add_argument("--source-id", action="append", default=[])
    for cmd in ("accept", "reject", "check"):
        sub = commands.add_parser(cmd)
        sub.add_argument("change_id")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    export = commands.add_parser("export-input", help="创建任务并导出分块输入")
    export.add_argument("source_id")
    export.add_argument("destination", type=Path)
    ingest = commands.add_parser("ingest-result", help="校验并导入 harness 结果")
    ingest.add_argument("chunk_id")
    ingest.add_argument("path", type=Path)
    extract = commands.add_parser("extract", help="旧项目单次提取；新项目请用 graph-analyze")
    extract.add_argument("source_id")
    extract.add_argument("--base-url")
    extract.add_argument("--model")
    extract.add_argument("--timeout", type=int)
    _context_arguments(extract)
    args = parser.parse_args()
    try:
        if args.command == "ui":
            app = Path(__file__).with_name("ui.py")
            project_path = Path(args.db).resolve()
            if project_path.suffix != ".sqlite3":
                raise OperationError("界面项目文件须使用 .sqlite3 扩展名")
            raise SystemExit(
                subprocess.call(
                    [
                        sys.executable,
                        "-m",
                        "streamlit",
                        "run",
                        str(app),
                        "--server.address=127.0.0.1",
                        f"--server.port={args.port}",
                        "--server.showEmailPrompt=false",
                        "--logger.hideWelcomeMessage=true",
                        "--browser.gatherUsageStats=false",
                    ],
                    env={
                        **os.environ,
                        "NARRATIVE_PROJECT_DIR": str(project_path.parent),
                        "NARRATIVE_PROJECT_NAME": project_path.stem,
                    },
                )
            )
        if args.command == "restore":
            print(Operations.restore_file(args.backup, args.destination))
            return
        if args.command.startswith("graph-"):
            graph = Operations.create_graph_project(args.db) if args.command == "graph-create" else Operations.open_graph_project(args.db)
            if args.command == "graph-create":
                result = graph.graph_project_info()
            elif args.command == "graph-import":
                result = {"source_id": graph.import_source(args.title or args.path.stem, args.path.read_text(encoding="utf-8"))}
            elif args.command == "graph-analyze":
                _save_context_arguments(graph, args)
                output_limit = args.output_limit if args.output_limit in {"inherit", "service-default"} else int(args.output_limit)
                budget = {"max_tokens_per_round": output_limit, "research_rounds": args.research_rounds, "request_seconds": args.request_seconds,
                          "max_rounds": args.max_rounds, "max_total_tokens": args.max_total_tokens, "wall_seconds": args.wall_seconds}
                run_id = graph.start_graph_analysis([args.source_id], task=args.task, feedback=args.feedback, seed_candidate_id=args.seed_candidate_id, feedback_origin=args.feedback_origin, budget=budget)
                print(f"图代理任务：{run_id}", flush=True)
                try:
                    while True:
                        result = graph.graph_analysis_status(run_id)
                        if result["status"] not in {"queued", "running"}:
                            break
                        time.sleep(0.5)
                except KeyboardInterrupt:
                    graph.cancel_graph_analysis(run_id)
                    raise SystemExit(130) from None
            elif args.command == "graph-status":
                result = graph.graph_analysis_status(args.run_id) if args.run_id else graph.graph_project_info()
            elif args.command == "graph-trace":
                result = graph.graph_analysis_trace(args.run_id)
            elif args.command == "graph-query":
                result = graph.graph_query(query=args.query, node_id=args.node_id)
            elif args.command == "graph-view":
                spec_text = args.spec_file.read_text(encoding="utf-8-sig") if args.spec_file else args.spec
                result = graph.graph_view(json.loads(spec_text))
            elif args.command == "graph-candidate":
                result = graph.graph_candidate(args.candidate_id)
            elif args.command == "graph-accept":
                result = {"revision": graph.graph_accept(args.candidate_id, args.version)}
            elif args.command == "graph-reject":
                graph.graph_reject(args.candidate_id, args.version)
                result = {"status": "rejected"}
            elif args.command == "graph-pause":
                graph.pause_graph_analysis(args.run_id)
                result = graph.graph_analysis_control(args.run_id)
            elif args.command == "graph-resume":
                graph.resume_graph_analysis(args.run_id)
                result = graph.graph_analysis_control(args.run_id)
            elif args.command == "graph-cancel":
                graph.cancel_graph_analysis(args.run_id)
                result = graph.graph_analysis_control(args.run_id)
            elif args.command == "graph-instruct":
                result = {"instruction_id": graph.instruct_graph_analysis(args.run_id, args.text)}
            else:
                raise AssertionError("unreachable")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        operations = Operations(args.db)
        if args.command == "demo":
            print(operations.load_demo())
        elif args.command == "import":
            print(operations.import_file(args.path, args.title))
        elif args.command == "status":
            print(
                json.dumps(
                    operations.status(),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "character":
            print(json.dumps(operations.character_profile(args.entity_id), ensure_ascii=False, indent=2))
        elif args.command == "event-profile":
            print(json.dumps(operations.event_profile(args.event_id), ensure_ascii=False, indent=2))
        elif args.command == "search-analysis":
            print(
                json.dumps(
                    operations.search_analysis(
                        args.query,
                        source_ids=args.source_id or None,
                        entity_ids=args.entity_id or None,
                        kinds=args.kind or None,
                        narrative_types=args.narrative_type or None,
                        categories=args.category or None,
                        statuses=args.status or None,
                        storyline_ids=args.storyline_id or None,
                        limit=args.limit,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "classification-table":
            print(
                json.dumps(
                    operations.classification_table(args.source_id or None),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command in {"accept", "reject", "check"}:
            review = operations.review_candidate(args.change_id)
            if args.command == "accept":
                print(operations.accept_candidate(args.change_id, review.version))
            elif args.command == "reject":
                operations.reject_candidate(args.change_id, review.version)
            else:
                print(
                    json.dumps(
                        [d.model_dump() for d in review.diagnostics],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
        elif args.command == "backup":
            print(operations.backup(args.destination))
        elif args.command == "export-input":
            print(operations.export_input_file(args.source_id, args.destination))
        elif args.command == "ingest-result":
            print(operations.ingest_result_file(args.chunk_id, args.path) or "空结果已记录")
        elif args.command == "extract":
            settings = operations.harness_settings()
            for argument, field in (
                (args.base_url, "base_url"),
                (args.model, "model"),
                (args.timeout, "timeout_seconds"),
                *((getattr(args, field), field) for field in CONTEXT_FIELDS),
            ):
                if argument is not None:
                    settings[field] = argument
            operations.save_harness_settings(settings)
            run_id = operations.create_run(args.source_id)
            operations.start_extraction(run_id)
            print(f"提取任务：{run_id}", flush=True)
            try:
                while True:
                    run = next(r for r in operations.runs() if r["id"] == run_id)
                    if run["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.5)
            except KeyboardInterrupt:
                operations.cancel_extraction(run_id)
                raise SystemExit(130) from None
            print(json.dumps(run, ensure_ascii=False, indent=2))
            if run["status"] != "complete":
                raise OperationError(run["data"].get("error") or "提取未完成")
    except (OperationError, ValueError, OSError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
