"""One paid-task QA of draft repair; the deliberately invalid seed is synthetic."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import accept_screenplay as qa

TEXT = '甲与乙在门口会面。'
TASK = '修正工程中已有的暂存提取。原文及全部必要信息已经提供，直接只补反馈指出的错误字段；不要重发整稿、另建提取或修改其他事实。通过后自然结束，账目由程序显示。'


def seed(ops):
    ops.save_harness_settings({'provider': 'ark', 'model': 'ark-code-latest', 'reasoning_effort': 'low',
        'context_window': 1_000_000, 'output_reserve': 131_072, 'compaction_reserve': 65_536,
        'compaction_max_tokens': 32_768, 'max_tokens': 131_072})
    source_id = ops.import_source('暂存字段修补合成原文', TEXT)
    draft = {'nodes': [
        {'type': 'person', 'name': '甲', 'quotes': ['甲']},
        {'type': 'person', 'name': '乙', 'quotes': ['乙']},
        {'type': 'location', 'name': '门口', 'quotes': ['门口']},
        {'type': 'scene', 'name': '门口会面', 'quotes': []},
        {'type': 'event', 'name': '甲乙会面', 'scene': '@4', 'participants': ['@1', '@2'], 'quotes': [TEXT]},
    ], 'properties': [{'node': '@5', 'property': '地点', 'value': '@3', 'value_type': 'node',
                       'op': 'set', 'at_event': '@5', 'quotes': ['门口']}]}
    staged = ops.submit_extraction(source_id, draft)
    assert staged['status'] == 'staged' and not ops.graph_snapshot()['nodes']
    screenplay = ops.create_screenplay('只读验收空容器', reason='复用统一验收器')['screenplay']
    return {'source_id': source_id, 'screenplay_id': screenplay['id'], 'submission': staged,
            'synthetic_invalid_draft': draft, 'source_before': ops.source(source_id)}


def prompt(fixture):
    return (TASK + '\n原文：' + TEXT + '\n暂存问题：' + json.dumps(fixture['submission'], ensure_ascii=False)
            + '\n修补使用此暂存的submission_revision，nodes索引3对应@4场景，仅补quotes即可；无需再读取已提供的原文或整稿。')


def validate(ops, fixture, screenplay, calls, report):
    identifier = fixture['submission']['submission_id']
    current = ops.read_extraction_submission(identifier)
    nodes = ops.read_extraction_submission(identifier, section='nodes')['entries']
    properties = ops.read_extraction_submission(identifier, section='properties')['entries']
    expected = copy.deepcopy(fixture['synthetic_invalid_draft'])
    actual = {'nodes': [entry['item'] for entry in nodes], 'properties': [entry['item'] for entry in properties]}
    repaired_quotes = actual['nodes'][3].get('quotes', [])
    expected['nodes'][3]['quotes'] = repaired_quotes
    audit = json.loads((Path(report['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
    steps = audit.get('steps', [])
    patches = [step for step in steps if step.get('tool') == 'patch_extraction_submission']
    checked = ops.check_project()
    report['staging_evidence'] = {'seed_origin': 'synthetic intentionally invalid fixture, not model-authored extraction',
        'before': fixture['submission'], 'after': current, 'model_patch_calls': patches,
        'stored_draft': actual, 'static_check': checked}
    return {'patched_existing_submission': len(patches) == 1 and patches[0].get('result', {}).get('status') == 'accepted',
            'no_full_resubmission': not any(s.get('tool') == 'submit_extraction' for s in steps),
            'only_problem_field_changed': actual == expected,
            'valid_repaired_quotes': bool(repaired_quotes) and all(isinstance(q, str) and q and q in TEXT for q in repaired_quotes),
            'automatically_committed': current['submission_status'] == 'committed' and len(ops.graph_snapshot()['nodes']) == 5,
            'static_no_errors': checked['counts'].get('error', 0) == 0,
            'source_unchanged': ops.source(fixture['source_id']) == fixture['source_before']}


def main():
    qa.seed, qa.TEXT, qa.TASK = seed, TEXT, TASK
    qa.PROMPT_VERSION = 'extraction-staging-field-repair/1'
    qa.FIXTURE_ORIGIN = 'Synthetic invalid draft staged by the host; real model authors only the repair. No manual post-response correction or retries.'
    return qa.main(validate=validate, required_tools=['patch_extraction_submission'], prompt_factory=prompt,
                   use_writing_target=False, allow_extraction_change=True)


if __name__ == '__main__':
    raise SystemExit(main())
