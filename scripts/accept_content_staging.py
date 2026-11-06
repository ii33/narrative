"""One real model task repairs one non-extraction content field in isolation."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import accept_screenplay as qa

TEXT = '甲与乙在门口会面。'
TASK = '本次只验证工程内容暂存的局部修补。直接将已暂存排版设置的字号修为14，其他内容保持不变。反馈和字段路径已提供，不必read/list，不要重新save整份设置；成功后自然结束，账目由程序输出。'


def seed(ops):
    ops.save_harness_settings({'provider': 'ark', 'model': 'ark-code-latest', 'reasoning_effort': 'low',
        'context_window': 1_000_000, 'output_reserve': 131_072, 'compaction_reserve': 65_536,
        'compaction_max_tokens': 32_768, 'max_tokens': 131_072})
    source_id = ops.import_source('统一暂存隔离短文', TEXT)
    screenplay = ops.create_screenplay('验收只读空容器', reason='统一验收器')['screenplay']
    initial = ops.load_compile_settings()
    staged = ops.save_compile_settings(initial['settings'] | {'font_size_pt': -1}, initial['revision'], '合成非法字号，非模型提取')
    assert staged['status'] == 'staged'
    assert any(problem.get('path') == ['settings', 'font_size_pt'] for problem in staged['problems'])
    return {'source_id': source_id, 'screenplay_id': screenplay['id'], 'submission': staged,
            'initial_settings': initial, 'source_before': ops.source(source_id)}


def prompt(fixture):
    return TASK + '\n暂存问题：' + json.dumps(fixture['submission'], ensure_ascii=False) + '\npatch路径为["settings","font_size_pt"]，expected_revision使用submission_revision。'


def validate(ops, fixture, screenplay, calls, report):
    current = ops.read_content_submission(fixture['submission']['submission_id'])
    settings = ops.load_compile_settings()
    expected = copy.deepcopy(fixture['initial_settings']['settings'])
    expected['font_size_pt'] = 14
    audit = json.loads((Path(report['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
    steps = audit.get('steps', [])
    patches = [step for step in steps if step.get('tool') == 'patch_content_submission']
    report['content_staging_evidence'] = {'fixture_origin': 'Intentionally invalid synthetic settings; model authors only the field repair.',
        'before': fixture['submission'], 'after': current, 'model_patch_calls': patches,
        'original_settings': fixture['initial_settings'], 'stored_settings': settings}
    return {'patched_existing_submission': len(patches) == 1 and patches[0].get('result', {}).get('status') == 'accepted',
        'no_full_resubmission': not any(step.get('tool') in {'save_compile_settings', 'submit_extraction'} for step in steps),
        'only_problem_field_changed': settings['settings'] == expected,
        'automatically_committed': current['submission_status'] == 'committed' and not current['problems'],
        'single_settings_revision': settings['revision'] == fixture['initial_settings']['revision'] + 1,
        'source_unchanged': ops.source(fixture['source_id']) == fixture['source_before']}


def main():
    qa.seed, qa.TEXT, qa.TASK = seed, TEXT, TASK
    qa.PROMPT_VERSION = 'unified-content-staging-field-repair/1'
    qa.FIXTURE_ORIGIN = 'Synthetic invalid compile settings, not model-authored extraction. Real model provides one content patch; no manual post-response repair or retry.'
    return qa.main(validate=validate, required_tools=['patch_content_submission'], prompt_factory=prompt,
                   use_writing_target=False)


if __name__ == '__main__':
    raise SystemExit(main())
