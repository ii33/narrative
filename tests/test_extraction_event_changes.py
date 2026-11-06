"""Extraction and editing consume event changes, never model-authored periods."""
import copy

import pytest

from narrative.operations import Operations
from narrative.tool_api import TOOLS, _relation_map


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    ops = Operations.create_graph_project(tmp_path / 'changes.nproj')
    text = '😀甲和乙在大厅见面。甲与乙成为朋友。后来甲明确结束友谊。'
    sid = ops.import_source('变化', text)
    nodes = [
        {'type': 'person', 'name': name, 'quotes': [text]} for name in ['甲', '乙']
    ] + [
        {'type': 'scene', 'name': '大厅会面', 'quotes': [text]},
        {'type': 'event', 'name': '结交', 'scene': '大厅会面', 'participants': ['甲', '乙'], 'quotes': ['甲与乙成为朋友。']},
        {'type': 'event', 'name': '绝交', 'scene': '大厅会面', 'participants': ['甲', '乙'], 'quotes': ['后来甲明确结束友谊。']},
    ]
    draft = {'nodes': nodes, 'properties': [
        {'node': '甲', 'property': '朋友', 'at_event': '结交', 'op': 'set', 'value': '乙',
         'value_type': 'node', 'quotes': ['甲与乙成为朋友。']},
        {'node': '甲', 'property': '朋友', 'at_event': '绝交', 'op': 'remove', 'value': '乙',
         'value_type': 'node', 'quotes': ['后来甲明确结束友谊。']},
    ]}
    return ops, sid, text, draft


def test_extraction_sequences_quotes_identity_and_current_relationship(project):
    ops, sid, text, draft = project
    result = ops.submit_extraction(sid, draft)
    assert result['status'] == 'accepted', result
    changes = [r for r in ops.property_records() if r['kind'] == 'property_change']
    assert len(changes) == 2
    assert {r['sequence'] for r in changes} == {text.index('甲与乙成为朋友。'), text.index('后来甲明确结束友谊。')}
    assert not any({'valid_from', 'valid_to'} & r.keys() for r in changes)
    ids = {n['name']: n['id'] for n in ops.graph_snapshot()['nodes']}
    assert ops.property_state(ids['甲'], '朋友')['state'] == {'kind': 'absent'}
    assert not _relation_map(ops, {})['edges']
    assert len(_relation_map(ops, {'cut': ids['结交']})['edges']) == 1
    again = copy.deepcopy(draft)
    for node in again['nodes']:
        node['existing_id'] = ids[node['name']]
    before = {r['id'] for r in changes}
    assert ops.submit_extraction(sid, again)['status'] == 'unchanged'
    assert {r['id'] for r in ops.property_records() if r['kind'] == 'property_change'} == before


@pytest.mark.parametrize('mutation', [
    lambda d: d['properties'][0].update(sequence=1),
    lambda d: d['properties'][0].update(valid_to={'start': 1}),
    lambda d: d.update(expirations=[]),
    lambda d: d['properties'][0].pop('at_event'),
    lambda d: d['properties'][0].update(op='clear'),
])
def test_old_or_invalid_draft_rejected_without_partial_write(project, mutation):
    ops, sid, _, draft = project
    mutation(draft)
    before = ops.graph_snapshot(), ops.property_records()
    staged = ops.submit_extraction(sid, draft)
    assert staged['status'] == 'staged' and staged['problems']
    assert (ops.graph_snapshot(), ops.property_records()) == before


def test_manual_false_clear_correct_and_stale_atomicity(project):
    ops, sid, _, draft = project
    assert ops.submit_extraction(sid, draft)['status'] == 'accepted'
    ids = {n['name']: n['id'] for n in ops.graph_snapshot()['nodes']}
    rev = ops.graph_project_info()['revision']
    ops.record_property_change(ids['甲'], '已知', ids['结交'], 'set', value=False,
        value_type='boolean', expected_revision=rev, reason='显式事实')
    record = next(r for r in ops.property_records() if r.get('property_id') == '已知')
    assert record['value'] is False
    ops.correct_property(record['id'], {'value': True}, '纠正错误', expected_revision=ops.graph_project_info()['revision'])
    corrected = next(r for r in ops.property_records() if r.get('id') == record['id'])
    assert corrected['sequence'] == record['sequence'] and corrected['value'] is True
    before = ops.property_records()
    rejected = ops.record_property_change(ids['甲'], '已知', ids['绝交'], 'clear', expected_revision=rev, reason='过期修订')
    assert rejected['status'] == 'rejected'
    assert ops.property_records() == before
    ops.record_property_change(ids['甲'], '已知', ids['绝交'], 'clear', expected_revision=ops.graph_project_info()['revision'], reason='明确清空')
    assert ops.property_state(ids['甲'], '已知')['state'] == {'kind': 'absent'}
    assert 'expire_properties' not in TOOLS and 'add_property' not in TOOLS
    assert 'record_property_change' in TOOLS


def test_collection_relationship_projection_retains_original_change_evidence(project):
    ops, sid, _, draft = project
    draft['properties'] = [draft['properties'][0] | {'cardinality': 'many', 'value': ['乙']}]
    assert ops.submit_extraction(sid, draft)['status'] == 'accepted'
    stored = next(r for r in ops.property_records() if r['kind'] == 'property_change')
    edge = _relation_map(ops, {})['edges'][0]
    assert edge['records'][0]['value'] == stored['value']
    assert edge['records'][0]['evidence'] == stored['evidence']
    assert edge['record_id'] == stored['id']
    assert edge['records'][0]['event_id'] == stored['event_id']


def test_false_zero_unknown_and_exact_schema_contract(project):
    from narrative.extraction_draft import RESULT_SCHEMA
    ops, sid, _, draft = project
    base = draft['properties'][0]
    draft['properties'] = [base | {'property': '计数', 'value_type': 'number', 'value': 0},
                           base | {'property': '开启', 'value_type': 'boolean', 'value': False}]
    assert ops.submit_extraction(sid, draft)['status'] == 'accepted'
    names = {n['name']: n['id'] for n in ops.graph_snapshot()['nodes']}
    assert ops.property_state(names['甲'], '计数')['state']['value'] == 0
    assert ops.property_state(names['甲'], '开启')['state']['value'] is False
    unknown = {'nodes': [], 'properties': [{
        'node': names['甲'], 'property': '计数', 'at_event': names['绝交'], 'op': 'unknown',
        'quotes': ['后来甲明确结束友谊。']}]}
    assert ops.submit_extraction(sid, unknown)['status'] == 'accepted'
    assert ops.property_state(names['甲'], '计数')['state'] == {'kind': 'unknown'}
    fields = RESULT_SCHEMA['properties']['properties']['items']['properties']
    assert 'sequence' not in fields and 'valid_to' not in fields
    assert set(fields['op']['enum']) == {'set', 'add', 'remove', 'clear', 'unknown'}
    assert 'expirations' not in RESULT_SCHEMA['properties']


def test_node_editor_anchor_steps_include_actions_in_source_order(project):
    ops, sid, _, draft = project
    draft['nodes'].append({'type': 'action', 'name': '确认友谊', 'event': '@4',
                           'quotes': ['成为朋友。']})
    assert ops.submit_extraction(sid, draft)['status'] == 'accepted'
    names = {n['name']: n['id'] for n in ops.graph_snapshot()['nodes']}
    editor = ops.node_editor(names['甲'])
    assert [r['id'] for r in editor['anchor_steps']] == [names['结交'], names['确认友谊'], names['绝交']]
    assert all(r['kind'] == 'event' for r in editor['events'])
