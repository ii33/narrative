"""Material JSON is authoritative for both card changes and their submission receipt."""
import hashlib
import json
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

_CONTEXT: ContextVar[Any] = ContextVar('material_submission', default=None)


def _basis(store, row):
    card_id = row['target'].get('card_id')
    if card_id is None:
        return None
    card = next((c for c in store.load(row['target']['node_id'])['cards'] if c['id'] == card_id), None)
    return hashlib.sha256(json.dumps(card, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_base(store, node_id):
    context = _CONTEXT.get()
    if context is None:
        return
    row, _expected = context
    if node_id != row['target']['node_id'] or row.get('material_base') != _basis(store, row):
        from .content_errors import ContentConflictError
        raise ContentConflictError('资料卡已变化，请保留草稿并重新读取，不能自动覆盖')


def commit_receipt(node_id, data, business_result):
    context = _CONTEXT.get()
    if context is None or business_result is None:
        return
    row, expected = context
    if row['target']['node_id'] != node_id:
        raise ValueError('资料卡目标不一致')
    prior = data.get('content_submissions', {}).get(row['submission_id'])
    if prior is None or prior['submission_revision'] != expected or prior['submission_status'] != 'staged':
        raise ValueError('资料内容暂存已变化，不能覆盖')
    from .task_write_summary import content_commit_changes
    receipt = deepcopy(business_result)
    changes = content_commit_changes(row['operation'], row['arguments'], receipt)
    if changes:
        receipt['committed_changes'] = changes
    row.update(status='accepted', submission_status='committed', validation_status='valid', problems=[], business_result=receipt)
    data.setdefault('content_submissions', {})[row['submission_id']] = deepcopy(row)


def authoritative(ops, row):
    if row.get('operation', '').startswith('material_'):
        from .node_material import MaterialStore
        actual = MaterialStore(ops._store.path).load(row['target']['node_id']).get('content_submissions', {}).get(row['submission_id'], row)
        if actual.get('submission_status') == 'committed' or row['submission_status'] == 'staged':
            return actual
    return row


def attempt(ops, row, expected, call):
    from .business_staging import _DEPTH, _errors, _problem, _save, summary
    store, _snapshot = ops._node_material(row['target']['node_id'])
    if 'material_base' not in row:
        row['material_base'] = _basis(store, row)
    existing = store.load(row['target']['node_id']).get('content_submissions', {}).get(row['submission_id'])
    if existing is None:
        store.stage_content_submission(row['target']['node_id'], row)
    elif existing['submission_revision'] != expected or existing['submission_status'] != 'staged':
        raise ValueError('资料内容暂存已变化或已完成')
    nesting = _DEPTH.set(str(ops._store.path))
    token = _CONTEXT.set((row, expected))
    try:
        try:
            result = call(row['arguments'])
        except _errors() as exc:
            row.update(status='staged', submission_status='staged', validation_status='invalid', problems=_problem(exc))
            _CONTEXT.reset(token)
            token = _CONTEXT.set(None)
            store.stage_content_submission(row['target']['node_id'], row, expected)
            result = None
        except Exception:
            actual = authoritative(ops, row)
            if actual.get('submission_status') != 'committed':
                row.update(status='rejected', submission_status='rejected', validation_status='blocked',
                    problems=[{'code': 'SUBMISSION_REJECTED', 'message': '操作被拒绝，不会延后自动执行', 'path': []}])
                _CONTEXT.reset(token)
                token = _CONTEXT.set(None)
                store.stage_content_submission(row['target']['node_id'], row, expected)
                with ops._store.connect(write=True) as db:
                    _save(db, row)
                raise
            row = actual
            result = row['business_result']
        if result is not None:
            row = authoritative(ops, row)
            if row['submission_status'] != 'committed':
                raise RuntimeError('资料写入没有原子提交收据')
            result = row['business_result']
        try:
            with ops._store.connect(write=True) as db:
                _save(db, row)
        except Exception as exc:
            # The file receipt remains authoritative and prevents duplicate replay.
            if isinstance(result, dict):
                result = result | {'feedback_error': {'type': type(exc).__name__, 'message': str(exc),
                    'detail': '资料文件及提交收据已保存，数据库索引反馈失败；不得重复执行'}}
            else:
                row['feedback_error'] = {'type': type(exc).__name__, 'message': str(exc)}
        return (result if isinstance(result, dict) else {}) | summary(row)
    finally:
        _CONTEXT.reset(token)
        _DEPTH.reset(nesting)
