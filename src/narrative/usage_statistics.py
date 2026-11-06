"""Read-only usage ledger projection and process-local live observations.

Provider requests are the consumption authority. Transcript messages and SDK
usage copies never add a second charge. Context is one generation, not a sum.
"""
from __future__ import annotations

import copy
import json
import threading
from datetime import UTC, datetime
from typing import Any

METRICS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'total_tokens')
_LOCK = threading.RLock()
_LIVE: dict[tuple[str, str, str], dict] = {}
_FILES: dict[str, tuple[int, int, dict]] = {}
_HISTORY: dict[tuple[str, str | None], dict] = {}
_SEQUENCE = 0


def _light_usage(value):
    if not isinstance(value, dict):
        return None
    keys = {'prompt_tokens', 'input_tokens', 'completion_tokens', 'output_tokens', 'total_tokens',
            'prompt_cache_hit_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'cache_read_tokens', 'cache_write_tokens'}
    result: dict[str, Any] = {key: _number(value[key]) for key in keys if key in value}
    for key in ('prompt_tokens_details', 'input_tokens_details'):
        details = value.get(key)
        if isinstance(details, dict):
            result[key] = {name: _number(details[name]) for name in ('cached_tokens', 'cache_write_tokens') if name in details}
    return result


def _light_metadata(value):
    """Discard content/tool payloads before a snapshot reaches any cache."""
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in ('usage_turn_id', 'turn_id', 'sdk_turn', 'provider', 'model', 'usage_incomplete', 'gateway_upstream_attempt_count') if key in value}
    for field, keys in {
        'context_policy': ('context_window', 'compaction_threshold_tokens', 'effective_output_reserve', 'output_reserve'),
        'native_context': ('latest_input_tokens', 'latest_input_source', 'current_estimate_tokens', 'current_estimate_source', 'state', 'stale', 'context_window', 'compaction_threshold_tokens'),
    }.items():
        source = value.get(field)
        if isinstance(source, dict):
            result[field] = {key: source[key] for key in keys if key in source}
    requests = value.get('model_requests')
    if isinstance(requests, list):
        result['model_requests'] = []
        for request in requests:
            if isinstance(request, dict):
                row = {key: request[key] for key in ('request_id', 'id', 'purpose', 'complete', 'max_tokens', 'finish_reason', 'usage_sampled_at') if key in request}
                row['usage'] = _light_usage(request.get('usage'))
                result['model_requests'].append(row)
    elif 'usage' in value:
        raw = value['usage']
        result['usage'] = [_light_usage(item) for item in raw] if isinstance(raw, list) else _light_usage(raw)
    return result


def _light_audit(value):
    if not isinstance(value, dict):
        raise ValueError('用量审计记录须为对象')
    calls = value.get('calls')
    return {key: value.get(key) for key in ('task_id', 'id', 'chat_id')} | {'calls': [_light_metadata(m) for m in calls if isinstance(m, dict)] if isinstance(calls, list) else []}


def _number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def normalize_usage(usage):
    usage = usage if isinstance(usage, dict) else {}
    def first(*values):
        return next((v for v in map(_number, values) if v is not None), None)
    details = usage.get('prompt_tokens_details') or usage.get('input_tokens_details') or {}
    if not isinstance(details, dict):
        details = {}
    input_tokens = first(usage.get('prompt_tokens'), usage.get('input_tokens'))
    output_tokens = first(usage.get('completion_tokens'), usage.get('output_tokens'))
    cache_read = first(details.get('cached_tokens'), usage.get('prompt_cache_hit_tokens'), usage.get('cache_read_input_tokens'), usage.get('cache_read_tokens'))
    cache_write = first(details.get('cache_write_tokens'), usage.get('cache_creation_input_tokens'), usage.get('cache_write_tokens'))
    # Messages reports non-cache input separately; OpenAI prompt_tokens already
    # includes cached input. Preserve unknown cache counters as null.
    if 'prompt_tokens' not in usage and input_tokens is not None and any(key in usage for key in ('cache_read_input_tokens', 'cache_creation_input_tokens')):
        input_tokens += sum(v for v in (cache_read, cache_write) if v is not None)
    total = first(usage.get('total_tokens'))
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    # Provider cache reads must be a subset of that same inclusive input.
    cache_invalid = cache_read is not None and input_tokens is not None and cache_read > input_tokens
    if cache_invalid:
        cache_read = None
    uncached = input_tokens-cache_read if input_tokens is not None and cache_read is not None else None
    # Cache counters are a breakdown, never added to the provider input count.
    return {'input_tokens': input_tokens, 'output_tokens': output_tokens,
            'cache_read_tokens': cache_read, 'uncached_input_tokens': uncached, 'cache_read_invalid': cache_invalid,
            'cache_write_tokens': cache_write,
            'total_tokens': total,
            'complete': input_tokens is not None and output_tokens is not None and total is not None}


def _project_key(path):
    from .project_package import layout
    return str(layout(path).db.resolve())


def publish_live_usage(project_path, chat_id, task_id, metadata, *, turn_id=None, event='usage', sampled_at=None):
    """Replace a live task/SDK-turn observation; never writes historical files."""
    global _SEQUENCE
    key = (_project_key(project_path), str(chat_id), str(task_id))
    stamp = sampled_at or datetime.now(UTC).isoformat()
    with _LOCK:
        _SEQUENCE += 1
        prior = _LIVE.get(key, {'turns': {}})
        turns = dict(prior['turns'])
        turn = str(turn_id if turn_id is not None else metadata.get('usage_turn_id', metadata.get('turn_id', metadata.get('sdk_turn', 1))))
        turns[turn] = _light_metadata(metadata)
        _LIVE[key] = {'turns': turns, 'event': event, 'sampled_at': stamp, 'sequence': _SEQUENCE}
    return read_statistics(project_path, str(chat_id), str(task_id), _refresh_history=False)


def _read_json(path):
    stat = path.stat()
    key = str(path)
    with _LOCK:
        cached = _FILES.get(key)
    signature = (stat.st_mtime_ns, stat.st_size)
    if cached and cached[:2] == signature:
        return cached[2], stat.st_mtime
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError('用量记录须为对象')
    value = _light_audit(raw) if path.name == 'task.json' else _light_metadata(raw) | {key: raw.get(key) for key in ('task_id', 'chat_id')}
    with _LOCK:
        _FILES[key] = (*signature, value)
    return value, stat.st_mtime


def _records(metadata, turn):
    requests = metadata.get('model_requests')
    if isinstance(requests, list):
        return [(str(r.get('request_id') or r.get('id') or f'{turn}:{index}'), r) for index, r in enumerate(requests) if isinstance(r, dict)]
    # Some durable turn metadata records only the provider usage list. It is an
    # alternative source for that same turn, never additional to model_requests.
    usage = metadata.get('usage')
    if isinstance(usage, dict):
        usage = [usage]
    return [(f'{turn}:{index}', {'usage': value, 'purpose': 'generation', 'complete': value is not None}) for index, value in enumerate(usage or [])] if isinstance(usage, list) else []


def _aggregate(records):
    rows = [normalize_usage(r.get('usage')) for r in records]
    result = {}
    known = {}
    for metric in METRICS:
        values = [r[metric] for r in rows]
        present = [v for v in values if v is not None]
        known[metric] = sum(present) if present else None
        result[metric] = sum(present) if len(present) == len(values) and rows else None
    missing = [metric for metric in METRICS if any(r[metric] is None for r in rows)]
    result.update(requests=len(records), known_requests=sum(r['complete'] for r in rows), unknown_requests=sum(not r['complete'] for r in rows),
                  incomplete=bool(missing) or any(not r['complete'] for r in rows) or any(not r.get('complete', False) for r in records), incomplete_metrics=missing, known_totals=known)
    covered = [row for row in rows if row['input_tokens'] is not None and row['cache_read_tokens'] is not None]
    covered_input = sum(row['input_tokens'] for row in covered)
    covered_read = sum(row['cache_read_tokens'] for row in covered)
    known_rate = covered_read/covered_input if covered_input else None
    all_covered = bool(rows) and len(covered) == len(rows)
    known_uncached = covered_input-covered_read if covered else None
    result.update(cache_hit_rate=known_rate if all_covered else None, known_cache_hit_rate=known_rate,
                  cache_covered_requests=len(covered), cache_unknown_requests=len(rows)-len(covered),
                  cache_covered_input_tokens=covered_input, cache_covered_read_tokens=covered_read,
                  cache_invalid_requests=sum(row['cache_read_invalid'] for row in rows),
                  uncached_input_tokens=known_uncached if all_covered else None,
                  known_uncached_input_tokens=known_uncached)
    return result


def _context(turns, sampled_at, live):
    empty = {'input_tokens': None, 'source': 'unknown', 'stale': True, 'context_window': None, 'compression_threshold': None,
             'output_reserve': None, 'sampled_at': sampled_at, 'purpose': None, 'compaction_pending': False}
    if not turns:
        return empty
    metadata = turns[-1]
    policy = metadata.get('context_policy') or {}
    empty.update(context_window=policy.get('context_window'), compression_threshold=policy.get('compaction_threshold_tokens'),
                 output_reserve=policy.get('effective_output_reserve', policy.get('output_reserve')))
    native = metadata.get('native_context') or {}
    empty.update(context_window=native.get('context_window', empty['context_window']), compression_threshold=native.get('compaction_threshold_tokens', empty['compression_threshold']))
    all_requests = [r for index, turn in enumerate(turns) for _, r in _records(turn, index)]
    generations = [(index, r) for index, r in enumerate(all_requests) if r.get('purpose', 'generation') == 'generation']
    if not generations:
        estimate = _number(native.get('current_estimate_tokens'))
        return empty | {'input_tokens': estimate, 'source': 'estimated' if estimate is not None else 'unknown',
                        'stale': native.get('stale', True), 'compaction_pending': native.get('state') == 'compacting' or any(r.get('purpose') == 'compaction' for r in all_requests)}
    index, request = generations[-1]
    tokens = normalize_usage(request.get('usage'))['input_tokens']
    last_known_request = next((r for _, r in reversed(generations) if normalize_usage(r.get('usage'))['input_tokens'] is not None), None)
    last_known = normalize_usage(last_known_request.get('usage'))['input_tokens'] if last_known_request else None
    last_sampled_at = last_known_request.get('usage_sampled_at') if last_known_request else None
    later_compaction = any(r.get('purpose') == 'compaction' for r in all_requests[index + 1:])
    result = empty | {'input_tokens': tokens, 'source': 'actual' if tokens is not None else 'unknown',
                    'stale': not live or later_compaction or tokens is None or request.get('complete', False),
                    'purpose': 'last_generation', 'compaction_pending': later_compaction,
                    'sample_kind': 'last_request_input', 'request_complete': request.get('complete', False),
                    'last_generation_input_tokens': last_known, 'last_known_input_tokens': last_known,
                    'last_known_sampled_at': last_sampled_at, 'last_known_observed_at': sampled_at,
                    'last_known_is_current_request': last_known_request is request}
    if native:
        estimate = _number(native.get('current_estimate_tokens'))
        result.update(input_tokens=estimate if estimate is not None else tokens,
                      source='estimated' if estimate is not None else 'actual' if tokens is not None else 'unknown',
                      stale=native.get('stale', True), compaction_pending=native.get('state') == 'compacting' or later_compaction,
                      state=native.get('state'), estimate_source=native.get('current_estimate_source'))
    return result


def read_statistics(project_path, chat_id=None, task_id=None, *, _refresh_history=True):
    from .chat_store import ChatStore
    from .project_package import layout
    project = layout(project_path)
    store = ChatStore(project.db)
    if not chat_id:
        try:
            pointer = json.loads((store.root / '.active').read_text(encoding='utf-8'))
            chat_id = str(pointer['chat_id'])
        except (OSError, ValueError, KeyError, TypeError):
            rows = store.list()
            chat_id = rows[0]['id'] if rows else None
    history_key = (str(project.db.resolve()), chat_id)
    with _LOCK:
        history = _HISTORY.get(history_key)
    if chat_id and _refresh_history:
        loaded = store.load(chat_id)
        chat = {'id': loaded['id'], 'has_messages': bool(loaded.get('messages'))}
        del loaded
    else:
        chat = history.get('chat') if history else None
    tasks = {}
    diagnostics = []
    data_time = None
    root = project.extraction / 'assistant' / 'tasks'
    refresh = _refresh_history
    if not refresh and history is not None:
        tasks, diagnostics, data_time = copy.deepcopy(history['tasks']), list(history['diagnostics']), history['data_time']
    for path in sorted(root.glob('*/task.json')) if refresh and root.exists() else []:
        try:
            audit, modified = _read_json(path)
        except (OSError, ValueError) as exc:
            diagnostics.append({'code': 'usage_record_unreadable', 'message': f'{path.parent.name}: {type(exc).__name__}'})
            continue
        if audit.get('chat_id') != chat_id:
            continue
        identifier = str(audit.get('task_id') or audit.get('id') or path.parent.name)
        stamp = datetime.fromtimestamp(modified, UTC).isoformat()
        data_time = max(data_time or stamp, stamp)
        tasks[identifier] = {'turns': {str(m.get('usage_turn_id') or index + 1): m for index, m in enumerate(audit.get('calls') or []) if isinstance(m, dict)}, 'sampled_at': stamp, 'live': False, 'sequence': 0}
    if refresh and chat_id:
        owners = {turn: identifier for identifier, task in tasks.items() for turn in task['turns']}
        turn_root = project.extraction / 'assistant' / 'chats' / chat_id / 'turns'
        for path in sorted(turn_root.glob('*/metadata.json')) if turn_root.exists() else []:
            try:
                metadata, modified = _read_json(path)
            except (OSError, ValueError) as exc:
                diagnostics.append({'code': 'usage_record_unreadable', 'message': f'{path.parent.name}: {type(exc).__name__}'})
                continue
            if metadata.get('chat_id') not in (None, chat_id):
                continue
            turn = metadata.get('usage_turn_id')
            if not isinstance(turn, str) or not turn.strip():
                diagnostics.append({'code': 'usage_source_identity_missing', 'message': f'{path.parent.name}: 独立用量记录缺少usage_turn_id，无法确认与任务审计的关系，已跳过；统计不完整'})
                continue
            identifier = owners.get(turn) or str(metadata.get('task_id') or turn)
            stamp = datetime.fromtimestamp(modified, UTC).isoformat()
            data_time = max(data_time or stamp, stamp)
            prior = tasks.get(identifier, {'turns': {}, 'sampled_at': stamp, 'live': False, 'sequence': 0})
            if turn not in prior['turns'] or stamp > prior['sampled_at']:
                prior['turns'][turn] = metadata
            prior['sampled_at'] = max(prior['sampled_at'], stamp)
            tasks[identifier] = prior
    if refresh:
        cached_history = {'tasks': copy.deepcopy(tasks), 'diagnostics': list(diagnostics), 'data_time': data_time, 'chat': chat}
        with _LOCK:
            _HISTORY[history_key] = cached_history
    durable_owners = {turn: identifier for identifier, task in tasks.items() for turn in task['turns']}
    with _LOCK:
        live_observations = list(_LIVE.items())
    for (owner, owner_chat, identifier), observation in live_observations:
        if owner == str(project.db.resolve()) and owner_chat == chat_id:
            saved = tasks.get(identifier, {'turns': {}})
            turns = observation['turns'] | saved['turns'] if saved.get('sampled_at', '') >= observation['sampled_at'] else saved['turns'] | observation['turns']
            tasks[identifier] = saved | observation | {'turns': turns, 'live': True}
            stamp = observation['sampled_at']
            data_time = max(data_time or stamp, stamp)
    ordered = sorted(tasks.items(), key=lambda item: item[1]['sampled_at'])
    chosen_task = str(task_id) if task_id else ordered[-1][0] if ordered else None
    requests = {}
    for identifier, task in ordered:
        for turn, metadata in task['turns'].items():
            for request_id, request in _records(metadata, turn):
                scope = turn if metadata.get('usage_turn_id') else identifier + ':' + turn
                requests[(scope, request_id)] = (durable_owners.get(turn, identifier), request)
    selected = tasks.get(chosen_task, {})
    selected_turns = list(selected.get('turns', {}).values())
    context_task = ordered[-1][1] if ordered else {}
    context_turns = [metadata for _, task in ordered for metadata in task['turns'].values()]
    chat_usage = _aggregate([r for _, r in requests.values()])
    task_usage = _aggregate([r for owner, r in requests.values() if owner == chosen_task])
    if diagnostics:
        for summary in (chat_usage, task_usage):
            summary.update(incomplete=True, incomplete_reasons=sorted({d['code'] for d in diagnostics}),
                           cache_hit_rate=None, uncached_input_tokens=None, cache_unknown_requests=None)
    history_pending = not refresh and history is None
    if history_pending:
        chat_usage.update({metric: None for metric in METRICS})
        chat_usage.update(requests=None, incomplete=True, incomplete_reasons=['history_not_loaded'],
                          cache_hit_rate=None, uncached_input_tokens=None, cache_unknown_requests=None)
    if not requests and chat and chat.get('has_messages'):
        chat_usage.update(requests=None, incomplete=True, cache_hit_rate=None, uncached_input_tokens=None, cache_unknown_requests=None)
    return {'chat_id': chat_id, 'task_id': chosen_task, 'chat_usage': chat_usage,
            'task_usage': task_usage,
            'context': _context(context_turns, context_task.get('sampled_at'), context_task.get('live', False)),
            'data_updated_at': data_time, 'data_sequence': max((t['sequence'] for t in tasks.values()), default=0),
            'diagnostics': diagnostics, 'usage_available': bool(requests), 'has_chat': chat is not None,
            'task_turns': len(selected_turns), 'history_pending': history_pending}


class UsageStatisticsOperationsMixin:
    def project_info(self):
        raise NotImplementedError

    def read_usage_statistics(self, chat_id=None, task_id=None):
        return read_statistics(self.project_info().path, chat_id, task_id)
