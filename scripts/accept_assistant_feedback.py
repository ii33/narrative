"""One minimal real tool batch verifies completed feedback and public results."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_usage_statistics import seed

from narrative.chat_store import ChatStore


def prompt(fixture):
    return ('这是工具反馈显示测试。请调用project_info，以及read_screenplay(screenplay_id='
            + fixture['screenplay_id'] + ')读取本工程剧本，可以同一批调用。之后只回答“反馈检查通过”。不要调用其他工具，不修改任何数据。')


def validate(ops, fixture, screenplay, calls, report):
    audit = json.loads((Path(report['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
    chat = ChatStore(ops.project_info().path).load(audit['chat_id'])
    replies = [row for row in chat['messages'] if row.get('role') == 'assistant' and row.get('text')]
    steps = audit['steps']
    public = audit['public_steps']
    summary = next((row for row in reversed(chat['messages']) if row.get('kind') == 'task_summary'), {})
    report['feedback_evidence'] = {'public_steps': public, 'assistant_messages': replies,
        'program_summary': summary, 'host_cleanup': audit.get('host_cleanup'),
        'ui_verification': 'Real Qt regressions separately project full_output, null arguments, matched and unmatched actual results, and completed answers with no QML exceptions.'}
    return {'two_real_read_tools': {row['tool'] for row in steps} == {'project_info', 'read_screenplay'} and len(steps) == 2,
        'no_failed_calls': all(row.get('ok') is True for row in steps),
        'all_public_results_formatted': len(public) == 2 and all(row.get('output_text') and row.get('output_text') != '等待工具结果' for row in public),
        'final_response_persisted': bool(replies) and '反馈检查通过' in replies[-1]['text'],
        'program_feedback_persisted': bool(summary.get('commit_summary')),
        'task_truly_completed': audit['status'] == 'completed',
        'no_unfinished_host_commands': not (audit.get('host_cleanup') or {}).get('cleanup_receipts'),
        'screenplay_unchanged': screenplay == fixture['before_screenplay']}


def main():
    qa.seed = seed
    qa.PROMPT_VERSION = 'assistant-feedback-real/1'
    qa.FIXTURE_ORIGIN = 'New isolated project; one real task reads project and screenplay, returns feedback, performs no host commands or writes.'
    return qa.main(validate=validate, required_tools=['project_info', 'read_screenplay'],
                   use_writing_target=False, prompt_factory=prompt)


if __name__ == '__main__':
    raise SystemExit(main())
