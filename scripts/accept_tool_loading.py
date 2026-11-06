"""Single isolated read-only task observes real per-request lazy tool exposure."""
from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path

import accept_screenplay as qa

TEXT = '窗边的登记簿尚未填写。'
TASK = '请通过工程工具查询当前工程的剧本列表，回答有哪些剧本。只读，不创建或修改任何内容。'
PROVIDER_REQUESTS = []


def seed(ops):
    ops.save_harness_settings({'provider': 'ark', 'model': 'ark-code-latest', 'reasoning_effort': 'low',
        'context_window': 1_000_000, 'output_reserve': 131_072, 'compaction_reserve': 65_536,
        'compaction_max_tokens': 32_768, 'max_tokens': 131_072})
    source = ops.import_source('按需工具只读样本', TEXT)
    script = ops.create_screenplay('空白登记簿', reason='按需工具只读验收种子')['screenplay']
    return {'source_id': source, 'screenplay_id': script['id'], 'before_source': ops.source(source),
        'before_screenplay': ops.read_screenplay(script['id']), 'before_list': ops.read_screenplay(),
        'before_properties': ops.property_records()}


def sdk_output_schema_errors(messages):
    """Only SDK bridge output-contract rejections, not ordinary business errors."""
    found = []
    for message in messages:
        if not isinstance(message, dict) or message.get('role') != 'tool':
            continue
        content = message.get('content')
        texts = [content] if isinstance(content, str) else [b.get('text', '') for b in content or [] if isinstance(b, dict)]
        for text in texts:
            if not isinstance(text, str):
                continue
            match = re.match(r'^Error: tool "([^"\n]+)" (returned invalid output:|returned invalid output schema:|invalid output schema:)', text)
            if match:
                found.append({'code': 'sdk_tool_output_schema_rejected', 'tool': match.group(1),
                    'tool_call_id': message.get('tool_call_id'), 'reason': match.group(2).rstrip(':')})
    return found


def external_abort_reason():
    errors = [error for request in PROVIDER_REQUESTS for error in request.get('sdk_output_schema_errors', [])]
    if errors:
        return 'External QA stopped: SDK tool output schema rejected (' + errors[0]['tool'] + '); no retry.'
    return None


@contextmanager
def capture_provider_tools(records):
    """QA-only actual wire capture: names/byte size, never prompts or headers."""
    from narrative import model_gateway
    lock = threading.Lock()
    patched = []
    for cls in [model_gateway._UpstreamHTTPConnection, model_gateway._UpstreamHTTPSConnection]:
        original = cls.request
        owned = 'request' in cls.__dict__

        def request(self, method, url, body=None, headers=None, *, encode_chunked=False, _original=original):
            if method == 'POST' and isinstance(body, (bytes, str)):
                payload = json.loads(body)
                names = [(t.get('function') or {}).get('name', '') for t in payload.get('tools', [])]
                schema_errors = sdk_output_schema_errors(payload.get('messages', []))
                with lock:
                    records.append({'index': len(records) + 1, 'tool_names': names,
                        'body_bytes': len(body.encode() if isinstance(body, str) else body),
                        **({'sdk_output_schema_errors': schema_errors} if schema_errors else {})})
            return _original(self, method, url, body, headers or {}, encode_chunked=encode_chunked)

        patched.append((cls, original, owned))
        cls.request = request
    try:
        yield
    finally:
        for cls, original, owned in reversed(patched):
            if owned:
                cls.request = original
            else:
                del cls.request


def canonical(name):
    return str(name).rsplit('/', 1)[-1].rsplit('.', 1)[-1]


def assess_loading(requests, steps, sessions, *, load_tool='load_tools'):
    catalogs = [{canonical(name) for name in r['tool_names']} for r in requests]
    load_indices = [i for i, s in enumerate(steps) if canonical(s.get('tool')) == load_tool and s.get('ok')]
    discoveries = [i for i, s in enumerate(steps) if canonical(s.get('tool')) == 'list_available_tools' and s.get('ok')]
    reads = [i for i, s in enumerate(steps) if canonical(s.get('tool')) == 'read_screenplay' and s.get('ok')]
    forbidden = {'apply_storyboard_changes', 'generate_storyboard_sketch', 'render_storyboard_style',
        'prepare_storyboard_video', 'submit_storyboard_video', 'export_storyboard_video'}
    return {'initial_catalog_omits_read_screenplay': bool(catalogs) and 'read_screenplay' not in catalogs[0],
        'later_catalog_has_read_screenplay': any('read_screenplay' in catalog for catalog in catalogs[1:]),
        'discovery_before_load': bool(discoveries and load_indices) and min(discoveries) < min(load_indices),
        'load_before_business_read': bool(load_indices and reads) and min(load_indices) < min(reads),
        'unloaded_large_storyboard_tools_hidden': bool(catalogs) and all(not catalog & forbidden for catalog in catalogs),
        'same_sdk_session': bool(sessions) and len(set(sessions)) == 1}


def assess_sdk_tool_responses(session_rows, expected_list):
    """Require the SDK-visible response, not merely a successful host dispatch."""
    calls = {}
    messages = [row.get('data', {}).get('message', {}) for row in session_rows]
    for message in messages:
        for block in message.get('content', []) or []:
            if not isinstance(block, dict) or canonical(block.get('name')) != 'read_screenplay':
                continue
            args = block.get('arguments', block.get('input', {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            if isinstance(args, dict) and args.get('screenplay_id') is None and block.get('id'):
                calls[block['id']] = args
    responses = []
    for message in messages:
        if message.get('role') != 'tool' or message.get('toolCallId') not in calls:
            continue
        texts = [block.get('text', '') for block in message.get('content', []) or []
            if isinstance(block, dict) and block.get('type') == 'text']
        decoded = []
        for text in texts:
            try:
                decoded.append(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                pass
        responses.append({'tool_call_id': message['toolCallId'], 'is_error': message.get('isError'),
            'content': message.get('content'), 'actual_fixture_list': any(value == expected_list for value in decoded)})
    return {'sdk_received_actual_list_without_error': any(
        r['is_error'] is not True and r['actual_fixture_list'] for r in responses)}, responses


def usage_breakdown(metadata):
    from narrative.usage_statistics import normalize_usage
    requests = [r for group in metadata for r in group.get('model_requests', [])]
    normalized = [normalize_usage(r.get('usage')) for r in requests]

    def exact(field):
        values = [r[field] for r in normalized]
        return sum(values) if values and all(v is not None for v in values) else None

    uncached = [r['uncached_input_tokens'] for r in normalized if r['uncached_input_tokens'] is not None]
    return {'total_input': exact('input_tokens'), 'cache_hit': exact('cache_read_tokens'),
        'uncached_known': sum(uncached) if uncached else None, 'output': exact('output_tokens'),
        'total': exact('total_tokens'), 'requests': len(requests), 'uncached_covered_requests': len(uncached),
        'missing_usage_requests': sum(not isinstance(r.get('usage'), dict) for r in requests),
        'note': 'Cache hits are part of total_input, not added again; null means unreported/incomplete, not zero.'}


def validate(ops, fixture, screenplay, calls, report):
    directory = Path(report['audit_directory'])
    audit = json.loads((directory / 'task.json').read_text(encoding='utf-8'))
    metadata = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(directory.glob('metadata-*.json'))]
    report['usage_breakdown'] = usage_breakdown(metadata)
    sessions = [m['session_id'] for m in metadata if m.get('session_id')]
    steps = audit.get('steps', [])
    checks = assess_loading(PROVIDER_REQUESTS, steps, sessions)
    session_files = [p for p in (directory.parents[1] / 'home' / 'sessions').rglob('session.v4.jsonl') if p.parent.name in sessions]
    session_rows = [json.loads(line) for p in session_files for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]
    sdk_checks, sdk_responses = assess_sdk_tool_responses(session_rows, fixture['before_list'])
    checks.update(sdk_checks)
    reads = [s for s in steps if canonical(s.get('tool')) == 'read_screenplay' and s.get('ok')]
    # Tool presentation may wrap the Operations list; preserve it unmodified for review.
    report['tool_loading_evidence'] = {'provider_requests': PROVIDER_REQUESTS, 'steps': steps,
        'session_ids': sessions, 'sdk_list_responses': sdk_responses, 'session_files': [str(p) for p in session_files], 'expected_screenplay_list': fixture['before_list'],
        'manual_review': 'Verify actual final answer names the seeded screenplay and the read tool result is the unchanged list; no model result was repaired.'}
    from narrative.chat_store import ChatStore
    chat = ChatStore(ops.project_info().path).load(audit['chat_id'])
    answers = [m.get('text', '') for m in chat['messages'] if m.get('task_id') == audit['task_id'] and m.get('role') == 'assistant' and m.get('display_kind', 'answer') == 'answer']
    checks.update(read_returns_actual_screenplay_list=any((r.get('args') or {}).get('screenplay_id') is None and r.get('result') == fixture['before_list'] for r in reads),
        final_answer_names_seeded_screenplay=bool(answers) and '空白登记簿' in answers[-1], screenplay_unchanged=screenplay == fixture['before_screenplay'],
        screenplay_list_unchanged=ops.read_screenplay() == fixture['before_list'],
        source_unchanged=ops.source(fixture['source_id']) == fixture['before_source'],
        properties_unchanged=ops.property_records() == fixture['before_properties'], business_read_completed=bool(reads))
    return checks


def main():
    qa.seed, qa.TASK, qa.TEXT = seed, TASK, TEXT
    qa.PROMPT_VERSION = 'tool-loading-real/3'
    qa.FIXTURE_ORIGIN = 'New isolated source plus one empty screenplay. Read-only task, real tool discovery/load/read, no warmup or retries.'
    PROVIDER_REQUESTS.clear()
    with capture_provider_tools(PROVIDER_REQUESTS):
        return qa.main(validate=validate, required_tools=['read_screenplay'], use_writing_target=False,
            prompt_factory=lambda _: TASK, external_abort=external_abort_reason)


if __name__ == '__main__':
    raise SystemExit(main())
