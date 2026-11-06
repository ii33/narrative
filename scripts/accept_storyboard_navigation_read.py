"""One read-only real assistant smoke check; Qt owns navigation verification."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_storyboard import seed as seed_scene

TASK = '仅调用一次read_storyboard，unit_id={unit_id}，确认已读取本场分镜数据。不要调用其他工具，不修改任何数据，不生成图像或视频。然后简短回复已读取。'


def seed(ops):
    fixture = seed_scene(ops)
    ops.save_harness_settings({'provider': 'ark', 'model': 'ark-code-latest', 'reasoning_effort': 'low',
        'context_window': 1_000_000, 'output_reserve': 131_072, 'compaction_reserve': 65_536,
        'compaction_max_tokens': 32_768, 'max_tokens': 131_072})
    board = ops.apply_storyboard_changes(fixture['unit_id'], 0, [{'op': 'create_shot', 'title': '门口交接', 'duration_ms': 2000, 'source_block_ids': [fixture['block']]}])
    fixture.update(before_board=board, before_screenplay=ops.read_screenplay(fixture['screenplay_id']))
    return fixture


def validate(ops, fixture, screenplay, calls, report):
    from narrative.tool_api import TOOLS
    audit = json.loads((Path(report['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
    steps = audit.get('steps', [])
    reads = [s for s in steps if s.get('tool') == 'read_storyboard' and s.get('ok')]
    report['navigation_read_evidence'] = {'reads': reads, 'scope': 'Only unchanged Operations/model read accessibility. Separate real Qt tests validate the two navigation surfaces and shared selection; this model task does not prove UI layout.'}
    return {'exactly_one_correct_read': len(steps) == len(reads) == 1 and reads[0]['args']['unit_id'] == fixture['unit_id'],
        'actual_board_returned': bool(reads) and reads[0]['result'] == fixture['before_board'],
        'no_business_writes': all(not TOOLS[s['tool']].writes for s in steps),
        'board_unchanged': ops.read_storyboard(fixture['unit_id']) == fixture['before_board'],
        'screenplay_unchanged': screenplay == fixture['before_screenplay']}


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = 'storyboard-navigation-read/1'
    qa.FIXTURE_ORIGIN = 'New isolated handoff scene and one controlled shot. Model reads only; no generation or writes.'
    return qa.main(validate=validate, required_tools=['read_storyboard'], use_writing_target=False, prompt_factory=lambda f: TASK.format(**f))


if __name__ == '__main__':
    raise SystemExit(main())
