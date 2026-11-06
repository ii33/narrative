"""Operations-level content staging with transaction-bound completion receipts."""
from __future__ import annotations

import json
from contextvars import ContextVar
from copy import deepcopy

from pydantic import ValidationError

from .content_operations import (
    CONTENT_OPERATIONS,
    capture_basis,
    eligible,
    execution_arguments,
    operation_target,
    preflight,
    validate_protected_arguments,
)
from .graph_store import atomic_write_scope, post_commit_warnings

_DEPTH: ContextVar[str | None] = ContextVar('content_submission_depth', default=None)
PREFIX = 'content_submission:'


def nested(ops=None):
    return bool(_DEPTH.get()) if ops is None else _DEPTH.get() == str(ops._store.path)


def _errors():
    from .content_errors import ContentValidationError
    return (ContentValidationError, ValidationError)


def _load(db, identifier):
    row = db.execute('SELECT value FROM meta WHERE key=?', (PREFIX+identifier,)).fetchone()
    if row is None:
        raise ValueError('内容暂存不存在')
    return json.loads(row[0])


def _save(db, row):
    db.execute('INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (PREFIX+row['submission_id'], json.dumps(row, ensure_ascii=False)))
    summary = {key: row.get(key) for key in ('submission_id', 'submission_revision', 'operation', 'target', 'submission_status', 'changed_paths')}
    db.execute('INSERT INTO actions(id,revision,action,reason,summary) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET summary=excluded.summary',
        (row['submission_id']+'-revision-'+str(row['submission_revision']), row['formal_revision'], 'content_staging', row.get('reason', '提交业务内容'), json.dumps(summary, ensure_ascii=False)))


def summary(row):
    return {'valid': row['validation_status'] == 'valid', **({'diagnostics': deepcopy(row['problems'])} if row['status'] == 'staged' else {}), 'editable_roots': list(CONTENT_OPERATIONS[row['operation']].editable), **{key: deepcopy(row[key]) for key in ('status', 'submission_id', 'submission_revision', 'submission_status',
        'validation_status', 'operation', 'target', 'base_revisions', 'problems')}}


def _cas(db, identifier, revision):
    row = _load(db, identifier)
    if row['submission_status'] != 'staged':
        from .content_errors import ContentGuardError
        raise ContentGuardError('该内容暂存不能再次修改或提交')
    if row['submission_revision'] != revision:
        from .content_errors import ContentConflictError
        raise ContentConflictError('内容暂存revision已变化，请重读')


def _problem(exc):
    declared = getattr(exc, 'problems', None) or getattr(exc, 'diagnostics', None)
    if declared:
        return [{**deepcopy(d), 'path': d.get('path', [])} for d in declared]
    if isinstance(exc, ValidationError):
        return [{'code': 'CONTENT_VALIDATION', 'message': e['msg'], 'path': [], 'model_location': list(e['loc'])} for e in exc.errors(include_url=False, include_context=False, include_input=False)]
    return [{'code': 'CONTENT_VALIDATION', 'message': str(exc), 'path': []}]


def _new(ops, operation, arguments):
    json.dumps(arguments, ensure_ascii=False, allow_nan=False)
    with ops._store.connect(write=True) as db:
        current = db.execute("SELECT value FROM meta WHERE key='next_content_submission_number'").fetchone()
        number = int(current[0]) if current else 1
        db.execute("INSERT INTO meta(key,value) VALUES ('next_content_submission_number',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(number+1),))
        row = {'submission_id': f'content-submission-{number}', 'submission_revision': 1,
            'operation': operation, 'arguments': deepcopy(arguments), 'target': operation_target(operation, arguments),
            'base_revisions': {k: v for k, v in arguments.items() if 'revision' in k or 'fingerprint' in k or k == 'read_batch_id'},
            'status': 'staged', 'submission_status': 'staged', 'validation_status': 'pending',
            'problems': [{'code': 'CHECK_PENDING', 'message': '内容已暂存，检查未完成', 'path': []}],
            'formal_revision': ops.graph_project_info()['revision'], 'reason': '首次提交',
            'captured_basis': capture_basis(ops, operation, arguments)}
        _save(db, row)
    return row


def _attempt(ops, row, expected, call):
    if CONTENT_OPERATIONS[row['operation']].medium == 'material':
        from .material_submission import attempt
        return attempt(ops, row, expected, call)
    row['arguments'] = execution_arguments(ops, row['operation'], row['arguments'])
    token = _DEPTH.set(str(ops._store.path))
    try:
        try:
            with atomic_write_scope(ops._store) as db:
                _cas(db, row['submission_id'], expected)
                if row.get('captured_basis') != capture_basis(ops, row['operation'], row['arguments']):
                    from .content_errors import ContentConflictError
                    raise ContentConflictError('原内容依据已变化，请保留草稿并重新读取，不自动刷新依据')
                preflight(ops, row['operation'], row['arguments'])
                result = call(row['arguments'])
                raw_result = result
                if row['operation'] == 'create_source_slices' and isinstance(result, list):
                    result = {'status': 'accepted', 'result': result}
                if isinstance(result, dict) and (result.get('valid') is False or result.get('status') == 'rejected'):
                    errors = result.get('diagnostics') or []
                    if any(d.get('code') in {'REVISION', 'VERSION', 'CONFLICT'} for d in errors):
                        row.update(status='rejected', submission_status='rejected', validation_status='blocked',
                            problems=deepcopy(errors))
                        _save(db, row)
                        return result | summary(row)
                    from .content_errors import ContentValidationError
                    error = ContentValidationError('；'.join(str(d.get('message') or d) for d in errors) or '业务内容未通过静态校验')
                    error.__dict__['diagnostics'] = errors
                    raise error
                if isinstance(result, dict):
                    from .task_write_summary import content_commit_changes
                    changes = content_commit_changes(row['operation'], row['arguments'], raw_result)
                    if changes or result.get('status') == 'unchanged':
                        result = result | {'committed_changes': changes}
                row.update(status=(result.get('status') if isinstance(result, dict) else None) or 'accepted',
                    submission_status='committed', validation_status='valid', problems=[], business_result=deepcopy(result))
                if row['status'] not in {'accepted', 'unchanged'}:
                    row['status'] = 'accepted'
                _cas(db, row['submission_id'], expected)
                _save(db, row)
        except _errors() as exc:
            row.update(status='staged', submission_status='staged', validation_status='invalid', problems=_problem(exc))
            with ops._store.connect(write=True) as db:
                _cas(db, row['submission_id'], expected)
                _save(db, row)
            return summary(row)
        except Exception:
            # Hard conflicts/permissions/IO never become deferred executable drafts.
            with ops._store.connect(write=True) as db:
                current = _load(db, row['submission_id'])
                if current['submission_status'] == 'staged' and current['submission_revision'] == expected:
                    row.update(status='rejected', submission_status='rejected', validation_status='blocked', problems=[{'code': 'SUBMISSION_REJECTED', 'message': '操作被拒绝；不能延后自动执行', 'path': []}])
                    _save(db, row)
            raise
    finally:
        _DEPTH.reset(token)
    if post_commit_warnings() and isinstance(result, dict):
        result = result | {'diagnostics': [*(result.get('diagnostics') or []), *post_commit_warnings()]}
    return (result if isinstance(result, dict) else {'result': result}) | summary(row)


def invoke(ops, operation, arguments, call):
    row = _new(ops, operation, arguments)
    try:
        return _attempt(ops, row, 1, call)
    except Exception:
        if CONTENT_OPERATIONS[operation].medium == 'material':
            with ops._store.connect(write=True) as db:
                current = _load(db, row['submission_id'])
                if current['submission_status'] == 'staged' and current['submission_revision'] == 1:
                    current.update(status='rejected', submission_status='rejected', validation_status='blocked',
                        problems=[{'code': 'SUBMISSION_REJECTED', 'message': '资料操作目标或存储被拒绝', 'path': []}])
                    _save(db, current)
        raise


def read(ops, submission_id, paths=None):
    if submission_id.startswith('extraction-submission-'):
        from .extraction_staging import read as extraction_read
        result = extraction_read(ops, submission_id)
        result.update(operation='submit_extraction', target={'source_id': result['source_id']}, base_revisions={'revision': result['formal_revision']})
        if paths is not None:
            with ops._store.connect() as db:
                raw = json.loads(db.execute('SELECT value FROM meta WHERE key=?', ('extraction_submission:'+submission_id,)).fetchone()[0])
            arguments = {'source_id': raw['source_id'], 'start': raw['start'], 'end': raw['end'], 'result': raw['result']}
            result['values'] = [{'path': path, 'value': deepcopy(_at(arguments, path))} for path in _paths(paths)]
        return result
    with ops._store.connect() as db:
        row = _load(db, submission_id)
    from .material_submission import authoritative
    row = authoritative(ops, row)
    result = summary(row)
    if paths is not None:
        result['values'] = [{'path': path, 'value': deepcopy(_at(row['arguments'], path))} for path in _paths(paths)]
    return result


def _paths(paths):
    if not isinstance(paths, list) or any(not isinstance(path, list) or any(not isinstance(t, str) and type(t) is not int for t in path) for path in paths):
        raise ValueError('paths须为字符串/整数token路径数组')
    return paths


def _at(value, path):
    for token in path:
        if isinstance(value, list):
            if type(token) is not int or not 0 <= token < len(value):
                raise ValueError('内容索引无效')
        elif not isinstance(value, dict) or token not in value:
            raise ValueError('内容字段路径不存在')
        value = value[token]
    return value


def _apply_patch(row, patches):
    if not isinstance(patches, list) or not patches:
        raise ValueError('patches须为非空修改数组')
    spec = CONTENT_OPERATIONS[row['operation']]
    arguments = deepcopy(row['arguments'])
    for change in patches:
        if not isinstance(change, dict) or change.get('op') not in {'set', 'remove', 'append'}:
            raise ValueError('局部修改op无效')
        path = _paths([change.get('path')])[0]
        if not path or path[0] not in spec.editable:
            from .content_errors import ContentGuardError
            raise ContentGuardError('不能修改操作、目标、权限或并发依据字段')
        expected = {'op', 'path'} | ({'value'} if change['op'] != 'remove' else set())
        if set(change) != expected:
            raise ValueError('局部修改字段无效')
        if change['op'] == 'append':
            target = _at(arguments, path)
            if not isinstance(target, list):
                raise ValueError('append仅能追加数组尾部')
            target.append(deepcopy(change['value']))
        else:
            parent = _at(arguments, path[:-1])
            leaf = path[-1]
            if isinstance(parent, list):
                if change['op'] != 'set' or type(leaf) is not int or not 0 <= leaf < len(parent) or isinstance(parent[leaf], dict):
                    raise ValueError('不能删除或替换整行，避免临时引用索引重排')
                parent[leaf] = deepcopy(change['value'])
                continue
            if not isinstance(parent, dict):
                raise ValueError('字段修改须指向对象')
            if change['op'] == 'remove':
                if leaf not in parent:
                    raise ValueError('移除的字段不存在')
                del parent[leaf]
            else:
                if isinstance(parent.get(leaf), list) and leaf in {'nodes', 'properties', 'graph_objects', 'property_records', 'changes', 'actions', 'blocks', 'units', 'images', 'reference_images', 'slices'}:
                    raise ValueError('请修改数组项的具体字段或append，不能整批替换稳定索引数组')
                parent[leaf] = deepcopy(change['value'])
    validate_protected_arguments(row['arguments'], arguments, spec)
    if not eligible(row['operation'], arguments):
        from .content_errors import ContentGuardError
        raise ContentGuardError('修补不能将内容转为删除、移动或外部文件命令')
    return arguments


def preview_patch(ops, submission_id, expected_revision, patches):
    if type(expected_revision) is not int:
        raise ValueError('请提供暂存revision')
    if submission_id.startswith('extraction-submission-'):
        with ops._store.connect() as db:
            raw = db.execute('SELECT value FROM meta WHERE key=?', ('extraction_submission:'+submission_id,)).fetchone()
            if raw is None:
                raise ValueError('提取暂存不存在')
            row = json.loads(raw[0])
        if row['submission_status'] != 'staged' or row['submission_revision'] != expected_revision:
            from .content_errors import ContentConflictError
            raise ContentConflictError('提取暂存已变化或已完成')
        arguments = {k: row[k] for k in ('source_id', 'start', 'end', 'result')}
        candidate = _apply_patch({'operation': 'submit_extraction', 'arguments': arguments}, patches)
        return {'operation': 'submit_extraction', 'arguments': candidate, 'target': {'source_id': row['source_id']}}
    with ops._store.connect() as db:
        row = _load(db, submission_id)
    from .material_submission import authoritative
    row = authoritative(ops, row)
    if row['submission_status'] != 'staged' or row['submission_revision'] != expected_revision:
        from .content_errors import ContentConflictError
        raise ContentConflictError('内容暂存已变化或已完成')
    return {'operation': row['operation'], 'arguments': execution_arguments(ops, row['operation'], _apply_patch(row, patches)), 'target': row['target']}


def patch(ops, submission_id, expected_revision, patches, reason):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('请提供修改原因')
    if submission_id.startswith('extraction-submission-'):
        preview_patch(ops, submission_id, expected_revision, patches)
        from .extraction_staging import patch as extraction_patch
        converted = []
        for change in patches:
            path = change.get('path') or []
            if path and path[0] == 'result':
                path = path[1:]
            if change.get('op') == 'append' and len(path) == 1:
                converted.append({'op': 'append', 'section': path[0], 'value': change['value']})
            elif len(path) == 3:
                converted.append({'op': change['op'], 'section': path[0], 'index': path[1], 'field': path[2], **({'value': change['value']} if 'value' in change else {})})
            else:
                raise ValueError('提取路径须为result/section/index/field；仅支持字段修改和section尾追加')
        return extraction_patch(ops, submission_id, expected_revision, converted, reason)
    resolved = preview_patch(ops, submission_id, expected_revision, patches)
    with ops._store.connect() as db:
        row = _load(db, submission_id)
    from .material_submission import authoritative
    row = authoritative(ops, row)
    row.update(arguments=resolved['arguments'], submission_revision=expected_revision+1, reason=reason, changed_paths=[p['path'] for p in patches])
    return _attempt(ops, row, expected_revision, lambda candidate: getattr(ops, row['operation'])(**candidate))


def list_pending(ops, operation=None, target=None, limit=20, after=0):
    if type(limit) is not int or not 1 <= limit <= 100 or type(after) is not int or after < 0:
        raise ValueError('limit须1..100，after须非负整数')
    with ops._store.connect() as db:
        rows = db.execute("SELECT key,json_extract(value,'$.submission_id'),json_extract(value,'$.operation'),"
            "json_extract(value,'$.target'),json_extract(value,'$.source_id') FROM meta "
            "WHERE (key GLOB 'content_submission:*' OR key GLOB 'extraction_submission:*') "
            "AND json_extract(value,'$.submission_status')='staged'").fetchall()
    picked = []
    for key, identifier, name, target_json, source_id in rows:
        extraction = key.startswith('extraction_submission:')
        name = 'submit_extraction' if extraction else name
        targets = {'source_id': source_id} if extraction else json.loads(target_json)
        if operation is not None and operation != name:
            continue
        if target is not None and (not all(targets.get(k) == v or (k == 'node_id' and v in targets.get('node_ids', [])) for k, v in target.items()) if isinstance(target, dict) else target not in targets.values() and target not in targets.get('node_ids', [])):
            continue
        cursor = int(identifier.rsplit('-', 1)[1])*2 + (0 if extraction else 1)
        if cursor > after:
            picked.append((cursor, identifier))
    picked.sort()
    selected = [read(ops, identifier) for _, identifier in picked[:limit]]
    return {'submissions': [row for row in selected if row['submission_status'] == 'staged'],
            'next_after': picked[limit-1][0] if len(picked) > limit else None}
