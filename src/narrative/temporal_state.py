"""Schema 11 event facts; current state is reduced, never stored as intervals."""
from __future__ import annotations

from copy import deepcopy

from .content_errors import (
    ContentConflictError,
    ContentGuardError,
    ContentValidationError,
    content_write,
)


def point(row):
    value = row.get('disclosure')
    if not isinstance(value, dict):
        return None
    return {k: value[k] for k in ('source_id', 'start') if k in value}


def source_order(snapshot):
    result = {r['source_id']: r['ordinal'] for r in snapshot.get('source_orders', []) if r.get('context_id', 'canon') == 'canon'}
    for source in snapshot.get('sources', []):
        if source['id'] not in result:
            result[source['id']] = max(result.values(), default=-1) + 1
    return result


def position(boundary, snapshot):
    if not isinstance(boundary, dict):
        return None
    ordinal = source_order(snapshot).get(boundary.get('source_id'))
    start = boundary.get('start')
    return (ordinal, start) if ordinal is not None and type(start) is int else None


def event_order(snapshot):
    return {n['id']: rank for n in snapshot.get('nodes', []) if n.get('node_type') in {'event', 'action'}
            and (rank := position(point(n), snapshot)) is not None}


def normalize_changes(records, snapshot):
    nodes = {n['id']: n for n in snapshot.get('nodes', [])}
    result = deepcopy(records)
    for row in result:
        if row.get('kind') != 'property_change':
            continue
        event = nodes.get(row.get('event_id'), {}) if isinstance(row.get('event_id'), str) else {}
        row.setdefault('context_id', event.get('context_id', 'canon'))
        row.setdefault('scope', 'world')
        if 'sequence' not in row:
            disclosure = point(row)
            if disclosure and type(disclosure.get('start')) is int:
                row['sequence'] = disclosure['start']
            else:
                raise ValueError('无原文披露位置的变更必须显式提供sequence')
    return result


def prepare(snapshot, existing, objects, records):
    graph = deepcopy(snapshot)
    merged = {r['id']: r for r in snapshot.get('objects', [])}
    merged.update({r['id']: r for r in objects})
    graph['objects'] = list(merged.values())
    for kind, key in (('node', 'nodes'), ('context', 'contexts'), ('timeline', 'timelines'), ('source_order', 'source_orders')):
        graph[key] = [r for r in graph['objects'] if r['kind'] == kind]
    if any(not isinstance(r.get('id'), str) or not r['id'] for r in records):
        raise ValueError('属性记录必须具有非空字符串id；仅property_change可由Operations分配，旧格式不受支持')
    values = {r['id']: deepcopy(r) for r in existing}
    values.update({r['id']: r for r in normalize_changes(records, graph)})
    return graph, list(values.values())



def _reducible(row):
    return (row.get('kind') == 'property_change'
            and all(isinstance(row.get(k, default), str) for k, default in [('node_id', None), ('property_id', None), ('event_id', None), ('context_id', 'canon'), ('scope', 'world')])
            and (row.get('holder_id') is None or isinstance(row.get('holder_id'), str))
            and isinstance(row.get('op'), str) and row['op'] in {'set', 'add', 'remove', 'clear', 'unknown'}
            and type(row.get('sequence')) is int and row['sequence'] >= 0)

def change_issues(snapshot, records):
    from .property_core import canonical_definitions, change_group, reduce_changes
    nodes = {n['id']: n for n in snapshot.get('nodes', [])}
    definitions = canonical_definitions(records)
    groups = {}
    for row in records:
        if _reducible(row):
            groups.setdefault(change_group(row), []).append(row)
    issues = []
    order = event_order(snapshot)
    for group, rows in groups.items():
        definition = definitions.get(group[1])
        if definition:
            issues.extend(reduce_changes(rows, definition, nodes, event_order=order)['diagnostics'])
    return issues


def diagnostics(store, snapshot, records, *, sources=None):
    from .graph_service import GraphService
    from .property_core import validate_records
    if sources is None:
        sources = {s['id']: s for s in store.sources(include_text=True)}
    graph = snapshot | {'sources': [s | {'text': sources[s['id']]['text']} for s in snapshot.get('sources', [])]}
    active = [r for r in graph['objects'] if not r.get('retracted')]
    issues = GraphService(store).precheck(active, snapshot['revision'], _snapshot=graph, _accepted_properties=records)
    issues.extend(validate_records(records, graph, [], sources=sources))
    issues.extend(change_issues(graph, records))
    return issues


def boundary_at(snapshot, at):
    if at is None:
        return None
    if isinstance(at, dict):
        if 'event_id' in at and 'source_id' not in at:
            return boundary_at(snapshot, at['event_id'])
        return at
    node = next((n for n in snapshot.get('nodes', []) if n['id'] == at), None)
    if not node:
        raise ValueError('阅读时间点不存在')
    if node.get('node_type') in {'storyline', 'big_event', 'scene'}:
        from .browse_view import cut_for
        resolved = cut_for(snapshot, at)
        return {'source_id': resolved['source_id'], 'start': resolved['start']} if resolved else None
    if node.get('events'):
        from .browse_view import anchor_event
        anchor = anchor_event(snapshot, node)
        if anchor['id'] != node['id']:
            return boundary_at(snapshot, anchor['id'])
    boundary = point(node)
    if boundary:
        spans = [s for s in node.get('evidence_spans', []) if s.get('source_id') == boundary['source_id']]
        if spans:
            boundary['start'] = max(s['end'] - 1 for s in spans)
    return boundary


def selected_changes(records, snapshot, at=None):
    from .property_core import change_key
    order = event_order(snapshot)
    if at is None or isinstance(at, str) and at in order or isinstance(at, dict) and 'event_id' in at:
        return [r for r in records if r.get('kind') == 'property_change'], at
    boundary = boundary_at(snapshot, at)
    cutoff = position(boundary, snapshot)
    if cutoff is None:
        raise ValueError('阅读切点必须是事件或合法原文坐标')
    return [r for r in records if r.get('kind') == 'property_change'
            and (key := change_key(r, order)) is not None and key[0] <= cutoff
            and ((own_position := position(point(r), snapshot)) is None or own_position <= cutoff)], None


def state_at(store, node_id, property_id, at=None, *, scope='world', holder_id=None, context_id='canon'):
    from .property_core import canonical_definitions, reduce_changes
    from .property_store import PropertyStore
    snapshot = store.snapshot()
    records = PropertyStore(store).accepted_records()
    rows, cut = selected_changes(records, snapshot, at)
    rows = [r for r in rows if r.get('node_id') == node_id and r.get('property_id') == property_id
            and r.get('scope', 'world') == scope and r.get('holder_id') == holder_id and r.get('context_id', 'canon') == context_id]
    definition = canonical_definitions(records).get(property_id, {})
    return reduce_changes(rows, definition, {n['id']: n for n in snapshot['nodes']}, event_order=event_order(snapshot), cut=cut)


def active_change_ids(records, snapshot, at=None):
    from .property_core import canonical_definitions, change_group, reduce_changes
    rows, cut = selected_changes(records, snapshot, at)
    groups = {}
    for row in rows:
        if _reducible(row):
            groups.setdefault(change_group(row), []).append(row)
    definitions = canonical_definitions(records)
    nodes = {n['id']: n for n in snapshot.get('nodes', [])}
    active = set()
    for group, changes in groups.items():
        result = reduce_changes(changes, definitions.get(group[1], {}), nodes, event_order=event_order(snapshot), cut=cut)
        active.update(result['active_change_ids'])
    return active


def persist(store, snapshot, records, *, expected_revision, action, reason):
    from .batch_write import capture, commit, prepare_state
    batch = capture(store)
    if batch.revision != expected_revision:
        raise ContentConflictError('正式资料版本已变化，请刷新')
    prepared = prepare_state(batch, snapshot, records)
    result = commit(store, prepared, reason, action=action)
    if not prepared.valid:
        raise ContentValidationError('；'.join(r['message'] for r in prepared.diagnostics if r.get('severity') == 'error'))
    return result['revision']


def mutate_records(ops, reason, mutation, *, expected_revision=None, action):
    from .batch_write import capture, commit, prepare_state
    batch = capture(ops._store)
    if expected_revision is not None and expected_revision != batch.revision:
        raise ContentConflictError('正式资料版本已变化，请刷新')
    graph = deepcopy(batch._graph)
    records = mutation(deepcopy(batch._records), graph)
    prepared = prepare_state(batch, graph, records)
    result = commit(ops._store, prepared, reason, action=action)
    if not prepared.valid:
        raise ContentValidationError('；'.join(r['message'] for r in prepared.diagnostics if r.get('severity') == 'error'))
    return result


@content_write
def correct(ops, record_id, changes, reason, *, expected_revision=None):
    def mutation(records, snapshot):
        row = next((r for r in records if r['id'] == record_id), None)
        if not row or row.get('kind') != 'property_change':
            raise ContentGuardError('属性变更不存在')
        allowed = {'op', 'value', 'event_id', 'category', 'scope', 'holder_id', 'context_id', 'source', 'related_nodes', 'source_id', 'quote'}
        if set(changes) - allowed:
            raise ValueError('更正字段不支持：'+','.join(set(changes)-allowed))
        for key, value in changes.items():
            if key not in {'source_id', 'quote'}:
                row[key] = value
        if row.get('op') in {'clear', 'unknown'}:
            row.pop('value', None)
        if changes.get('source_id') and changes.get('quote'):
            source = ops._store.source(changes['source_id'])
            quote = changes['quote']
            start = source['text'].find(quote)
            if start < 0 or source['text'].find(quote, start+1) >= 0:
                raise ValueError('更正引文须逐字唯一匹配')
            row['evidence'] = {'source_id': changes['source_id'], 'quote': quote}
            row['disclosure'] = {'source_id': changes['source_id'], 'start': start}
        return records
    return mutate_records(ops, reason, mutation, expected_revision=expected_revision, action='correct_property') | {'record_id': record_id}


def delete_records(ops, record_ids, reason, *, expected_revision=None):
    def mutation(records, snapshot):
        if set(record_ids) - {r['id'] for r in records}:
            raise ValueError('要删除的属性记录不存在')
        return [r for r in records if r['id'] not in record_ids]
    return mutate_records(ops, reason, mutation, expected_revision=expected_revision, action='delete_property_records')


def rename(ops, source_id, target_id, reason, expected_revision, *, merge=False):
    def mutation(records, snapshot):
        from .property_core import canonical_definitions
        definitions = canonical_definitions(records)
        if source_id not in definitions or source_id == target_id or merge != (target_id in definitions):
            raise ValueError('属性改名或合并目标无效')
        for identifier in (source_id, target_id):
            if identifier == '持有者' and identifier in definitions and not definitions[identifier].get('semantic'):
                definitions[identifier]['semantic'] = 'item_holder'
        if merge:
            fields = ('value_type', 'cardinality', 'symmetric', 'semantic', 'target_node_types')
            if any(definitions[source_id].get(k, False if k == 'symmetric' else None) != definitions[target_id].get(k, False if k == 'symmetric' else None) for k in fields):
                raise ValueError('属性定义不兼容，不能合并')
        result = []
        for row in records:
            if row['id'] == source_id and row.get('kind') == 'property_definition':
                if merge:
                    continue
                row['id'] = target_id
            if row.get('property_id') == source_id:
                row['property_id'] = target_id
            result.append(row)
        return result
    return mutate_records(ops, reason, mutation, expected_revision=expected_revision, action='merge_properties' if merge else 'rename_property')


def rollback_plan(ops, source_id, start, expected_revision):
    from .property_store import PropertyStore
    snapshot = ops._store.snapshot()
    source = ops._store.source(source_id)
    cutoff = position({'source_id': source_id, 'start': start}, snapshot)
    if type(start) is not int or not 0 <= start <= len(source['text']) or cutoff is None or expected_revision != snapshot['revision']:
        raise ValueError('回退位置或修订无效')
    graph = deepcopy(snapshot)
    removed = {r['id'] for r in graph['nodes'] if (p := position(point(r), snapshot)) is not None and p > cutoff}
    while True:
        extra = {r['id'] for r in graph['objects'] if any(r.get(k) in removed for k in ('holder_id', 'context_id', 'parent_id'))}
        if extra <= removed:
            break
        removed |= extra
    kept = []
    for row in graph['objects']:
        if row['id'] in removed:
            continue
        for key in ('events', 'participants', 'predecessors', 'successors'):
            if key in row:
                row[key] = [v for v in row[key] if v not in removed]
        kept.append(row)
    graph['objects'] = kept
    for kind, key in (('node', 'nodes'), ('context', 'contexts'), ('timeline', 'timelines'), ('source_order', 'source_orders')):
        graph[key] = [r for r in kept if r['kind'] == kind]
    records, deleted = [], []
    for row in PropertyStore(ops._store).accepted_records():
        refs = [row.get(k) for k in ('node_id', 'event_id', 'holder_id', 'context_id')]
        value = row.get('value')
        refs += value if isinstance(value, list) else [value]
        at = position(point(row), snapshot)
        if row.get('kind') == 'property_change' and (at is not None and at > cutoff or any(isinstance(r, str) and r in removed for r in refs)):
            deleted.append(row['id'])
        else:
            records.append(row)
    issues = diagnostics(ops._store, graph, records)
    report = {'valid': not any(r['severity'] == 'error' for r in issues), 'diagnostics': issues, 'expected_revision': expected_revision,
              'source_id': source_id, 'start': start, 'deleted_node_ids': sorted(removed), 'deleted_record_ids': deleted}
    return graph, records, report


def rollback(ops, source_id, start, expected_revision, reason):
    graph, records, report = rollback_plan(ops, source_id, start, expected_revision)
    if not report['valid']:
        return report | {'status': 'rejected'}
    revision = persist(ops._store, graph, records, expected_revision=expected_revision, action='rollback', reason=reason)
    return report | {'revision': revision, 'status': 'accepted'}


def projected_changes(graph, records, cut=None):
    """Current scoped facts, including collection reductions, never raw positives."""
    from .property_core import canonical_definitions, change_group, reduce_changes, relation_target
    rows, limit = selected_changes(records, graph, cut)
    groups = {}
    for row in rows:
        if _reducible(row):
            groups.setdefault(change_group(row), []).append(row)
    definitions = canonical_definitions(records)
    nodes = {n['id']: n for n in graph.get('nodes', [])}
    result = []
    for group, changes in groups.items():
        definition = definitions.get(group[1], {})
        state = reduce_changes(changes, definition, nodes, event_order=event_order(graph), cut=limit)
        history = state['history_changes']
        if not history:
            continue
        active = [r for r in history if r['id'] in state['active_change_ids']]
        base = deepcopy((active or history)[-1])
        base.update(derived=True, state_kind=state['state']['kind'], active_change_ids=state['active_change_ids'],
                    provenance=deepcopy(active), history_change_ids=[r['id'] for r in history])
        base.pop('value', None)
        if state['state']['kind'] == 'known':
            value = state['state']['value']
            relation = definition.get('cardinality', 'one') == 'one' and any(relation_target(r, nodes, definitions) is not None for r in changes)
            if relation:
                for target in value:
                    contributor = next((r for r in reversed(active) if r.get('value') == target), base)
                    result.append(deepcopy(contributor) | {'derived': True, 'state_kind': 'known', 'op': 'set',
                                          'value': target, 'active_change_ids': [contributor['id']], 'provenance': [deepcopy(contributor)],
                                          'history_change_ids': base['history_change_ids']})
                continue
            base['value'] = value
            base['op'] = 'set'
        result.append(base)
    return result
