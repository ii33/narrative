"""Typed extraction references retain raw slot identity and existing-node semantics."""
from __future__ import annotations

import copy

import pytest
from agent_fixtures import project as project

from narrative.extraction import parse_nodes

TEXT = '甲先走。甲再停。'


def draft():
    return {'nodes': [
        {'type': 'person', 'name': '甲', 'quotes': ['甲先走。']},
        {'type': 'scene', 'name': '行走停步', 'quotes': [TEXT]},
        {'type': 'event', 'name': '行走', 'scene': '@2', 'participants': ['@1'], 'quotes': ['甲先走。']},
        {'type': 'event', 'name': '停步', 'scene': '@2', 'participants': ['@1'], 'quotes': ['甲再停。']},
    ], 'properties': []}


def test_original_indices_survive_sorting_and_rejected_nodes():
    payload = {'nodes': [{'type': 'person', 'name': '坏行', 'quotes': ['不存在']},
                         {'type': 'event', 'name': '后发生', 'quotes': ['甲再停。']},
                         {'type': 'scene', 'name': '先披露', 'quotes': ['甲先走。']}], 'properties': []}
    parsed = parse_nodes(payload, TEXT)
    assert [(row['name'], row['reference']) for row in parsed['nodes']] == [('先披露', '@3'), ('后发生', '@2')]


def test_event_owner_event_target_and_existing_definition_type_are_preserved(project):
    sid = project.import_source('事件引用', TEXT)
    payload = draft()
    payload['properties'] = [{'node': '@3', 'property': '对应过程', 'value': '@4', 'value_type': 'node',
                              'at_event': '@3', 'related_nodes': ['@4'], 'quotes': ['甲先走。']}]
    original = copy.deepcopy(payload)
    result = project.submit_extraction(sid, payload)
    assert result['status'] == 'accepted', result
    assert payload == original
    first, second = result['node_ids']['@3'], result['node_ids']['@4']
    row = next(row for row in project.property_records() if row['kind'] == 'property_value')
    assert (row['node_id'], row['value'], row['event_id']) == (first, second, first)
    assert row['related_nodes'] == [second]
    follow = {'nodes': [], 'properties': [{'node': first, 'property': '对应过程', 'value': second,
                                          'at_event': second, 'quotes': ['甲再停。']}]}
    assert project.submit_extraction(sid, follow)['status'] == 'accepted'
    values = [row for row in project.property_records() if row['kind'] == 'property_value']
    assert len(values) == 2 and all(row['value'] == second for row in values)


def test_node_selector_expiry_resolves_local_target_to_formal_id(project):
    sid = project.import_source('指向失效', TEXT)
    payload = draft()
    payload['properties'] = [{'node': '@1', 'property': '对应过程', 'value': '@3', 'value_type': 'node',
                              'at_event': '@3', 'quotes': ['甲先走。']}]
    payload['expirations'] = [{'node': '@1', 'property': '对应过程', 'value': '@3', 'at_event': '@4'}]
    result = project.submit_extraction(sid, payload)
    assert result['status'] == 'accepted', result
    row = next(row for row in project.property_records() if row['kind'] == 'property_value')
    assert row['valid_to']['event_id'] == result['node_ids']['@4']
    assert row['value'] == result['node_ids']['@3']


def test_duplicate_identity_conflicting_participants_is_rejected_without_writes(project):
    sid = project.import_source('重复身份冲突', TEXT)
    payload = draft()
    payload['nodes'].append(payload['nodes'][2] | {'participants': []})
    before = project.graph_snapshot()
    result = project.preview_extraction(sid, payload)
    assert not result['valid']
    assert any('不同身份' in row['message'] for row in result['diagnostics'])
    assert project.graph_snapshot() == before


@pytest.mark.parametrize('field', ['participants', 'related_nodes'])
def test_unknown_explicit_reference_never_silently_disappears(project, field):
    sid = project.import_source('显式错误引用', TEXT)
    payload = draft()
    if field == 'participants':
        payload['nodes'][2][field] = ['@99']
    else:
        payload['properties'] = [{'node': '@1', 'property': '状态', 'value': '行走', 'at_event': '@3',
                                  'related_nodes': ['@99'], 'quotes': ['甲先走。']}]
    result = project.preview_extraction(sid, payload)
    assert not result['valid']
    assert any('@99' in row['message'] for row in result['diagnostics'])
    assert not project.property_records()
