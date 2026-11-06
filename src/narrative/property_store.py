"""Event-bound property facts and read-only derived state projections."""
from __future__ import annotations

import json
from pathlib import Path

from . import property_categories, property_labels
from .graph_store import GraphStore
from .property_core import canonical_definitions, change_group, reduce_changes
from .property_core import relation_target as relation_target
from .temporal_state import event_order, projected_changes, selected_changes, state_at

PROPERTY_CONTRACT_VERSION = 'property-state/15'
PROPERTY_RECORD_KINDS = ('property_definition', 'property_change')
PROPERTY_EFFECT_OPS = ('set', 'add', 'remove', 'clear', 'unknown')
PROPERTY_STATE_CELL_KINDS = ('known', 'unknown', 'absent', 'conflict', 'unrecorded')


def relation_targets(record, nodes, definitions):
    definition = definitions.get(record.get('property_id'), {})
    if definition.get('value_type') not in {'node', 'either'}:
        return []
    values = record.get('value') if isinstance(record.get('value'), list) else [record.get('value')]
    return [v for v in values if isinstance(v, str) and v in nodes and nodes.get(record.get('node_id'), {}).get('node_type') == 'person' and nodes[v].get('node_type') == 'person']


class PropertyStore:
    def __init__(self, store: GraphStore | str | Path, *, create=False):
        self.store = store if isinstance(store, GraphStore) else GraphStore(store, create=create)

    @staticmethod
    def _records_from_db(db):
        return [json.loads(r['data']) for r in db.execute('SELECT data FROM property_records')]

    def accepted_records(self):
        with self.store.connect() as db:
            return self._records_from_db(db)

    def precheck(self, records, expected_revision):
        from .batch_write import capture, prepare
        return prepare(capture(self.store), [], records, expected_revision).diagnostics

    @staticmethod
    def _check_scope(scope, holder_id):
        if scope not in {'world', 'claim', 'cognition'} or scope == 'world' and holder_id is not None or scope != 'world' and not holder_id:
            raise ValueError('world不带holder，claim/cognition查询须指定holder')

    def state(self, node_id, property_id, at=None, *, scope='world', holder_id=None, context_id='canon', disclosure_cut=None, include_diagnostics=False):
        self._check_scope(scope, holder_id)
        result = state_at(self.store, node_id, property_id, disclosure_cut if disclosure_cut is not None else at,
                          scope=scope, holder_id=holder_id, context_id=context_id)
        return result

    def disclosure_state(self, node_id, property_id, disclosure_cut, *, scope='world', holder_id=None, context_id='canon'):
        return self.state(node_id, property_id, disclosure_cut, scope=scope, holder_id=holder_id, context_id=context_id)

    def history(self, node_id, property_id, *, scope='world', holder_id=None, context_id='canon'):
        from .property_core import history
        self._check_scope(scope, holder_id)
        rows = history(self.accepted_records(), node_id, property_id, scope, holder_id)
        order = event_order(self.store.snapshot())
        changes = [r for r in rows if r.get('kind') == 'property_change' and r.get('context_id', 'canon') == context_id]
        changes.sort(key=lambda r: (order.get(r['event_id'], (float('inf'), 0)), r['sequence']))
        return [r for r in rows if r.get('kind') == 'property_definition'] + changes

    def audit(self, node_ids=None):
        from .property_audit import audit_properties
        snapshot = self.store.snapshot()
        return audit_properties(self.accepted_records(), snapshot['nodes'], node_ids=node_ids, categories=property_categories.load(self.store))

    def categories(self):
        return property_categories.load(self.store)

    def set_categories(self, config):
        previous = property_categories.load(self.store)
        vocabulary = property_categories.save(self.store, config)
        renamed = property_categories.renames(previous, vocabulary)
        nodes = {n['id']: n for n in self.store.snapshot()['nodes']}
        pairs = []
        for row in self.accepted_records():
            if row.get('kind') != 'property_change':
                continue
            kind = nodes.get(row.get('node_id'), {}).get('node_type')
            category, _ = property_categories.resolve(row, kind, previous, {})
            if (kind, category) in renamed:
                pairs.append((row['id'], renamed[(kind, category)]))
        moved = self._apply_categorizations(pairs, '分类改名')['moved'] if pairs else []
        return {'vocabulary': vocabulary, 'moved': moved}

    def _apply_categorizations(self, pairs, note):
        from .temporal_state import persist
        snapshot = self.store.snapshot()
        targets = dict(pairs)
        records = self.accepted_records()
        for row in records:
            if row['id'] in targets and row.get('kind') == 'property_change':
                row['category'] = targets[row['id']]
        revision = persist(self.store, snapshot, records, expected_revision=snapshot['revision'], action='categorize', reason=note)
        return {'revision': revision, 'moved': list(targets), 'category': next(iter(targets.values()), '')}

    def categorize(self, targets, category, note=''):
        if not isinstance(category, str) or not category.strip() or len(category.strip()) > 20:
            raise ValueError('分类须为1至20字')
        rows = {r['id']: r for r in self.accepted_records()}
        if not targets or any(rows.get(t, {}).get('kind') != 'property_change' for t in targets):
            raise ValueError('请选择存在的属性变更记录')
        return self._apply_categorizations([(t, category.strip()) for t in dict.fromkeys(targets)], note or '移动分类')

    def _container_progress(self, node_id, snapshot, records, cut=None):
        nodes = {n['id']: n for n in snapshot['nodes']}
        owner = nodes.get(node_id, {})
        level = {'storyline': ('big_event', property_labels.STORYLINE_PROGRESS), 'big_event': ('scene', property_labels.BIG_EVENT_PROGRESS)}.get(owner.get('node_type'))
        if not level:
            return []
        children = {c for c in owner.get('events', []) if nodes.get(c, {}).get('node_type') == level[0]}
        candidates = [r for r in projected_changes(snapshot, records, cut) if r.get('node_id') in children and r.get('property_id') == level[1]]
        if not candidates:
            return []
        order = event_order(snapshot)
        latest = max(candidates, key=lambda r: (order.get(r['event_id'], (-1, -1)), r['sequence']))
        return [latest | {'node_id': node_id, 'derived_from': latest['node_id'], 'derived': True}]

    def node_properties(self, node_id, cut=None, include_derived=False):
        snapshot = self.store.snapshot()
        records = self.accepted_records()
        nodes = {n['id']: n for n in snapshot['nodes']}
        names = {identifier: n.get('name', identifier) for identifier, n in nodes.items()}
        node_type = nodes.get(node_id, {}).get('node_type')
        config = property_categories.load(self.store)
        definitions = canonical_definitions(records)
        visible, limit = selected_changes(records, snapshot, cut)
        order = event_order(snapshot)
        groups = {}
        for row in records:
            if row.get('kind') == 'property_change' and row.get('node_id') == node_id:
                groups.setdefault(change_group(row), []).append(row)
        if include_derived:
            for row in self._container_progress(node_id, snapshot, records, cut):
                groups.setdefault(change_group(row), []).append(row)
        visible_ids = {r['id'] for r in visible}
        items, hidden = [], 0
        for group, changes in groups.items():
            shown = [r for r in changes if r['id'] in visible_ids or r.get('derived')]
            result = reduce_changes(shown, definitions.get(group[1], {}), nodes, event_order=order, cut=limit)
            history = result['history_changes']
            hidden += len(changes) - len(history)
            if not history:
                continue
            latest = history[-1]
            category, category_source = property_categories.resolve(latest, node_type, config, {})
            active = set(result['active_change_ids'])
            decorated = []
            for row in history:
                value = row.get('value')
                ref = {'id': value, 'name': names[value]} if isinstance(value, str) and value in names else None
                decorated.append(row | {'value_node': ref, 'event_name': names.get(row['event_id']),
                    'holder_name': names.get(row.get('holder_id')), 'property_label': property_labels.label_for(row['property_id']),
                    'category': category, 'category_source': category_source, 'is_current': row['id'] in active,
                    'is_latest': row is latest})
            cell = result['state']
            value = cell.get('value')
            ref = {'id': value, 'name': names[value]} if isinstance(value, str) and value in names else None
            items.append({'property_id': group[1], 'property_ids': [group[1]], 'aspect': group[1], 'label': property_labels.label_for(group[1]),
                'category': category, 'category_source': category_source, 'other_categories': [], 'state': cell,
                'state_kind': cell['kind'], 'value': value, 'value_node': ref, 'scope': group[3], 'scopes': [group[3]],
                'holder_id': group[4], 'holder_name': names.get(group[4]), 'context_id': group[2], 'value_type': definitions.get(group[1], {}).get('value_type'),
                'event_id': latest['event_id'], 'start': (latest.get('disclosure') or {}).get('start'), 'disclosed_at': names.get(latest['event_id']),
                'is_current': cell['kind'] != 'unrecorded', 'record_count': len(history), 'later_count': len(changes)-len(history),
                'records': decorated, 'history_changes': history, 'active_change_ids': result['active_change_ids'], 'diagnostics': result['diagnostics']})
        projection = projected_changes(snapshot, records, cut)
        inbound, claims, heard = [], [], []
        for row in projection:
            if row.get('state_kind') != 'known':
                continue
            property_id = row['property_id']
            value = row.get('value')
            values = value if isinstance(value, list) else [value]
            definition = definitions.get(property_id, {})
            if definition.get('value_type') in {'node', 'either'} and node_id in values and row['node_id'] != node_id:
                inbound.append({'record_id': row['id'], 'node_id': row['node_id'], 'node_name': names.get(row['node_id']),
                    'property_id': property_id, 'property_label': property_labels.label_for(property_id), 'scope': row.get('scope', 'world'),
                    'holder_id': row.get('holder_id'), 'event_id': row['event_id'], 'event_name': names.get(row['event_id']), 'evidence': row.get('evidence'),
                    'active_change_ids': row['active_change_ids']})
            if row.get('scope', 'world') != 'world':
                projected = row | {'record_id': row['id'], 'node_name': names.get(row['node_id']), 'holder_name': names.get(row.get('holder_id')),
                                   'property_label': property_labels.label_for(property_id), 'scope_label': '声称' if row['scope'] == 'claim' else '认知'}
                if row.get('holder_id') == node_id and row['node_id'] != node_id:
                    claims.append(projected)
                elif row['node_id'] == node_id and row.get('holder_id') != node_id:
                    heard.append(projected)
        ordered_categories = property_categories.order_categories(sorted({i['category'] for i in items}), node_type, config)
        children = [{'id': c, 'name': names[c], 'node_type': nodes[c].get('node_type'), 'start': (nodes[c].get('disclosure') or {}).get('start')}
                    for c in nodes.get(node_id, {}).get('events', []) if c in nodes]
        from .node_involvement import involvement
        return {'node_id': node_id, 'node_type': node_type, 'revision': snapshot['revision'], 'vocabulary': property_categories.vocabulary_for(node_type, config),
                'categories': [{'key': c, 'count': sum(i['category'] == c for i in items), 'record_count': sum(i['record_count'] for i in items if i['category'] == c),
                                'items': [i for i in items if i['category'] == c]} for c in ordered_categories],
                'properties': items, 'inbound': inbound, 'claims': claims, 'heard': heard, 'children': children,
                'involvement': involvement(snapshot, records, node_id, cut), 'cut': cut, 'later_count': hidden}

    def world_state(self, at=None):
        snapshot = self.store.snapshot()
        records = self.accepted_records()
        groups = {change_group(r) for r in records if r.get('kind') == 'property_change' and r.get('scope', 'world') == 'world'}
        flat, nodes = [], {}
        for group in sorted(groups):
            result = state_at(self.store, group[0], group[1], at, context_id=group[2])
            nodes.setdefault(group[0], {})[group[1]] = result
            flat.append({'node_id': group[0], 'property_id': group[1], 'context_id': group[2], **result})
        return {'at': at, 'revision': snapshot['revision'], 'nodes': nodes, 'properties': flat}
