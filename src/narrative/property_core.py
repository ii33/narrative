"""Schema 11 event-bound property facts and deterministic state reduction."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any

from .source_location import ExactQuoteLookup

KINDS = {'property_definition', 'property_change'}
OPS = {'set', 'add', 'remove', 'clear', 'unknown'}
VALUE_TYPES = {'string', 'number', 'boolean', 'node', 'either', 'any'}
CHANGE_FIELDS = {'id', 'kind', 'node_id', 'property_id', 'event_id', 'op', 'value', 'sequence',
                 'scope', 'holder_id', 'context_id', 'disclosure', 'evidence', 'evidence_spans',
                 'source', 'category', 'related_nodes', 'anchor_basis'}
DEFINITION_FIELDS = {'id', 'kind', 'value_type', 'cardinality', 'target_node_types', 'semantic', 'symmetric', 'evidence'}


def _issue(code, message, object_id=None, severity='error'):
    return {'code': code, 'message': message, 'object_id': object_id, 'severity': severity}


def canonical_definitions(records):
    return {r['id']: r for r in records if isinstance(r, dict) and r.get('kind') == 'property_definition' and isinstance(r.get('id'), str)}


def _freeze(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _valid_value(value: Any, definition: dict, graph_nodes: dict) -> bool:
    kind = definition.get('value_type')
    if kind == 'any':
        return True
    if kind == 'string':
        return isinstance(value, str)
    if kind == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if kind == 'boolean':
        return type(value) is bool
    if kind == 'either':
        return isinstance(value, str)
    if kind == 'node':
        target = graph_nodes.get(value) if isinstance(value, str) else None
        return bool(target and (not definition.get('target_node_types') or target.get('node_type') in definition['target_node_types']))
    return False


def relation_target(record, nodes=None, definitions=None):
    nodes, definitions = nodes or {}, definitions or {}
    value = record.get('value')
    property_id = record.get('property_id')
    definition = definitions.get(property_id, {}) if isinstance(property_id, str) else {}
    if definition.get('cardinality', 'one') != 'one' or not isinstance(definition.get('value_type'), str) or definition['value_type'] not in {'node', 'either'}:
        return None
    if isinstance(value, str) and nodes.get(record.get('node_id') if isinstance(record.get('node_id'), str) else None, {}).get('node_type') == 'person' and nodes.get(value, {}).get('node_type') == 'person':
        return value
    return None


def change_group(row):
    return (row.get('node_id'), row.get('property_id'), row.get('context_id', 'canon'), row.get('scope', 'world'), row.get('holder_id'))


def _rank(value):
    return value if isinstance(value, tuple) else tuple(value) if isinstance(value, list) else (value,)


def change_key(row, event_order):
    rank = event_order.get(row.get('event_id'))
    return None if rank is None else (_rank(rank), row.get('sequence', -1))


def reduce_changes(changes, definition, nodes, *, event_order, cut=None):
    """Fold one scoped property group; caller supplies its authoritative event axis."""
    rows = [r for r in changes if r.get('kind') == 'property_change']
    diagnostics = []
    groups = {change_group(r) for r in rows}
    if len(groups) > 1:
        diagnostics.append(_issue('STATE_GROUP', '状态推导只能处理一个主体、属性、语境及立场分组'))
    selected = []
    limit = None
    if cut is not None:
        event = cut.get('event_id') if isinstance(cut, dict) else cut
        rank = event_order.get(event)
        if rank is None:
            raise ValueError('状态切点事件不存在或没有正式顺序')
        sequence = cut.get('sequence', math.inf) if isinstance(cut, dict) else math.inf
        if sequence != math.inf and (type(sequence) is not int or sequence < 0):
            raise ValueError('切点sequence必须为非负整数')
        limit = (_rank(rank), sequence)
    for row in rows:
        key = change_key(row, event_order)
        if key is None:
            diagnostics.append(_issue('EVENT_ORDER', '属性变更事件缺少正式顺序', row.get('id')))
        elif type(row.get('sequence')) is not int or row['sequence'] < 0:
            diagnostics.append(_issue('SEQUENCE', 'sequence必须为非负整数', row.get('id')))
        elif limit is None or key <= limit:
            selected.append(row)
    selected.sort(key=lambda r: change_key(r, event_order) or ((-1,), -1))
    relation = definition.get('cardinality', 'one') == 'one' and any(relation_target(r, nodes, {r.get('property_id'): definition}) is not None for r in rows)
    many = definition.get('cardinality') == 'many'
    state = {'kind': 'unrecorded'}
    active = []
    partners = {}
    known_members = {}
    baseline = False
    batches = defaultdict(list)
    for row in selected:
        batches[change_key(row, event_order)].append(row)
    for batch in batches.values():
        signatures = {(r.get('op'), _freeze(r.get('value'))) for r in batch}
        relation_commutes = False
        if relation and all(r.get('op') in {'set', 'add', 'remove'} and relation_target(r, nodes, {r.get('property_id'): definition}) is not None for r in batch):
            directions = defaultdict(set)
            for r in batch:
                directions[r['value']].add('remove' if r['op'] == 'remove' else 'establish')
            relation_commutes = all(len(ops) == 1 for ops in directions.values())
        collection_commutes = False
        if many and all(r.get('op') in {'add', 'remove'} and isinstance(r.get('value'), list) for r in batch):
            additions = {_freeze(v) for r in batch if r['op'] == 'add' for v in r['value']}
            removals = {_freeze(v) for r in batch if r['op'] == 'remove' for v in r['value']}
            collection_commutes = not additions.intersection(removals)
        if len(signatures) > 1 and not relation_commutes and not collection_commutes:
            diagnostics.append(_issue('STATE_CONFLICT', '同一事件同sequence存在不相容变更，不能按ID或提交顺序猜测', batch[0].get('id')))
            state, active, partners, baseline = {'kind': 'conflict'}, [r['id'] for r in batch], {}, False
            continue
        for row in batch:
            op, value, identifier = row.get('op'), row.get('value'), row.get('id')
            if op in {'clear', 'unknown'}:
                state = {'kind': 'absent' if op == 'clear' else 'unknown'}
                active, partners, known_members, baseline = [identifier], {}, {}, False
            elif relation:
                if relation_target(row, nodes, {row.get('property_id'): definition}) is None:
                    diagnostics.append(_issue('VALUE', '人物关系须引用单一有效对方', identifier))
                    continue
                if op in {'set', 'add'}:
                    partners[value] = identifier
                elif op == 'remove':
                    partners.pop(value, None)
                state = {'kind': 'known', 'value': list(partners)} if partners else {'kind': 'absent'}
                active = list(partners.values()) or [identifier]
            elif many:
                values = value if isinstance(value, list) else []
                if op == 'set':
                    known_members = {_freeze(v): v for v in values}
                    baseline = True
                    active = [identifier]
                elif op == 'add':
                    known_members.update({_freeze(v): v for v in values})
                    active.append(identifier)
                elif op == 'remove':
                    for v in values:
                        known_members.pop(_freeze(v), None)
                    active.append(identifier)
                state = {'kind': 'known', 'value': list(known_members.values())} if baseline else {'kind': 'unknown'}
            elif op == 'set':
                state, active = {'kind': 'known', 'value': value}, [identifier]
            else:
                diagnostics.append(_issue('OP', '单值非人物关系只允许set、clear或unknown', identifier))
    if diagnostics and any(d['code'] in {'STATE_GROUP', 'EVENT_ORDER', 'SEQUENCE'} for d in diagnostics):
        state = {'kind': 'conflict'}
    return {'state': state, 'active_change_ids': list(dict.fromkeys(active)), 'record_ids': list(dict.fromkeys(active)),
            'history_changes': selected, 'diagnostics': diagnostics}


def definition_reference_issues(records, nodes):
    definitions = canonical_definitions(records)
    return [_issue('PROPERTY', '属性定义不存在', r.get('id')) for r in records
            if r.get('kind') == 'property_change' and r.get('property_id') not in definitions]


def _evidence_error(record, sources, lookups=None):
    evidence = record.get('evidence')
    if evidence is None:
        return None
    if not isinstance(evidence, dict) or set(evidence) != {'source_id', 'quote'}:
        return 'evidence必须包含source_id和逐字quote'
    source = sources.get(evidence.get('source_id')) if isinstance(evidence.get('source_id'), str) else None
    quote = evidence.get('quote')
    if source is None or not isinstance(quote, str) or not quote:
        return '证据来源或引文无效'
    if lookups is None:
        lookup = ExactQuoteLookup(source['text'])
    else:
        if evidence['source_id'] not in lookups:
            lookups[evidence['source_id']] = ExactQuoteLookup(source['text'])
        lookup = lookups[evidence['source_id']]
    if len(lookup.positions(quote, 2)) != 1:
        return '证据引文必须逐字唯一匹配原文'
    return None


def validate_records(records, snapshot, existing_records=None, *, sources=None):
    issues = []
    nodes = {n['id']: n for n in snapshot.get('nodes', [])}
    if not nodes:
        nodes = {n['id']: n for n in snapshot.get('objects', []) if n.get('kind') == 'node'}
    all_records = list(existing_records or []) + list(records)
    definitions = canonical_definitions(all_records)
    sources = sources if sources is not None else {s['id']: s for s in snapshot.get('sources', [])}
    ids = {r['id'] for r in existing_records or []}
    contexts = {c['id'] for c in snapshot.get('contexts', [])} or {'canon'}
    lookups = {}
    for row in records:
        if not isinstance(row, dict):
            issues.append(_issue('FORMAT', '属性记录必须为对象'))
            continue
        rid, kind = row.get('id'), row.get('kind')
        if not isinstance(kind, str) or kind not in KINDS:
            issues.append(_issue('KIND', '只支持property_definition与property_change', rid))
            continue
        if not isinstance(rid, str) or not rid or rid in ids:
            issues.append(_issue('ID', '属性记录ID必须非空且唯一', rid))
        if isinstance(rid, str):
            ids.add(rid)
        allowed = DEFINITION_FIELDS if kind == 'property_definition' else CHANGE_FIELDS
        if set(row) - allowed:
            issues.append(_issue('FORMAT', '属性记录含不允许字段：'+','.join(sorted(set(row)-allowed)), rid))
        error = _evidence_error(row, sources, lookups)
        if error:
            issues.append(_issue('EVIDENCE', error, rid))
        if kind == 'property_definition':
            if not isinstance(row.get('value_type'), str) or row['value_type'] not in VALUE_TYPES or not isinstance(row.get('cardinality'), str) or row['cardinality'] not in {'one', 'many'}:
                issues.append(_issue('DEFINITION', '定义须明确value_type与cardinality(one/many)', rid))
            if 'symmetric' in row and type(row['symmetric']) is not bool:
                issues.append(_issue('DEFINITION', 'symmetric必须为布尔值', rid))
            if row.get('target_node_types') and (not isinstance(row['target_node_types'], list) or row.get('value_type') != 'node'):
                issues.append(_issue('DEFINITION', 'target_node_types只适用于node定义', rid))
            continue
        definition = definitions.get(row.get('property_id')) if isinstance(row.get('property_id'), str) else None
        if not isinstance(row.get('node_id'), str) or row['node_id'] not in nodes or definition is None:
            issues.append(_issue('TARGET', '属性主体或定义不存在', rid))
        event = nodes.get(row.get('event_id')) if isinstance(row.get('event_id'), str) else None
        if not event or event.get('node_type') not in {'event', 'action'}:
            issues.append(_issue('EVENT_REF', '变更必须锚定有效事件或动作', rid))
        if type(row.get('sequence')) is not int or row['sequence'] < 0:
            issues.append(_issue('SEQUENCE', 'sequence须为非负整数', rid))
        if not isinstance(row.get('context_id', 'canon'), str) or row.get('context_id', 'canon') not in contexts:
            issues.append(_issue('CONTEXT', '语境不存在', rid))
        scope, holder = row.get('scope', 'world'), row.get('holder_id')
        if not isinstance(scope, str) or scope not in {'world', 'claim', 'cognition'} or scope == 'world' and holder is not None or scope != 'world' and (not isinstance(holder, str) or nodes.get(holder, {}).get('node_type') != 'person'):
            issues.append(_issue('SCOPE', 'world不带holder，claim/cognition须指定人物holder', rid))
        disclosure = row.get('disclosure')
        if disclosure is not None:
            source = sources.get(disclosure.get('source_id')) if isinstance(disclosure, dict) and isinstance(disclosure.get('source_id'), str) else None
            if not source or type(disclosure.get('start')) is not int or not 0 <= disclosure['start'] <= len(source['text']):
                issues.append(_issue('DISCLOSURE', '披露位置须为合法原文Unicode坐标', rid))
            elif row.get('evidence') and (row['evidence']['source_id'] != disclosure['source_id'] or source['text'].find(row['evidence']['quote']) != disclosure['start']):
                issues.append(_issue('DISCLOSURE', '披露位置须匹配逐字证据起点', rid))
        spans = row.get('evidence_spans', [])
        if not isinstance(spans, list):
            issues.append(_issue('EVIDENCE', 'evidence_spans必须为数组', rid))
        else:
            for span in spans:
                source = sources.get(span.get('source_id')) if isinstance(span, dict) and isinstance(span.get('source_id'), str) else None
                if not source or type(span.get('start')) is not int or type(span.get('end')) is not int or not 0 <= span['start'] < span['end'] <= len(source['text']) or source['text'][span['start']:span['end']] != span.get('quote'):
                    issues.append(_issue('EVIDENCE', '证据范围须逐字对应合法原文Unicode位置', rid))
        if row.get('related_nodes') is not None and (not isinstance(row['related_nodes'], list) or any(not isinstance(v, str) or v not in nodes for v in row['related_nodes'])):
            issues.append(_issue('NODE_REF', 'related_nodes须引用有效节点', rid))
        op = row.get('op')
        if not isinstance(op, str) or op not in OPS:
            issues.append(_issue('OP', '无效属性变更操作', rid))
        elif op in {'clear', 'unknown'}:
            if 'value' in row:
                issues.append(_issue('VALUE', 'clear和unknown不得携带value', rid))
        elif definition is not None:
            value = row.get('value')
            many = definition.get('cardinality') == 'many'
            values = value if many and isinstance(value, list) else [value]
            if many and not isinstance(value, list) or not all(_valid_value(v, definition, nodes) for v in values) or len({_freeze(v) for v in values}) != len(values):
                issues.append(_issue('VALUE', '值类型、集合唯一性或节点引用无效', rid))
            if op in {'add', 'remove'} and not many and relation_target(row, nodes, definitions) is None:
                issues.append(_issue('OP', '单值add/remove仅适用于明确人物对方关系', rid))
    return issues


def history(accepted_records, node_id, property_id, scope='world', holder_id=None):
    return [r for r in accepted_records if r.get('kind') == 'property_definition' and r.get('id') == property_id
            or r.get('kind') == 'property_change' and r.get('node_id') == node_id and r.get('property_id') == property_id
            and r.get('scope', 'world') == scope and r.get('holder_id') == holder_id]
