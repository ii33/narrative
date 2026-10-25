"""Extract one chapter's nodes and events by reading it whole. Writes a report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from narrative.extraction import NODE_TYPES, extract_chapter
from narrative.harness import HarnessSettings

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "person": "人物", "group": "群体", "item": "物品",
    "location": "地点", "scene": "场景", "event": "事件",
}


def _api_key(provider: str) -> str:
    names = ("ARK_API_KEY", "DEEPSEEK_API_KEY") if provider.startswith("ark") else ("DEEPSEEK_API_KEY", "ARK_API_KEY")
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    raise SystemExit("缺少 " + " / ".join(names))


def _markdown(result: dict) -> str:
    counts = "、".join(
        f"{LABELS[key]} {value}" for key, value in result["counts"].items() if value
    )
    lines = [
        f"# {result['chapter']} · 章节结构",
        "",
        f"- 字数：{result['characters']}",
        f"- 模型：{result['provider']} / {result['model']}",
        f"- 节点：{len(result['nodes'])}（{counts}）",
        f"- 属性：{len(result.get('properties') or [])}",
        f"- 被丢弃：{len(result['rejected'])}",
    ]
    for node_type in NODE_TYPES:
        items = result["grouped"][node_type]
        if not items:
            continue
        lines += ["", f"## {LABELS[node_type]}（{len(items)}）", ""]
        if node_type == "event":
            lines += ["| 位置 | 事件 | 参与者 | 场景 | 引文 |", "| ---: | --- | --- | --- | --- |"]
            for item in items:
                quote = item["quotes"][0].replace("|", "\\|")
                quote = quote[:40] + "…" if len(quote) > 40 else quote
                lines.append(
                    f"| {item['start']} | {item['name']} | {'、'.join(item['participants']) or '—'} "
                    f"| {item['scene'] or '—'} | {quote} |"
                )
        else:
            lines += ["| 位置 | 名称 | 别名 | 引文 |", "| ---: | --- | --- | --- |"]
            for item in items:
                quote = item["quotes"][0].replace("|", "\\|")
                quote = quote[:40] + "…" if len(quote) > 40 else quote
                lines.append(
                    f"| {item['start']} | {item['name']} | {'、'.join(item['aliases']) or '—'} | {quote} |"
                )
    properties = result.get("properties") or []
    if properties:
        lines += ["", f"## 属性（{len(properties)}）", "",
                  "| 位置 | 节点 | 属性 | 值 | 分类 | 范围 | 引文 |",
                  "| ---: | --- | --- | --- | --- | --- | --- |"]
        for item in properties:
            quote = item["quotes"][0].replace("|", "\\|")
            quote = quote[:32] + "…" if len(quote) > 32 else quote
            scope = item["scope"] + (f"（{item['holder']}）" if item["holder"] else "")
            lines.append(
                f"| {item['start']} | {item['node']} | {item['property']} | {item['value']} "
                f"| {item.get('category') or '其他'} | {scope} | {quote} |"
            )
    review = result.get("review") or {}
    if review.get("dropped") or review.get("unresolved"):
        lines += ["", f"## 第二轮对比（{review.get('rounds', 0)} 轮）", ""]
        for item in review.get("dropped") or []:
            lines.append(
                f"- 丢弃 {item.get('node')}.{item.get('property')} = "
                f"{item.get('value')}：{item.get('reason', '')}"
            )
        for item in review.get("unresolved") or []:
            lines.append(
                f"- 存疑 {item.get('node')}.{item.get('property')} = "
                f"{item.get('value')}：{item.get('question', '')}"
            )
    if result["rejected"]:
        lines += ["", "## 被丢弃", ""]
        for item in result["rejected"]:
            lines.append(f"- {item.get('name', '?')}：{item['reason']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", type=Path, required=True, help="章节 txt")
    parser.add_argument("--title", default="")
    parser.add_argument("--model", default="ark-code-latest")
    parser.add_argument("--provider", default="ark")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--project", type=Path, default=None,
                        help="图项目：提供后开启第二轮属性对比")
    parser.add_argument("--timeout", type=int, default=600, help="单次调用超时（秒）")
    args = parser.parse_args()

    text = args.text.read_text(encoding="utf-8")
    title = args.title or text.splitlines()[0].strip()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = args.out or ROOT / "data" / "extraction" / f"{stamp}-{args.text.stem}"
    out.mkdir(parents=True, exist_ok=True)

    settings = HarnessSettings(
        provider=args.provider, model=args.model, timeout_seconds=args.timeout
    )
    ops = None
    if args.project is not None:
        from narrative.graph_operations import GraphOperations

        ops = GraphOperations(args.project)
    result = extract_chapter(
        title, text, settings=settings, api_key=_api_key(args.provider),
        workdir=out / "sdk", ops=ops,
    )

    (out / "result.json").write_text(
        json.dumps({key: value for key, value in result.items() if key != "raw"},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "raw.txt").write_text(result["raw"], encoding="utf-8")
    (out / "table.md").write_text(_markdown(result), encoding="utf-8")
    print(_markdown(result))
    print(f"\n输出目录：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
