"""Persistent extraction drafts with stable slots and atomic automatic acceptance."""
from __future__ import annotations

import json
import re
from copy import deepcopy

SECTIONS = ('nodes', 'properties', 'uncertainties', 'exclusions')
PREFIX = 'extraction_submission:'
COUNTER = 'next_extraction_submission_number'


def _load(db, identifier):
    row = db.execute('SELECT value FROM meta WHERE key=?', (PREFIX + identifier,)).fetchone()
    if row is None:
        raise ValueError('提取暂存不存在')
    return json.loads(row[0])


def _save(db, row):
    db.execute('INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (PREFIX + row['submission_id'], json.dumps(row, ensure_ascii=False)))
    summary = {key: row.get(key) for key in ('submission_id', 'submission_revision', 'submission_status', 'changed_fields')}
    db.execute('INSERT INTO actions(id,revision,action,reason,summary) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,reason=excluded.reason,summary=excluded.summary',
        (row['submission_id'] + '-revision-' + str(row['submission_revision']), row['formal_revision'],
         'extraction_staging', row.get('last_reason', '首次提交'), json.dumps(summary, ensure_ascii=False)))



def _summary(row):
    return {'status': (row.get('commit_result') or {}).get('status', 'staged'), **{key: deepcopy(row[key]) for key in (
        'submission_id', 'submission_revision', 'submission_status', 'source_id', 'start', 'end',
        'formal_revision', 'problems', 'counts', 'validation_status')}}


def read(ops, submission_id, *, section=None, indices=None):
    if not isinstance(submission_id, str):
        raise ValueError('submission_id须为字符串')
    if section is not None and section not in SECTIONS:
        raise ValueError('section须为nodes/properties/uncertainties/exclusions')
    if indices is not None and (section is None or not isinstance(indices, list) or any(type(i) is not int or i < 0 for i in indices)):
        raise ValueError('indices须为指定section的非负整数索引数组')
    with ops.project.store.connect() as db:
        row = _load(db, submission_id)
    result = _summary(row)
    if section is not None:
        entries = row['result'].get(section, [])
        selected = range(len(entries)) if indices is None else indices
        if any(i >= len(entries) for i in selected):
            raise ValueError('暂存索引越界')
        result['section'] = section
        result['entries'] = [dict(index=i, item=deepcopy(entries[i]), **({'reference': f'@{i+1}'} if section == 'nodes' else {})) for i in selected]
    return result


def list_pending(ops, source_id=None, *, limit=20, after=0):
    if type(limit) is not int or not 1 <= limit <= 100 or type(after) is not int or after < 0:
        raise ValueError('limit须为1..100，after须为非负暂存编号')
    if source_id is not None and not isinstance(source_id, str):
        raise ValueError('source_id须为字符串')
    number_start = len(PREFIX + 'extraction-submission-') + 1
    with ops.project.store.connect() as db:
        rows = db.execute("SELECT value FROM meta WHERE key GLOB 'extraction_submission:*' "
            "AND json_extract(value,'$.submission_status')='staged' "
            "AND (? IS NULL OR json_extract(value,'$.source_id')=?) "
            "AND CAST(substr(key,?) AS INTEGER)>? ORDER BY CAST(substr(key,?) AS INTEGER) LIMIT ?",
            (source_id, source_id, number_start, after, number_start, limit+1)).fetchall()
    selected = [json.loads(row[0]) for row in rows[:limit]]
    return {'submissions': [_summary(row) for row in selected],
            'next_after': int(selected[-1]['submission_id'].rsplit('-', 1)[1]) if len(rows) > limit else None}


def _problems(raw, diagnostics, built=None):
    """Locate diagnostics using original slots, never sorted or parsed row indices."""
    node_ids = (built or {}).get('node_ids') or {}
    problems = []
    for diagnostic in diagnostics:
        if diagnostic.get('severity', 'error') not in {'error', 'warning'}:
            continue
        message = str(diagnostic.get('message') or diagnostic.get('reason') or diagnostic)
        identity = str(diagnostic.get('object_id') or '')
        targets = []
        explicit = re.search(r'(nodes|properties|uncertainties|exclusions)\[(\d+)\]', message)
        if explicit:
            section, number = explicit.group(1), int(explicit.group(2))
            targets.append({'section': section, 'index': number})
        else:
            for index, item in enumerate(raw.get('nodes') or []):
                if not isinstance(item, dict):
                    continue
                reference = f'@{index+1}'
                if identity and identity in {item.get('name'), reference, item.get('existing_id'), node_ids.get(reference)}:
                    targets.append({'section': 'nodes', 'index': index, 'reference': reference})
            for index, item in enumerate(raw.get('properties') or []):
                if not isinstance(item, dict):
                    continue
                prop, owner = str(item.get('property') or ''), str(item.get('node') or '')
                if identity and identity in {prop, owner + '.' + prop}:
                    targets.append({'section': 'properties', 'index': index})
                elif index in (built or {}).get('property_origins', {}).get(identity, []):
                    targets.append({'section': 'properties', 'index': index})
        fields = []
        if any(word in message for word in ('引文', 'quote', '证据文本')):
            fields = ['quotes']
        elif 'at_event' in message and 'op' in message:
            fields = ['at_event', 'op']
        elif 'value' in message:
            fields = ['value']
        for target in targets:
            if fields:
                target['fields'] = fields
                if len(fields) == 1:
                    target['field'] = fields[0]
            if target['section'] == 'nodes':
                target['reference'] = f"@{target['index']+1}"
        problem = {'severity': diagnostic.get('severity', 'error'), 'code': diagnostic.get('code', 'DRAFT_SCHEMA'),
                   'message': message, 'targets': targets}
        if identity:
            problem['object_id'] = identity
        if len(targets) == 1:
            problem.update(targets[0])
        problems.append(problem)
    return problems


def _cas(db, identifier, expected):
    current = _load(db, identifier)
    if current['submission_status'] != 'staged':
        from .content_errors import ContentGuardError
        raise ContentGuardError('暂存已完成入库，不能再次修改')
    if current['submission_revision'] != expected:
        from .content_errors import ContentConflictError
        raise ContentConflictError('暂存revision已变化，请重读问题部分')


def _evaluate(ops, row, expected, reason):
    from .conversation_session import record_business_action, use_chat
    from .extraction_draft import _compact, _prepare
    built = {}
    try:
        _data, built, prepared, outcome = _prepare(ops, row['source_id'], row['result'], row['start'], row['end'])
    except ValueError as exc:
        prepared = None
        outcome = {'valid': False, 'revision': ops.graph_project_info()['revision'],
                   'diagnostics': [{'severity': 'error', 'code': 'DRAFT_SCHEMA', 'message': str(exc)}]}
    row['validation_status'] = 'valid' if outcome['valid'] else 'invalid'
    row['formal_revision'] = outcome['revision']
    row['problems'] = _problems(row['result'], outcome.get('diagnostics', []), built)
    row['counts'] = {key: len(value) for key, value in row['result'].items() if isinstance(value, list)}
    row['last_reason'] = reason
    if not outcome['valid']:
        with ops.project.store.connect(write=True) as db:
            _cas(db, row['submission_id'], expected)
            _save(db, row)
        return _compact(outcome) | _summary(row) | {'status': 'staged'}

    def accepted(db, committed):
        _cas(db, row['submission_id'], expected)
        row.update(submission_status='committed', formal_revision=committed['revision'])
        row['commit_result'] = committed
        _save(db, row)

    note = {'purpose': '统一提取自动入库', 'submission_id': row['submission_id'],
            'submission_revision': row['submission_revision'], 'reason': reason,
            'uncertainties': row['result'].get('uncertainties') or [], 'exclusions': row['result'].get('exclusions') or []}
    with use_chat(ops) as chat:
        if prepared is None:
            with ops.project.store.connect(write=True) as db:
                revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
                if revision != outcome['revision']:
                    _cas(db, row['submission_id'], expected)
                    row.update(formal_revision=revision, validation_status='invalid', problems=[{
                        'severity': 'error', 'code': 'FORMAL_REVISION_CHANGED',
                        'message': '正式资料版本已变化，请重检暂存', 'targets': []}])
                    _save(db, row)
                    return _summary(row) | {'valid': False}
                committed = {'status': 'unchanged', 'revision': revision, 'committed_changes': []}
                accepted(db, committed)
        else:
            try:
                committed = ops.commit_changes(prepared, json.dumps(note, ensure_ascii=False), _transaction_callback=accepted)
            except ValueError as exc:
                if '正式资料版本已变化' not in str(exc):
                    raise
                row['problems'] = [{'severity': 'error', 'code': 'FORMAL_REVISION_CHANGED', 'message': str(exc), 'targets': []}]
                row['validation_status'] = 'invalid'
                row['formal_revision'] = ops.graph_project_info()['revision']
                with ops.project.store.connect(write=True) as db:
                    _cas(db, row['submission_id'], expected)
                    _save(db, row)
                return _summary(row) | {'status': 'staged', 'valid': False}
        outcome.update(committed)
        if built.get('node_ids'):
            outcome['node_ids'] = built['node_ids']
        try:
            record_business_action(ops, {'action': 'automatic_extraction', 'chat_id': chat['id'], 'source_id': row['source_id'], 'result': _compact(outcome) | _summary(row)})
        except Exception as exc:
            outcome['feedback_error'] = {'type': type(exc).__name__, 'message': str(exc)}
    return _compact(outcome) | _summary(row)


def submit(ops, source_id, result, start=0, end=None):
    from .extraction_draft import _source_range
    if not isinstance(result, dict):
        raise ValueError('result须为JSON对象')
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    _source, _text, start, end = _source_range(ops, source_id, start, end)
    with ops.project.store.connect(write=True) as db:
        counter = db.execute('SELECT value FROM meta WHERE key=?', (COUNTER,)).fetchone()
        number = int(counter[0]) if counter else 1
        db.execute('INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (COUNTER, str(number+1)))
        row = {'submission_id': f'extraction-submission-{number}', 'submission_revision': 1, 'submission_status': 'staged',
               'source_id': source_id, 'start': start, 'end': end, 'formal_revision': ops.graph_project_info()['revision'],
               'result': deepcopy(result), 'validation_status': 'pending',
               'problems': [{'severity': 'info', 'code': 'CHECK_PENDING', 'message': '草稿已暂存，静态检查尚未完成', 'targets': []}],
               'counts': {key: len(value) for key, value in result.items() if isinstance(value, list)}}
        _save(db, row)
    return _evaluate(ops, row, 1, '首次提交')


def patch(ops, submission_id, expected_revision, patches, reason):
    if type(expected_revision) is not int or expected_revision < 1 or not isinstance(reason, str) or not reason.strip():
        raise ValueError('请提供暂存revision与修改reason')
    if not isinstance(patches, list) or not patches:
        raise ValueError('patches须为非空局部修改数组')
    with ops.project.store.connect() as db:
        _cas(db, submission_id, expected_revision)
        row = _load(db, submission_id)
    for index, change in enumerate(patches):
        if not isinstance(change, dict) or change.get('op') not in {'set', 'remove', 'append'} or change.get('section') not in SECTIONS:
            raise ValueError(f'patches[{index}]操作或section无效')
        section = change['section']
        entries = row['result'].setdefault(section, [])
        if not isinstance(entries, list):
            raise ValueError('section不是数组，不能进行局部修改')
        if change['op'] == 'append':
            if set(change) != {'op', 'section', 'value'} or not isinstance(change['value'], dict):
                raise ValueError('append须提供单个对象value，只能尾部追加')
            entries.append(deepcopy(change['value']))
            continue
        expected_keys = {'op', 'section', 'index', 'field'} | ({'value'} if change['op'] == 'set' else set())
        if set(change) != expected_keys or type(change.get('index')) is not int or not 0 <= change['index'] < len(entries):
            raise ValueError('字段修改索引或参数无效；不允许删除行或重排索引')
        field = change.get('field')
        if not isinstance(field, str) or not field or '.' in field or '/' in field or not isinstance(entries[change['index']], dict):
            raise ValueError('field须为对象的单一顶层字段，不接受文件路径或整行替换')
        if change['op'] == 'set':
            entries[change['index']][field] = deepcopy(change['value'])
        else:
            if field not in entries[change['index']]:
                raise ValueError('要移除的字段不存在')
            del entries[change['index']][field]
    json.dumps(row['result'], ensure_ascii=False, allow_nan=False)
    row['changed_fields'] = [{key: change[key] for key in ('op', 'section', 'index', 'field') if key in change} for change in patches]
    row['submission_revision'] = expected_revision + 1
    return _evaluate(ops, row, expected_revision, reason)
