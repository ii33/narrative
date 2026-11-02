"""Current-state transactions and disclosure intervals for the current schema.

No before/after payload, patch chain or retraction ledger is persisted.
"""
from __future__ import annotations

import json
from copy import deepcopy


def point(row: dict) -> dict | None:
    boundary = row.get('valid_from') or row.get('disclosure')
    if not isinstance(boundary, dict):
        return None
    result = {key: boundary[key] for key in ('source_id', 'start', 'event_id') if key in boundary}
    if row.get('event_id') and not result.get('event_id'):
        result['event_id'] = row['event_id']
    return result


def source_order(snapshot: dict) -> dict[str, int]:
    result = {row['source_id']: row['ordinal'] for row in snapshot.get('source_orders', [])
              if row.get('context_id', 'canon') == 'canon'}
    for source in snapshot.get('sources', []):
        if source['id'] not in result:
            result[source['id']] = max(result.values(), default=-1) + 1
    return result


def position(boundary: dict | None, snapshot: dict) -> tuple | None:
    if not isinstance(boundary, dict):
        return None
    source_id = boundary.get('source_id')
    ordinal = source_order(snapshot).get(source_id) if isinstance(source_id, str) else None
    start = boundary.get('start')
    return (ordinal, start) if ordinal is not None and type(start) is int else None


def normalize_values(records: list[dict], snapshot: dict) -> list[dict]:
    nodes = {row['id']: row for row in snapshot['nodes']}
    if not isinstance(records, list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in records):
        raise ValueError('属性记录必须是含稳定id的对象数组')
    try:
        json.dumps(records, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('属性记录必须为合法JSON值') from exc
    result = deepcopy(records)
    for row in result:
        if row.get('kind') not in {'property_value', 'property_definition'}:
            raise ValueError('v8只存属性定义与有效区间取值，不接受撤回/别名/分类记录')
        if row.get('kind') == 'property_definition':
            row.setdefault('symmetric', False)
        if row.get('kind') == 'property_definition' and row.get('id') == '持有者':
            row['semantic'] = 'item_holder'
        if row.get('kind') != 'property_value':
            continue
        if 'valid_from' in row:
            if not isinstance(row['valid_from'], dict):
                raise ValueError('valid_from必须是原文坐标对象')
            boundary = deepcopy(row['valid_from'])
            if row.get('event_id'):
                boundary.setdefault('event_id', row['event_id'])
        else:
            boundary = point(row)
            if not boundary:
                anchor = nodes.get(row.get('event_id'), {})
                boundary = point(anchor)
        if not boundary:
            raise ValueError('property_value必须给出valid_from或有精确disclosure的event锚点')
        row['valid_from'] = boundary
        row.setdefault('valid_to', None)
        row.setdefault('scope', 'world')
        row.setdefault('disclosure', {key: boundary[key] for key in ('source_id', 'start')})
    return result


def prepare(snapshot: dict, existing: list[dict], objects: list[dict], records: list[dict]) -> tuple[dict, list[dict]]:
    from .property_core import canonical_definitions
    from .property_store import relation_target

    for rows, label in ((objects, '图对象'), (records, '属性记录')):
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in rows):
            raise ValueError(label + '必须是含稳定id的对象数组')
        identifiers = [row['id'] for row in rows]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(label + '同一批次不能重复同一个id')
    graph = {row['id']: row for row in snapshot['objects']}
    graph.update({row['id']: deepcopy(row) for row in objects})
    prospective = snapshot | {'objects': list(graph.values())}
    for kind, key in (('node', 'nodes'), ('context', 'contexts'), ('timeline', 'timelines'), ('source_order', 'source_orders')):
        prospective[key] = [row for row in graph.values() if row['kind'] == kind]
    values = {row['id']: row for row in existing}
    normalized = normalize_values(records, prospective)
    definitions = canonical_definitions(list(values.values()) + normalized)
    nodes = {row['id']: row for row in prospective['nodes']}
    affected = set()
    for row in normalized:
        if row.get('kind') == 'property_value':
            affected.add((row.get('node_id'), row.get('property_id'), row.get('scope') or 'world', row.get('holder_id')))
        values[row['id']] = row
    groups = {}
    for row in values.values():
        if row.get('kind') != 'property_value':
            continue
        group = (row.get('node_id'), row.get('property_id'), row.get('scope') or 'world', row.get('holder_id'))
        if group not in affected:
            continue
        definition = definitions.get(str(row.get('property_id') or ''), {})
        if definition.get('cardinality') == 'many':
            continue
        relation = relation_target(row, nodes, definitions)
        key = group + ((row.get('value') if relation is not None else None),)
        groups.setdefault(key, []).append(row)
    orders = source_order(prospective)
    def at(boundary):
        if not isinstance(boundary, dict):
            return None
        source_id = boundary.get('source_id')
        ordinal = orders.get(source_id) if isinstance(source_id, str) else None
        return (ordinal, boundary['start']) if ordinal is not None and type(boundary.get('start')) is int else None
    for rows in groups.values():
        ordered = sorted(rows, key=lambda row: at(point(row)) or (-1, -1))
        for old, following in zip(ordered, ordered[1:], strict=False):
            older, newer = at(point(old)), at(point(following))
            if older == newer and old.get('value') != following.get('value'):
                raise ValueError('同一属性在同一时间点出现不同值；录入错误请直接更正，不创建并列状态')
            end = at(old.get('valid_to'))
            if older is not None and newer is not None and older < newer and (end is None or end > newer):
                values[old['id']] = old | {'valid_to': deepcopy(following['valid_from'])}

    return prospective, list(values.values())


def diagnostics(store, snapshot: dict, records: list[dict], *, sources: dict | None = None) -> list[dict]:
    from .graph_service import GraphService
    from .property_core import validate_records
    if sources is None:
        sources = {row['id']: row for row in store.sources(include_text=True)}
    graph = snapshot | {'sources': [row | {'text': sources[row['id']]['text']} for row in snapshot['sources']]}
    active = [row for row in graph['objects'] if not row.get('retracted')]
    issues = GraphService(store).precheck(active, snapshot['revision'],
                                        _snapshot=graph, _accepted_properties=records)
    current = [row for row in records if row.get('kind') != 'property_value' or not row.get('valid_to')]
    issues.extend(validate_records(current, graph, []))
    issues.extend(interval_issues(snapshot, records))
    return issues


def interval_issues(snapshot: dict, records: list[dict]) -> list[dict]:
    sources = {row['id']: row for row in snapshot['sources']}
    nodes = {row['id']: row for row in snapshot['nodes']}
    issues = []
    for row in records:
        if row.get('kind') != 'property_value':
            continue
        invalid = False
        for field in ('valid_from', 'valid_to'):
            boundary = row.get(field)
            if field == 'valid_to' and boundary is None:
                continue
            if not isinstance(boundary, dict) or set(boundary) - {'source_id', 'start', 'event_id'}:
                invalid = True
                continue
            source = sources.get(boundary.get('source_id')) if isinstance(boundary.get('source_id'), str) else None
            start = boundary.get('start')
            length = source.get('length', len(source.get('text', ''))) if source else 0
            if not source or type(start) is not int or start < 0 or start > length:
                invalid = True
            if boundary.get('event_id'):
                event = nodes.get(boundary['event_id']) if isinstance(boundary['event_id'], str) else None
                if not event or event.get('node_type') not in {'event', 'action'}:
                    invalid = True
        begin, end = position(row.get('valid_from'), snapshot), position(row.get('valid_to'), snapshot)
        if invalid or begin is None or row.get('valid_to') is not None and (end is None or end <= begin):
            issues.append({'severity': 'error', 'code': 'VALIDITY', 'object_id': row['id'],
                           'message': '属性有效区间必须是合法原文坐标，且valid_to晚于valid_from'})
    return issues


def persist(store, snapshot: dict, records: list[dict], *, expected_revision: int,
            action: str, reason: str) -> int:
    from .batch_write import capture, commit, prepare_state
    batch = capture(store)
    if batch.revision != expected_revision:
        raise ValueError('正式资料版本已变化，请刷新')
    prepared = prepare_state(batch, snapshot, records)
    result = commit(store, prepared, reason, action=action)
    if not prepared.valid:
        raise ValueError('；'.join(row['message'] for row in prepared.diagnostics if row.get('severity') == 'error'))
    return result['revision']


def mutate_records(ops, reason: str, mutation, *, expected_revision: int | None = None, action: str) -> dict:
    from .batch_write import capture, commit, prepare_state
    batch = capture(ops._store)
    if expected_revision is not None and expected_revision != batch.revision:
        raise ValueError('正式资料版本已变化，请刷新')
    graph = deepcopy(batch._graph)
    records = mutation(deepcopy(batch._records), graph)
    prepared = prepare_state(batch, graph, records)
    result = commit(ops._store, prepared, reason, action=action)
    if not prepared.valid:
        raise ValueError('；'.join(row['message'] for row in prepared.diagnostics if row.get('severity') == 'error'))
    return result


def correct(ops, record_id: str, value, reason: str, **changes) -> dict:
    def mutation(records, snapshot):
        row = next((row for row in records if row['id'] == record_id), None)
        if not row or row['kind'] != 'property_value':
            raise ValueError('属性取值记录不存在')
        old_boundary = deepcopy(point(row))
        row['value'] = value
        for name in ('event_id', 'category', 'scope', 'holder_id'):
            if changes.get(name) is not None:
                row[name] = changes[name]
        boundary = None
        if changes.get('event_id'):
            event = next((node for node in snapshot['nodes'] if node['id'] == changes['event_id']), {})
            boundary = point(event)
        if changes.get('source_id') and changes.get('quote'):
            source = ops._store.source(changes['source_id'])
            quote = changes['quote']
            offset = source['text'].find(quote)
            if offset < 0 or source['text'].find(quote, offset + 1) >= 0:
                raise ValueError('更正引文必须逐字且唯一对应原文')
            row['evidence'] = {'source_id': changes['source_id'], 'quote': quote}
            boundary = {'source_id': changes['source_id'], 'start': offset}
        if boundary:
            boundary['event_id'] = row['event_id']
            row['valid_from'] = boundary
            row['disclosure'] = {key: boundary[key] for key in ('source_id', 'start')}
            group = tuple(row.get(key) or ('world' if key == 'scope' else None)
                          for key in ('node_id', 'property_id', 'scope', 'holder_id'))
            for previous in records:
                previous_group = tuple(previous.get(key) or ('world' if key == 'scope' else None)
                                       for key in ('node_id', 'property_id', 'scope', 'holder_id'))
                if previous['id'] != record_id and previous_group == group and previous.get('valid_to') == old_boundary:
                    previous['valid_to'] = deepcopy(boundary)
        return records
    return mutate_records(ops, reason, mutation, action='correct_property') | {'record_id': record_id}


def expire(ops, record_ids: list[str], source_id: str, start: int, *, reason: str, event_id: str | None = None) -> dict:
    def mutation(records, snapshot):
        boundary = {'source_id': source_id, 'start': start, **({'event_id': event_id} if event_id else {})}
        if type(start) is not int or position(boundary, snapshot) is None:
            raise ValueError('失效时间点必须是原文source_id及整数start')
        by_id = {row['id']: row for row in records}
        for identifier in record_ids:
            row = by_id.get(identifier)
            if not row or row.get('kind') != 'property_value':
                raise ValueError(f'属性取值记录不存在：{identifier}')
            row['valid_to'] = boundary
        return records
    return mutate_records(ops, reason, mutation, action='expire_properties') | {'record_ids': record_ids}


def delete_records(ops, record_ids: list[str], reason: str) -> dict:
    def mutation(records, snapshot):
        existing = {row['id'] for row in records}
        if set(record_ids) - existing:
            raise ValueError('要删除的属性记录不存在')
        return [row for row in records if row['id'] not in record_ids]
    return mutate_records(ops, reason, mutation, action='delete_property_records')


def rename(ops, source_id: str, target_id: str, reason: str, expected_revision: int, *, merge: bool = False) -> dict:
    def mutation(records, snapshot):
        definitions = {row['id']: row for row in records if row['kind'] == 'property_definition'}
        if source_id not in definitions or source_id == target_id:
            raise ValueError('原属性不存在或新旧名字相同')
        if merge != (target_id in definitions):
            raise ValueError('合并目标必须存在；改名目标必须未使用')
        if merge:
            source, target = definitions[source_id], definitions[target_id]
            def contract(definition):
                return {'value_type': definition.get('value_type'),
                        'cardinality': definition.get('cardinality'),
                        'symmetric': definition.get('symmetric', False),
                        'semantic': definition.get('semantic'),
                        'target_node_types': frozenset(definition.get('target_node_types') or [])}
            source_contract, target_contract = contract(source), contract(target)
            conflicts = [key for key in source_contract if source_contract[key] != target_contract[key]]
            if conflicts:
                raise ValueError('属性定义不兼容，不能合并：' + '、'.join(conflicts) +
                                 '；请先查证并显式修正定义，合并不会自动改变属性语义')
        result = []
        for row in records:
            if row['id'] == source_id and row['kind'] == 'property_definition':
                if merge:
                    continue
                row['id'] = target_id
            if row.get('property_id') == source_id:
                row['property_id'] = target_id
            result.append(row)
        return result
    return mutate_records(ops, reason, mutation, expected_revision=expected_revision,
                          action='merge_properties' if merge else 'rename_property')


def rollback_plan(ops, source_id: str, start: int, expected_revision: int) -> tuple[dict, list[dict], dict]:
    from .property_store import PropertyStore
    snapshot = ops._store.snapshot()
    boundary = {'source_id': source_id, 'start': start}
    cutoff = position(boundary, snapshot)
    source = ops._store.source(source_id)
    if type(start) is not int or start < 0 or start > len(source['text']) or cutoff is None:
        raise ValueError('回退时间点必须是合法原文Unicode位置')
    if expected_revision != snapshot['revision']:
        raise ValueError('正式资料版本已变化')
    objects = deepcopy(snapshot['objects'])
    removed = set()
    for row in objects:
        if row['kind'] == 'node':
            at = position(point(row), snapshot)
            if at is not None and at > cutoff:
                removed.add(row['id'])
    # Remove dependent viewpoints and their contents if their owner disappeared.
    while True:
        extra = {row['id'] for row in objects if row.get('holder_id') in removed
                 or row.get('context_id') in removed or row.get('parent_id') in removed}
        if extra <= removed:
            break
        removed.update(extra)
    unpositioned = [row['id'] for row in objects if row['kind'] == 'node' and point(row) is None]
    cleared_occurrences = []
    kept = []
    for row in objects:
        if row['id'] in removed:
            continue
        for key in ('events', 'participants', 'predecessors', 'successors'):
            if key in row:
                row[key] = [identifier for identifier in row[key] if identifier not in removed]
        occurrence = row.get('occurrence')
        if occurrence:
            def dangling(value):
                if isinstance(value, dict):
                    return any(key in {'event_id', 'timeline_id'} and isinstance(item, str) and item in removed
                               or dangling(item) for key, item in value.items())
                return isinstance(value, list) and any(dangling(item) for item in value)
            if dangling(occurrence):
                row.pop('occurrence', None)
                cleared_occurrences.append(row['id'])
        if row.get('source_marks'):
            row['source_marks'] = [mark for mark in row['source_marks']
                                   if mark.get('event_id') not in removed
                                   and source_order(snapshot).get(mark.get('source_id'), -1) <= cutoff[0]
                                   and (position(mark, snapshot) is None or (position(mark, snapshot) or (-1, -1)) <= cutoff)]
        if row.get('evidence_spans'):
            row['evidence_spans'] = [span for span in row['evidence_spans']
                                     if position(span, snapshot) is None or (position(span, snapshot) or (-1, -1)) <= cutoff]
        kept.append(row)
    graph = snapshot | {'objects': kept}
    for kind, key in (('node', 'nodes'), ('context', 'contexts'), ('timeline', 'timelines'), ('source_order', 'source_orders')):
        graph[key] = [row for row in kept if row['kind'] == kind]
    records = []
    removed_records = []
    cleared = []
    definitions = {row['id']: row for row in PropertyStore(ops._store).accepted_records() if row['kind'] == 'property_definition'}
    for row in deepcopy(PropertyStore(ops._store).accepted_records()):
        begin = position(point(row), snapshot)
        refs = [row.get(key) for key in ('node_id', 'holder_id', 'event_id', 'applies_to')]
        refs.append((row.get('valid_from') or {}).get('event_id'))
        definition = definitions.get(str(row.get('property_id') or ''), {})
        if definition.get('value_type') in {'node', 'either'} and isinstance(row.get('value'), str):
            refs.append(row['value'])
        if begin is not None and begin > cutoff or any(ref in removed for ref in refs if isinstance(ref, str)):
            removed_records.append(row['id'])
            continue
        end = position(row.get('valid_to'), snapshot)
        if end is not None and end > cutoff or (row.get('valid_to') or {}).get('event_id') in removed:
            row['valid_to'] = None
            cleared.append(row['id'])
        if definition.get('value_type') in {'node', 'either'} and isinstance(row.get('value'), list):
            row['value'] = [value for value in row['value'] if not isinstance(value, str) or value not in removed]
        if row.get('related_nodes'):
            row['related_nodes'] = [identifier for identifier in row['related_nodes'] if identifier not in removed]
        records.append(row)
    issues = diagnostics(ops._store, graph, records)
    if unpositioned:
        issues.append({'severity': 'warning', 'code': 'ROLLBACK_UNPOSITIONED',
                       'message': '无披露坐标的手工节点保留，不能按原文时间点判断其归属',
                       'node_ids': unpositioned})
    report = {'valid': not any(row.get('severity') == 'error' for row in issues), 'diagnostics': issues,
              'expected_revision': expected_revision, 'source_id': source_id, 'start': start,
              'deleted_node_ids': sorted(removed), 'deleted_record_ids': removed_records,
              'reopened_record_ids': cleared, 'cleared_occurrence_ids': cleared_occurrences,
              'unpositioned_node_ids': unpositioned}
    return graph, records, report


def rollback(ops, source_id: str, start: int, expected_revision: int, reason: str) -> dict:
    graph, records, report = rollback_plan(ops, source_id, start, expected_revision)
    if not report['valid']:
        return report | {'status': 'rejected'}
    revision = persist(ops._store, graph, records, expected_revision=expected_revision,
                       action='rollback', reason=reason)
    return report | {'status': 'accepted', 'revision': revision}

def interval_state(store, node_id: str, property_id: str, at=None, *, scope='world', holder_id=None) -> dict:
    from .property_core import canonical_definitions
    from .property_store import PropertyStore, relation_target
    snapshot = store.snapshot()
    if isinstance(at, str):
        event = next((node for node in snapshot['nodes'] if node['id'] == at), None)
        if not event:
            raise ValueError('阅读事件不存在')
        boundary = point(event)
        if boundary:
            spans = [span for span in event.get('evidence_spans', []) if span.get('source_id') == boundary['source_id']]
            if spans:
                boundary['start'] = max(span['end'] - 1 for span in spans)
    else:
        boundary = at
    cutoff = position(boundary, snapshot) if boundary is not None else None
    if at is not None and cutoff is None:
        raise ValueError('阅读时间点必须是事件ID或原文坐标')
    rows = PropertyStore(store).accepted_records()
    applicable = []
    for row in rows:
        if row.get('kind') != 'property_value' or row.get('node_id') != node_id or row.get('property_id') != property_id:
            continue
        if (row.get('scope') or 'world') != scope or row.get('holder_id') != holder_id:
            continue
        begin, end = position(point(row), snapshot), position(row.get('valid_to'), snapshot)
        if begin is not None and (cutoff is None and end is None or cutoff is not None and begin <= cutoff and (end is None or cutoff < end)):
            applicable.append(row)
    if not applicable:
        return {'state': {'kind': 'unrecorded'}, 'record_ids': []}
    nodes = {node['id']: node for node in snapshot['nodes']}
    definitions = canonical_definitions(rows)
    if all(relation_target(row, nodes, definitions) is not None for row in applicable):
        return {'state': {'kind': 'known', 'value': list(dict.fromkeys(row['value'] for row in applicable))},
                'record_ids': [row['id'] for row in applicable]}
    latest = max(applicable, key=lambda row: position(point(row), snapshot) or (-1, -1))
    return {'state': {'kind': 'known', 'value': latest['value']}, 'record_ids': [latest['id']]}

def boundary_at(snapshot: dict, at) -> dict | None:
    if at is None:
        return None
    if isinstance(at, dict):
        return at
    event = next((node for node in snapshot['nodes'] if node['id'] == at), None)
    if not event:
        raise ValueError('阅读事件不存在')
    from .browse_view import anchor_event
    is_composite_event = event.get('node_type') == 'event' and anchor_event(snapshot, event)['id'] != event['id']
    if event.get('node_type') in {'storyline', 'big_event', 'scene'} or is_composite_event:
        from .browse_view import cut_for
        resolved = cut_for(snapshot, event['id'])
        return {'source_id': resolved['source_id'], 'start': resolved['start']} if resolved else None
    boundary = point(event)
    if boundary:
        spans = [span for span in event.get('evidence_spans', []) if span.get('source_id') == boundary['source_id']]
        if spans:
            boundary['start'] = max(span['end'] - 1 for span in spans)
    return boundary


def ended_at(records: list[dict], snapshot: dict, at=None) -> dict[str, dict]:
    cutoff = position(boundary_at(snapshot, at), snapshot) if at is not None else None
    return {row['id']: {'kind': 'interval_end', 'boundary': row['valid_to']}
            for row in records if row.get('kind') == 'property_value' and row.get('valid_to')
            and (at is None or cutoff is not None and
                 (position(row['valid_to'], snapshot) or (float('inf'), 0)) <= cutoff)}
