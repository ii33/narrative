"""Explicit event-local property editing via the shared atomic Operations boundary."""
from __future__ import annotations

from .content_errors import ContentConflictError, ContentGuardError, ContentValidationError

MISSING = object()
OPS = {'set', 'add', 'remove', 'clear', 'unknown'}


def record_change(ops, node_id, property_id, event_id, op, *, expected_revision, reason, value=MISSING,
                  value_type=None, cardinality=None, symmetric=None, scope='world', holder_id=None,
                  context_id='canon', quote=None, source_id=None, category=None, source=None, related_nodes=None):
    snapshot = ops.graph_snapshot()
    if type(expected_revision) is not int or expected_revision != snapshot['revision']:
        return {'status': 'rejected', 'valid': False, 'revision': snapshot['revision'],
                'diagnostics': [{'severity': 'error', 'code': 'REVISION', 'message': '正式资料版本已变化，请刷新'}]}
    if not isinstance(op, str) or op not in OPS:
        raise ContentValidationError('属性变更op须为set/add/remove/clear/unknown')
    if (value is not MISSING) != (op in {'set', 'add', 'remove'}):
        raise ContentValidationError('set/add/remove必须带value，clear/unknown不能带value')
    if type(expected_revision) is not int or not isinstance(reason, str) or not reason.strip():
        raise ContentConflictError('请提供当前revision及变更依据')
    if value_type is not None and (not isinstance(value_type, str) or value_type not in {'string', 'number', 'boolean', 'node', 'either', 'any'}):
        raise ContentValidationError('value_type必须是合法类型')
    if cardinality is not None and (not isinstance(cardinality, str) or cardinality not in {'one', 'many'}):
        raise ContentValidationError('cardinality必须为one/many')
    if symmetric is not None and type(symmetric) is not bool:
        raise ContentValidationError('symmetric必须为布尔值')
    snapshot = ops.graph_snapshot()
    nodes = {row['id']: row for row in snapshot['nodes']}
    if node_id not in nodes or nodes.get(event_id, {}).get('node_type') not in {'event', 'action'}:
        raise ContentGuardError('主体或变更事件不存在')
    records = ops.property_records()
    definition = next((row for row in records if row.get('kind') == 'property_definition' and row['id'] == property_id), None)
    batch = []
    if definition is None:
        if value_type is None:
            raise ContentValidationError('新属性定义必须明确value_type')
        batch.append({'kind': 'property_definition', 'id': property_id, 'value_type': value_type,
                      'cardinality': cardinality or 'one', 'symmetric': symmetric if symmetric is not None else False})
    else:
        for field, selected in [('value_type', value_type), ('cardinality', cardinality), ('symmetric', symmetric)]:
            if selected is not None and selected != definition.get(field, False if field == 'symmetric' else 'one' if field == 'cardinality' else None):
                raise ContentValidationError(f'{field}与已有定义不一致，须显式更正定义')
    record = {'kind': 'property_change', 'node_id': node_id, 'property_id': property_id, 'event_id': event_id,
              'op': op, 'sequence': max((row['sequence'] for row in records if row.get('kind') == 'property_change' and row['event_id'] == event_id), default=-1)+1,
              'scope': scope, 'context_id': context_id}
    if value is not MISSING:
        record['value'] = value
    for field, item in [('holder_id', holder_id), ('category', category), ('source', source), ('related_nodes', related_nodes)]:
        if item is not None:
            record[field] = item
    if quote is not None:
        if not isinstance(quote, str) or not quote:
            raise ContentValidationError('引文须为准确非空文本')
        source_id = source_id or (nodes[event_id].get('disclosure') or {}).get('source_id')
        hits = ops.locate(source_id, [quote], limit_per_quote=2)[0]['matches']
        if len(hits) != 1:
            raise ContentValidationError('变更引文须逐字唯一匹配原文')
        record['evidence'] = {'source_id': source_id, 'quote': quote}
        record['disclosure'] = {'source_id': source_id, 'start': hits[0]['start']}
    batch.append(record)
    return ops.apply_changes([], batch, expected_revision, reason)
