"""Build a chapter-ordered disclosure inventory from a GraphRAG export.

This is a read-only sidecar: it does not write formal graph records. It answers
"what does the text disclose at this position, and where is the evidence?"
for entities, claims and relationships, while preserving items that cannot be
positioned exactly.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _chinese_number(value: str) -> int:
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return digits.get(value, 0)


def _source_order(source_map: dict) -> list[dict]:
    import re

    def key(item: dict) -> tuple:
        title = item["title"]
        match = re.search(r"第([一二三四五六七八九十]+)章", title)
        number = _chinese_number(match.group(1)) if match else 0
        return (title.split("第")[0], number, title)

    return sorted(source_map["sources"], key=key)


def _document_source_map(export: dict, source_map: dict) -> dict[str, str]:
    input_to_source = {
        item["input_file"]: item["source_id"] for item in source_map["sources"]
    }
    result: dict[str, str] = {}
    for document in export["tables"]["documents"]:
        stem = Path(str(document.get("title", ""))).stem
        source_id = input_to_source.get(stem)
        if source_id:
            result[document["id"]] = source_id
    return result


def _text_unit_source_map(export: dict, document_source: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for unit in export["tables"]["text_units"]:
        source_id = document_source.get(unit.get("document_id"))
        if source_id:
            result[unit["id"]] = source_id
    return result


def _find_all(text: str, needle: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while needle:
        index = text.find(needle, start)
        if index < 0:
            break
        positions.append(index)
        start = index + max(1, len(needle))
    return positions


def _sentence(text: str, start: int, end: int, radius: int = 60) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right].replace("\n", " ").strip()


def _locate_claim(chapters: list[dict], source_text: str) -> tuple[dict | None, int, str, str]:
    """Locate a claim by full text or a unique prefix/suffix.

    Returns (chapter, start, status, matched_text). ``status`` is ``exact`` or
    ``partial``; ``None`` means no reliable position.
    """
    if not source_text:
        return None, 0, "unplaced", ""
    for chapter in chapters:
        index = chapter["text"].find(source_text)
        if index >= 0:
            return chapter, index, "exact", source_text
    stripped = source_text.strip()
    candidates = []
    for length in (30, 20, 12):
        if len(stripped) >= length:
            candidates.append(stripped[:length])
            candidates.append(stripped[-length:])
    for candidate in candidates:
        hits = []
        for chapter in chapters:
            index = chapter["text"].find(candidate)
            if index >= 0:
                hits.append((chapter, index))
        if len(hits) == 1:
            chapter, index = hits[0]
            return chapter, index, "partial", candidate
    return None, 0, "unplaced", ""


def _sentences(text: str) -> list[tuple[int, int, str]]:
    import re

    result = []
    for match in re.finditer(r"[^。！？\n]+[。！？]?", text):
        value = match.group(0)
        if value.strip():
            result.append((match.start(), match.end(), value))
    return result


def _cooccurrence_position(
    chapters: list[dict], source: str, target: str
) -> tuple[dict | None, int, int, str]:
    if not source or not target or source == "NONE" or target == "NONE":
        return None, 0, 0, ""
    hits = []
    for chapter in chapters:
        for start, end, sentence in _sentences(chapter["text"]):
            if source in sentence and target in sentence:
                hits.append((chapter, start, end, sentence))
    if len(hits) == 1:
        return hits[0]
    return None, 0, 0, ""


def build(export_path: Path, source_map_path: Path, input_dir: Path, mapping_path: Path | None) -> dict:
    export = _load(export_path)
    source_map = _load(source_map_path)
    sources = _source_order(source_map)
    texts = {
        item["source_id"]: (input_dir / item["input_file"]).read_text(encoding="utf-8")
        for item in sources
    }
    mapping = {}
    if mapping_path and mapping_path.is_file():
        mapping = (
            _load(mapping_path)
            .get("namespaces", {})
            .get("sample_zh", {})
            .get("entities", {})
        )
    entity_by_id = {
        item["id"]: item for item in export["tables"]["entities"] if item.get("id")
    }
    entity_by_title = {
        str(item.get("title", "")).strip(): item for item in entity_by_id.values()
    }

    chapters = [
        {
            "source_id": item["source_id"],
            "title": item["title"],
            "text": texts[item["source_id"]],
            "entities": [],
            "claims": [],
            "relationships": [],
        }
        for item in sources
    ]
    for entity in entity_by_id.values():
        title = str(entity.get("title", "")).strip()
        if not title:
            continue
        for chapter in chapters:
            for position in _find_all(chapter["text"], title):
                chapter["entities"].append(
                    {
                        "kind": "entity",
                        "external_id": entity["id"],
                        "name": title,
                        "entity_type": entity.get("type"),
                        "node_id": mapping.get(entity["id"]),
                        "start": position,
                        "end": position + len(title),
                        "snippet": _sentence(chapter["text"], position, position + len(title)),
                    }
                )

    unplaced_claims = []
    for claim in export["tables"]["covariates"]:
        source_text = str(claim.get("source_text", ""))
        chapter, start, status, matched = _locate_claim(chapters, source_text)
        subject = entity_by_title.get(str(claim.get("subject_id", "")).strip())
        target = entity_by_title.get(str(claim.get("object_id", "")).strip())
        item = {
            "kind": "claim",
            "external_id": claim.get("id"),
            "subject": claim.get("subject_id"),
            "object": claim.get("object_id"),
            "status": claim.get("status"),
            "description": claim.get("description"),
            "source_text": source_text,
            "matched_text": matched,
            "subject_node_id": mapping.get(subject["id"]) if subject else None,
            "object_node_id": mapping.get(target["id"]) if target else None,
            "start": start if chapter else None,
            "end": start + len(matched) if chapter else None,
            "position_status": status,
            "reason": None if chapter else "no unique exact or prefix position in the chapter texts",
        }
        if chapter:
            chapter["claims"].append(item)
        else:
            unplaced_claims.append(item)

    unplaced_relationships = []
    for relationship in export["tables"]["relationships"]:
        source_name = str(relationship.get("source") or "")
        target_name = str(relationship.get("target") or "")
        chapter, start, end, sentence = _cooccurrence_position(
            chapters, source_name, target_name
        )
        item = {
            "kind": "relationship",
            "external_id": relationship.get("id"),
            "source": source_name,
            "target": target_name,
            "description": relationship.get("description"),
            "chapters": [chapter["source_id"]] if chapter else [],
            "start": start if chapter else None,
            "end": end if chapter else None,
            "evidence": sentence if chapter else None,
            "position_status": "cooccurrence" if chapter else "unplaced",
            "reason": (
                "located by a unique sentence containing both source and target; verify semantics"
                if chapter
                else "no unique sentence contains both source and target"
            ),
        }
        if chapter:
            chapter["relationships"].append(item)
        else:
            unplaced_relationships.append(item)

    for chapter in chapters:
        chapter["entities"].sort(key=lambda item: item["start"])
        chapter["claims"].sort(
            key=lambda item: (item["start"] is None, item["start"] or 0)
        )
        chapter["relationships"].sort(key=lambda item: str(item["external_id"]))

    claims = [item for chapter in chapters for item in chapter["claims"]]
    entity_mentions = [item for chapter in chapters for item in chapter["entities"]]
    all_claims = claims + unplaced_claims
    summary = {
        "chapters": len(chapters),
        "entities": len(entity_by_id),
        "entities_with_mentions": len(
            {item["external_id"] for item in entity_mentions}
        ),
        "entity_mentions": len(entity_mentions),
        "claims": len(export["tables"]["covariates"]),
        "claims_exact": sum(1 for item in claims if item["position_status"] == "exact"),
        "claims_partial": sum(1 for item in claims if item["position_status"] == "partial"),
        "claims_unplaced": len(unplaced_claims),
        "relationships": len(export["tables"]["relationships"]),
        "relationships_positioned": sum(
            1 for chapter in chapters for item in chapter["relationships"]
        ),
        "relationships_unplaced": len(unplaced_relationships),
        "claims_with_subject_node_positioned": sum(
            1 for item in claims if item["subject_node_id"]
        ),
        "claims_with_object_node_positioned": sum(
            1 for item in claims if item["object_node_id"]
        ),
        "claims_with_subject_node_all": sum(
            1 for item in all_claims if item["subject_node_id"]
        ),
        "claims_with_object_node_all": sum(
            1 for item in all_claims if item["object_node_id"]
        ),
    }
    return {
        "schema": "chapter-disclosure/1",
        "summary": summary,
        "unplaced_claims": unplaced_claims,
        "unplaced_relationships": unplaced_relationships,
        "chapters": [
            {
                "source_id": chapter["source_id"],
                "title": chapter["title"],
                "text": chapter["text"],
                "text_length": len(chapter["text"]),
                "entities": chapter["entities"],
                "claims": chapter["claims"],
                "relationships": chapter["relationships"],
            }
            for chapter in chapters
        ],
    }


def _highlight(text: str, claims: list[dict], entities: list[dict]) -> str:
    spans: list[tuple[int, int, str, str, str]] = []
    for claim in claims:
        if claim.get("start") is not None:
            spans.append(
                (
                    claim["start"],
                    claim["end"],
                    "claim",
                    f"claim-{claim['external_id']}",
                    f"{claim.get('status') or ''} {claim.get('source_text') or ''}",
                )
            )
    for entity in entities:
        spans.append(
            (
                entity["start"],
                entity["end"],
                "entity",
                f"entity-{entity['external_id']}-{entity['start']}",
                f"{entity.get('entity_type') or ''} {entity['name']}",
            )
        )
    spans.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    chosen: list[tuple[int, int, str, str, str]] = []
    last_end = -1
    for span in spans:
        if span[0] >= last_end:
            chosen.append(span)
            last_end = span[1]
    pieces: list[str] = []
    cursor = 0
    for start, end, kind, anchor, title in chosen:
        pieces.append(html.escape(text[cursor:start]))
        pieces.append(
            f'<span id="{html.escape(anchor)}" class="hl {kind}" '
            f'title="{html.escape(title)}">{html.escape(text[start:end])}</span>'
        )
        cursor = end
    pieces.append(html.escape(text[cursor:]))
    return "".join(pieces)


def write_html(payload: dict, out: Path) -> None:
    sections = []
    for chapter in payload["chapters"]:
        entity_count = len(chapter["entities"])
        exact_claims = sum(
            1 for item in chapter["claims"] if item["position_status"] == "exact"
        )
        sections.append(
            f"<section><h2>{html.escape(chapter['title'])}</h2>"
            f"<p class='meta'>实体提及 {entity_count} · claim {len(chapter['claims'])}"
            f"（精确定位 {exact_claims}）· relationship {len(chapter['relationships'])}</p>"
            f"<div class='text'>{_highlight(chapter['text'], chapter['claims'], chapter['entities'])}</div>"
            f"<h3>本章条目</h3><table><thead><tr><th>位置</th><th>类型</th>"
            f"<th>内容</th><th>证据/说明</th></tr></thead><tbody>"
            + "".join(
                _row(item)
                for item in (
                    chapter["claims"] + chapter["relationships"]
                )
            )
            + "</tbody></table></section>"
        )
    summary = payload["summary"]
    unplaced_rows = "".join(
        _row(item) for item in (
            payload.get("unplaced_claims", []) + payload.get("unplaced_relationships", [])
        )
    )
    body = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>章节披露整理</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#f6f7f9;color:#1f2328}}
header{{padding:24px 32px;background:#fff;border-bottom:1px solid #d8dee4}}
h1{{margin:0 0 8px;font-size:24px}} h2{{margin-top:32px}} h3{{margin-top:20px}}
.meta{{color:#57606a;font-size:13px}} .stats{{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px}}
.stat{{background:#eef2f6;border-radius:8px;padding:8px 12px;font-size:13px}}
section{{background:#fff;margin:24px 32px;padding:20px 24px;border:1px solid #d8dee4;border-radius:10px}}
.text{{white-space:pre-wrap;line-height:1.9;font-size:15px;max-height:520px;overflow:auto;border:1px solid #eaeef2;padding:12px;border-radius:8px}}
.hl{{border-radius:3px;padding:0 1px}} .hl.claim{{background:#fff3cd;border-bottom:2px solid #d4a72c}}
.hl.entity{{background:#ddf4ff;border-bottom:1px solid #54aeff}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border-bottom:1px solid #eaeef2;padding:8px;vertical-align:top;text-align:left}}
th{{color:#57606a;font-weight:600}} code{{background:#eef2f6;padding:1px 4px;border-radius:4px}}
</style></head><body><header><h1>章节披露整理（前三章）</h1>
<p class="meta">按原文顺序列出实体、claim 与 relationship；黄色为精确 claim 证据，蓝色为实体提及。此页是披露清单，不写入正式图。</p>
<div class="stats">
<div class="stat">实体 {summary['entities']}（有字面提及 {summary['entities_with_mentions']}）</div>
<div class="stat">实体提及 {summary['entity_mentions']}</div>
<div class="stat">claim {summary['claims']}（精确 {summary['claims_exact']} / 部分 {summary['claims_partial']} / 未定位 {summary['claims_unplaced']}）</div>
<div class="stat">relationship {summary['relationships']}（共现定位 {summary['relationships_positioned']} / 未定位 {summary['relationships_unplaced']}）</div>
<div class="stat">claim 主语映射 {summary['claims_with_subject_node_all']} / 宾语 {summary['claims_with_object_node_all']}（全部 118）</div>
</div></header>{''.join(sections)}
<section><h2>未定位条目</h2>
<p class="meta">没有可靠章节/原文位置的 claim 与 relationship；保留原文和原因，不猜测位置。</p>
<table><thead><tr><th>类型</th><th>内容</th><th>原因</th></tr></thead><tbody>
{unplaced_rows}
</tbody></table></section></body></html>"""
    out.write_text(body, encoding="utf-8")


def _row(item: dict) -> str:
    if item["kind"] == "claim":
        position = item.get("start")
        pos = f"{position}" if position is not None else "章级"
        content = (
            f"<b>{html.escape(str(item.get('subject') or ''))}</b> → "
            f"<b>{html.escape(str(item.get('object') or ''))}</b><br>"
            f"{html.escape(str(item.get('description') or ''))}"
        )
        evidence = html.escape(str(item.get("source_text") or ""))
        if item.get("reason"):
            evidence += f"<br><span class='meta'>{html.escape(item['reason'])}</span>"
    else:
        pos = "章级"
        content = (
            f"<b>{html.escape(str(item.get('source') or ''))}</b> ↔ "
            f"<b>{html.escape(str(item.get('target') or ''))}</b><br>"
            f"{html.escape(str(item.get('description') or '')[:400])}"
        )
        evidence = html.escape(str(item.get("evidence") or ""))
        if item.get("reason"):
            evidence += f"<br><span class='meta'>{html.escape(item['reason'])}</span>"
    return (
        f"<tr><td>{pos}</td><td>{html.escape(item['kind'])}</td>"
        f"<td>{content}</td><td>{evidence}</td></tr>"
    )


def write_markdown(payload: dict, out: Path) -> None:
    lines = ["# 前三章披露整理", "", "| 项 | 数量 |", "| --- | ---: |"]
    for key, value in payload["summary"].items():
        lines.append(f"| {key} | {value} |")
    for chapter in payload["chapters"]:
        lines += ["", f"## {chapter['title']}", ""]
        lines.append(f"- 实体提及：{len(chapter['entities'])}")
        lines.append(f"- claim：{len(chapter['claims'])}")
        lines.append(f"- relationship：{len(chapter['relationships'])}")
        lines += ["", "### 精确 claim", ""]
        for item in chapter["claims"]:
            if item["position_status"] != "exact":
                continue
            lines.append(
                f"- `{item['start']}` {item.get('subject')} → {item.get('object')}："
                f"{item.get('source_text')}"
            )
        chapter_only = [item for item in chapter["claims"] if item["position_status"] != "exact"]
        if chapter_only:
            lines += ["", f"### 章级 claim（{len(chapter_only)}，无精确引文）", ""]
            for item in chapter_only[:10]:
                lines.append(
                    f"- {item.get('subject')} → {item.get('object')}："
                    f"{(item.get('description') or '')[:120]}"
                )
            if len(chapter_only) > 10:
                lines.append(f"- … 另有 {len(chapter_only) - 10} 条")
    unplaced_claims = payload.get("unplaced_claims", [])
    unplaced_relationships = payload.get("unplaced_relationships", [])
    if unplaced_claims or unplaced_relationships:
        lines += ["", "## 未定位条目", ""]
        lines.append(f"- 未定位 claim：{len(unplaced_claims)}")
        lines.append(f"- 未定位 relationship：{len(unplaced_relationships)}")
        for item in unplaced_claims[:10]:
            lines.append(
                f"- claim {item.get('subject')} → {item.get('object')}："
                f"{(item.get('description') or '')[:100]}（{item.get('reason')}）"
            )
        for item in unplaced_relationships[:10]:
            lines.append(
                f"- relationship {item.get('source')} ↔ {item.get('target')}："
                f"{(item.get('description') or '')[:100]}（{item.get('reason')}）"
            )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--source-map", required=True, type=Path)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    payload = build(args.export, args.source_map, args.input_dir, args.mapping)
    (args.out / "index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(payload, args.out / "report.md")
    write_html(payload, args.out / "report.html")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
