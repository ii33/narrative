"""Explicit property symmetry passes through the real Operations write boundary."""
from __future__ import annotations

import copy

import pytest
from agent_fixtures import project as project

TEXT = '甲与乙互相信任。甲敬佩乙。'


def draft():
    return {'nodes': [
        {'type': 'person', 'name': '甲', 'quotes': [TEXT]},
        {'type': 'person', 'name': '乙', 'quotes': [TEXT]},
        {'type': 'scene', 'name': '交往', 'quotes': [TEXT]},
        {'type': 'event', 'name': '相互信任并敬佩', 'scene': '交往', 'quotes': [TEXT], 'participants': ['甲', '乙']},
    ], 'properties': [
        {'node': '甲', 'property': '任意相互关系', 'value': '乙', 'value_type': 'node',
         'symmetric': True, 'category': '关系', 'quotes': ['甲与乙互相信任。']},
        {'node': '甲', 'property': '敬佩', 'value': '乙', 'value_type': 'node',
         'category': '关系', 'quotes': ['甲敬佩乙。']},
    ]}


def test_draft_symmetric_definition_persists_without_reverse_values(project):
    sid = project.import_source('对称语义', TEXT)
    result = project.submit_extraction(sid, draft())
    assert result['status'] == 'accepted', result
    definitions = {row['id']: row for row in project.property_records() if row['kind'] == 'property_definition'}
    assert definitions['任意相互关系']['symmetric'] is True
    assert definitions['敬佩']['symmetric'] is False
    values = [row for row in project.property_records() if row['kind'] == 'property_value']
    assert len(values) == 2
    names = {node['id']: node['name'] for node in project.graph_snapshot()['nodes']}
    assert all(names[row['node_id']] == '甲' and names[row['value']] == '乙' for row in values)


def test_explicit_definition_change_and_omitted_flag_preserves_current_value(project):
    sid = project.import_source('对称语义', TEXT)
    payload = draft()
    assert project.submit_extraction(sid, payload)['status'] == 'accepted'
    payload['properties'][0]['symmetric'] = False
    assert project.submit_extraction(sid, payload)['status'] == 'accepted'
    assert next(row for row in project.property_records() if row['id'] == '任意相互关系')['symmetric'] is False
    payload['properties'][0]['symmetric'] = True
    assert project.submit_extraction(sid, payload)['status'] == 'accepted'
    payload['properties'][0].pop('symmetric')
    project.submit_extraction(sid, payload)
    assert next(row for row in project.property_records() if row['id'] == '任意相互关系')['symmetric'] is True


@pytest.mark.parametrize('bad', [1, 0, 'true', None, [], {}])
def test_non_boolean_definition_flag_is_rejected_atomically(project, bad):
    before = copy.deepcopy(project.graph_snapshot())
    result = project.preview_changes([], [{'kind': 'property_definition', 'id': '关系定义',
                                           'value_type': 'node', 'cardinality': 'one', 'symmetric': bad}], before['revision'])
    assert not result['valid']
    assert any(row['code'] == 'DEFINITION' for row in result['diagnostics'])
    assert project.graph_snapshot() == before
    assert not project.property_records()


def test_conflicting_or_non_boolean_draft_declarations_are_not_coerced(project):
    sid = project.import_source('对称语义', TEXT)
    payload = draft()
    payload['properties'][0]['symmetric'] = 'true'
    assert not project.preview_extraction(sid, payload)['valid']
    payload = draft()
    payload['properties'].append(payload['properties'][0] | {'symmetric': False})
    assert not project.preview_extraction(sid, payload)['valid']
    assert not project.property_records()
