"""One real no-tool request checks raw cache counters against task/chat ledgers."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa
from accept_usage_statistics import seed
from accept_usage_statistics import validate as validate_usage

TASK = '这是缓存计量显示的隔离测试。不要调用工具，不要读取或修改工程，只回复“缓存计量测试完成”。'


def validate(ops, fixture, screenplay, calls, report):
    checks = validate_usage(ops, fixture, screenplay, calls, report)
    audit = json.loads((Path(report['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
    requests = [request for turn in audit.get('calls', []) for request in turn.get('model_requests', [])]
    raw = requests[-1].get('usage') or {} if requests else {}
    cached = (raw.get('prompt_tokens_details') or {}).get('cached_tokens')
    inclusive = raw.get('prompt_tokens')
    task = report['usage_evidence']['statistics']['task_usage']
    chat = report['usage_evidence']['statistics']['chat_usage']
    rate = cached / inclusive if isinstance(cached, int) and isinstance(inclusive, int) and inclusive > 0 else None
    known = isinstance(cached, int) and isinstance(inclusive, int)
    checks.update(cache_raw_counter_known=known,
        cache_task_exact=task['cache_read_tokens'] == cached and task['cache_hit_rate'] == rate,
        cache_chat_exact=chat['cache_read_tokens'] == cached and chat['cache_hit_rate'] == rate,
        cache_zero_is_valid=not (cached == 0) or (rate == 0 and task['cache_hit_rate'] == 0),
        cache_coverage_exact=task['cache_covered_requests'] == chat['cache_covered_requests'] == (1 if known else 0),
        inclusive_input_not_double_counted=known and task['uncached_input_tokens'] + task['cache_read_tokens'] == inclusive,
        one_task_chat_count=task['requests'] == chat['requests'] == 1)
    report['cache_evidence'] = {'raw_provider_cache_read': cached, 'raw_provider_inclusive_input': inclusive,
        'expected_rate': rate, 'task_usage': task, 'chat_usage': chat,
        'scope': 'One isolated short request only. A zero cache hit is a valid measured result, not a failed warmup. The UI reads this same Operations ledger. Cache hits do not imply exemption from provider request/token rate limits.'}
    return checks


def main():
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = 'cache-statistics-real/1'
    qa.FIXTURE_ORIGIN = 'New isolated project/chat; one sentence without tools. No cache warmup/retry request.'
    return qa.main(validate=validate, required_tools=[], use_writing_target=False, prompt_factory=lambda _: TASK)


if __name__ == '__main__':
    raise SystemExit(main())
