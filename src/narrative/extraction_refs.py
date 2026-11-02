"""Revision-local typed references; @indices never become persisted aliases."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from copy import deepcopy

ENTITY_TYPES = {'person', 'group', 'item', 'location'}
MOMENT_TYPES = {'event', 'action'}
ID_PATTERN = re.compile(r'[PGILEASTBN][0-9]+(?:\.[0-9]+)*\Z')


class References:
    def __init__(self, nodes: list[dict], current: list[dict]):
        self.local = {row['reference']: row for row in nodes}
        self.current = {row['id']: row for row in current}
        self.aliases = {}
        identities = {}
        for ref, row in self.local.items():
            key = json.dumps([row.get('type'), row.get('name'), (row.get('quotes') or [''])[0],
                              row.get('context_id', 'canon')],
                             ensure_ascii=False, sort_keys=True)
            canonical = identities.setdefault(key, ref)
            self.aliases[ref] = canonical
        self.names = defaultdict(list)
        for ref, row in self.local.items():
            self.names[row['name']].append((ref, row, True))
        for ref, row in self.current.items():
            self.names[row.get('name')].append((ref, row, False))

    def resolve(self, value, *, allowed=None, context=None, optional=False, literal=False):
        if value is None or value == '':
            if optional:
                return ''
            raise ValueError('必须提供节点引用')
        if not isinstance(value, str):
            raise ValueError('节点引用必须是正式ID或@索引字符串')
        if literal and value in {'无', '未知'}:
            return value
        if value.startswith('@'):
            if not re.fullmatch(r'@[1-9][0-9]*', value) or value not in self.local:
                raise ValueError(f'局部引用{value}无效，索引按原始nodes完整数组从1起，拒绝项不重编号')
            canonical = self.aliases[value]
            candidates = [(canonical, self.local[canonical], True)]
        elif value in self.current:
            candidates = [(value, self.current[value], False)]
        elif ID_PATTERN.fullmatch(value):
            raise ValueError(f'正式节点ID不存在：{value}；若这是新节点名称请用@索引')
        else:
            candidates = self.names.get(value, [])
            if not candidates and literal:
                return value
        valid = []
        for ref, row, local in candidates:
            kind = row.get('type') if local else row.get('node_type')
            if row.get('retracted') or allowed is not None and kind not in allowed:
                continue
            if context is not None and row.get('context_id', 'canon') != context:
                continue
            canonical = self.aliases.get(ref, ref)
            identity = row.get('existing_id') if local else ref
            valid.append((identity or canonical, canonical, local))
        if not value.startswith('@') and value not in self.current:
            local_matches = [item for item in valid if item[2]]
            if local_matches:
                valid = local_matches
        unique = {}
        for identity, ref, _local in valid:
            unique.setdefault(identity, ref)
        if len(unique) != 1:
            raise ValueError(f'引用{value}不存在、歧义或类型/语境不符；请使用正式ID或明确@索引')
        return next(iter(unique.values()))


def bind(data: dict, snapshot: dict, records: list[dict]) -> dict:
    """Resolve only reference-typed fields, preserving the caller's original JSON."""
    result = deepcopy(data)
    resolver = References(result['nodes'], snapshot['nodes'])
    issues = result.setdefault('rejected', [])
    def ref(value, owner, **options):
        try:
            return resolver.resolve(value, **options)
        except ValueError as exc:
            issues.append({'name': owner, 'reference': value, 'reason': str(exc)})
            return ''
    for row in result['nodes']:
        name = row['name']
        row['display_name'] = name
        row['name'] = resolver.aliases[row['reference']]
        context = row.get('context_id', 'canon')
        for field, kind in [('scene', 'scene'), ('event', 'event'), ('big_event', 'big_event'), ('storyline', 'storyline')]:
            row[field] = ref(row.get(field), name, allowed={kind}, context=context, optional=True)
        child_kind = {'storyline':'big_event','big_event':'scene','scene':'event','event':'action'}.get(row['type'])
        row['children'] = [ref(value, name, allowed=({"scene", "event"} if row["type"] == "scene" else {child_kind}) if child_kind else set(), context=context)
                           for value in row.get('children', [])]
        row['participants'] = [ref(value, name, allowed=ENTITY_TYPES) for value in row.get('participants', [])]
        row['participants_not_in_text'] = []
    types = {row['id']: row.get('value_type') for row in records if row.get('kind') == 'property_definition'}
    for row in result.get('properties', []):
        owner = str(row.get('node')) + '.' + str(row.get('property'))
        row['node'] = ref(row.get('node'), owner)
        value_type = row.get('value_type') or 'string'
        types[row['property']] = value_type
        if value_type in {'node', 'either'}:
            row['value'] = ref(row.get('value'), owner, literal=value_type == 'either')
        row['holder'] = ref(row.get('holder'), owner, allowed={'person'},
                            optional=(row.get('scope') or 'world') == 'world')
        row['at_event'] = ref(row.get('at_event'), owner, allowed=MOMENT_TYPES, optional=True)
        row['related_nodes'] = [ref(value, owner) for value in row.get('related_nodes', [])]
    for row in result.get('expirations', []):
        owner = row.get('record_id') or row.get('property') or '失效'
        if not row.get('record_id'):
            row['node'] = ref(row.get('node'), owner)
            if types.get(row.get('property')) in {'node', 'either'}:
                row['value'] = ref(row.get('value'), owner, literal=types[row['property']] == 'either')
        row['at_event'] = ref(row.get('at_event'), owner, allowed=MOMENT_TYPES, optional=True)
        if isinstance(row.get('valid_to'), dict) and row['valid_to'].get('event_id') is not None:
            row['valid_to']['event_id'] = ref(row['valid_to']['event_id'], owner, allowed=MOMENT_TYPES)
    result['reference_types'] = {**{ref: row['type'] for ref, row in resolver.local.items()},
                                 **{ref: row['node_type'] for ref, row in resolver.current.items()}}
    result['property_reference_types'] = types
    return result
