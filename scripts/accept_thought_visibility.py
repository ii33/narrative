"""One read-only real task checks public process text and a visible final answer."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

TASK = '请先用一句简短的公开进度说明介绍你将做的检查，然后调用project_info，回答当前工程的人物数量。只读，不提交或修改任何业务内容；不需要详细推理，也不用固定标签。'
PRIVATE_TYPES = {'reasoning', 'thinking', 'redacted_thinking'}


def seed(ops):
    ops.save_harness_settings({'provider': 'ark', 'model': 'ark-code-latest', 'reasoning_effort': 'low',
        'context_window': 1_000_000, 'output_reserve': 131_072, 'compaction_reserve': 65_536,
        'compaction_max_tokens': 32_768, 'max_tokens': 131_072})
    source = ops.import_source('只读公开过程验收', '档案室的窗户开着，桌上放着一本空白登记簿。')
    script = ops.create_screenplay('只读空容器', reason='公开过程显示验收种子')['screenplay']
    fixture = {'source_id': source, 'screenplay_id': script['id']}
    return fixture | {'before': business_snapshot(ops, fixture)}


def business_snapshot(ops, fixture):
    return {'graph': ops.graph_snapshot(), 'properties': ops.property_records(),
        'adaptation': ops.adaptation_snapshot(), 'screenplay': ops.read_screenplay(fixture['screenplay_id']),
        'source': ops.source(fixture['source_id']),
        'assets': {category: ops.list_assets(category) for category in ['appearance', 'item', 'location', 'material']}}


def assess_messages(rows, metadata_messages):
    def blocks(row):
        return row.get('content') or []
    thoughts = [r for r in rows if r.get('display_kind') == 'thought' and r.get('text', '').strip()
        and any(b.get('type') in {'tool_use', 'tool_call', 'tool-call'} for b in blocks(r))]
    answers = [r for r in rows if r.get('display_kind') == 'answer' and r.get('text', '').strip()]
    metadata_by_id = {r.get('message_id'): r for r in metadata_messages}
    return {'public_tool_process_saved': bool(thoughts), 'visible_final_answer_saved': bool(answers) and rows[-1] in answers,
        'final_reports_empty_person_count': bool(answers) and any(token in answers[-1]['text'] for token in ['0', '零']),
        'no_private_blocks_in_visible_records': bool(rows) and all(b.get('type') not in PRIVATE_TYPES for r in rows for b in blocks(r)),
        'metadata_and_chat_text_match': bool(rows) and all(r.get('message_id') in metadata_by_id and
            metadata_by_id[r['message_id']].get('text') == r.get('text') and
            metadata_by_id[r['message_id']].get('display_kind') == r.get('display_kind') for r in rows),
        'public_raw_preserved': bool(rows) and all(r.get('full_output') for r in rows)}


def validate(ops, fixture, screenplay, calls, report):
    from narrative.chat_store import ChatStore
    directory = Path(report['audit_directory'])
    audit = json.loads((directory / 'task.json').read_text(encoding='utf-8'))
    chat = ChatStore(ops.project_info().path).load(audit['chat_id'])
    rows = [m for m in chat['messages'] if m.get('task_id') == audit['task_id'] and m.get('kind') == 'model_output']
    metadata = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(directory.glob('metadata-*.json'))]
    messages = [m for group in metadata for m in group.get('model_messages', [])]
    summary = ops.read_task_write_summary(audit['task_id'], audit['chat_id'])
    report['thought_visibility_evidence'] = {'model_rows': rows, 'metadata_messages': messages, 'program_summary': summary,
        'manual_review': 'Read actual public progress and final person-count answer. No host-generated progress is added; raw private reasoning remains audit-only.'}
    return assess_messages(rows, messages) | {'business_domains_unchanged': business_snapshot(ops, fixture) == fixture['before'],
        'only_read_tool_executed': bool(audit.get('steps')) and all(s.get('tool') == 'project_info' and s.get('ok') for s in audit['steps']),
        'program_summary_normal': summary.get('status') == 'completed' and not summary.get('has_changes') and bool(summary.get('compact_text'))}


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = 'thought-visibility-real/1'
    qa.FIXTURE_ORIGIN = 'New isolated empty project and source; real public text and final answer only, no synthetic progress or business writes.'
    return qa.main(validate=validate, required_tools=['project_info'], use_writing_target=False, prompt_factory=lambda _: TASK)


if __name__ == '__main__':
    raise SystemExit(main())
