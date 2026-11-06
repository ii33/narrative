"""Multiple explicit relation targets survive actual extraction and tool writes."""
import copy

import pytest

from narrative.operations import Operations
from narrative.project_package import layout
from narrative.tool_api import ToolError, call


@pytest.fixture
def relation_project(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    ops = Operations.create_graph_project(tmp_path / 'relations.nproj')
    text = '林岚、许禾和周宁在资料室见面。林岚与许禾是朋友，林岚与周宁也是朋友。'
    source = ops.import_source('朋友并存', text)
    draft = {'nodes': [
        *[{'type': 'person', 'name': name, 'quotes': [text]} for name in ['林岚', '许禾', '周宁']],
        {'type': 'scene', 'name': '见面', 'quotes': [text]},
        {'type': 'event', 'name': '三人见面', 'scene': '见面', 'participants': ['林岚', '许禾', '周宁'], 'quotes': [text]}],
        'properties': [{'node': '林岚', 'property': '朋友', 'value': other, 'value_type': 'node', 'cardinality': 'many', 'at_event': '三人见面', 'op': 'set', 'quotes': [text]} for other in ['许禾', '周宁']]}
    return ops, source, draft


def test_extraction_explicit_many_keeps_two_friends_and_omission_keeps_definition(relation_project):
    ops, source, draft = relation_project
    draft['properties'] = [draft['properties'][0] | {'value': ['许禾', '周宁']}]
    result = ops.submit_extraction(source, draft)
    assert result['status'] == 'accepted', result.get('diagnostics')
    names = {n['name']: n['id'] for n in ops.graph_snapshot()['nodes']}
    definition = next(r for r in ops.property_records() if r.get('kind') == 'property_definition' and r['id'] == '朋友')
    assert definition['cardinality'] == 'many'
    active = [r for r in ops.property_records() if r.get('kind') == 'property_change' and r['node_id'] == names['林岚'] and r['property_id'] == '朋友']
    assert len(active) == 1 and set(active[0]['value']) == {names['许禾'], names['周宁']}
    inherited = {'nodes': [], 'properties': [{'node': names['周宁'], 'property': '朋友', 'value': [names['林岚']],
        'at_event': names['三人见面'], 'op': 'set', 'quotes': draft['properties'][0]['quotes']}]}
    assert ops.submit_extraction(source, inherited)['status'] == 'accepted'
    params = {'node_id': names['许禾'], 'property_id': '朋友', 'value': [names['林岚']], 'event_id': names['三人见面']}
    assert call('record_property_change', params | {'op': 'set', 'expected_revision': ops.graph_project_info()['revision'], 'reason': '补录事实'}, project=layout(ops.project_info().path).entry)
    assert next(r for r in ops.property_records() if r.get('kind') == 'property_definition' and r['id'] == '朋友')['cardinality'] == 'many'
    before = ops.property_records()
    with pytest.raises(ToolError, match='cardinality'):
        call('record_property_change', params | {'op': 'set', 'expected_revision': ops.graph_project_info()['revision'], 'reason': '补录事实'} | {'cardinality': 'one'}, project=layout(ops.project_info().path).entry)
    assert ops.property_records() == before


@pytest.mark.parametrize('cardinalities', [('many', 'one'), ('invalid', 'many')])
def test_conflicting_or_invalid_cardinality_rejects_atomically(relation_project, cardinalities):
    ops, source, draft = relation_project
    for row, cardinality in zip(draft['properties'], cardinalities, strict=True):
        row['cardinality'] = cardinality
    before = ops.graph_snapshot(), ops.property_records()
    result = ops.submit_extraction(source, draft)
    assert result['status'] == 'staged' and not result['valid']
    assert (ops.graph_snapshot(), ops.property_records()) == before


def test_existing_one_is_not_implicitly_changed_by_extraction(relation_project):
    ops, source, draft = relation_project
    first = copy.deepcopy(draft)
    first['properties'] = first['properties'][:1]
    del first['properties'][0]['cardinality']
    assert ops.submit_extraction(source, first)['status'] == 'accepted'
    names = {n['name']: n['id'] for n in ops.graph_snapshot()['nodes']}
    next_draft = {'nodes': [], 'properties': [draft['properties'][1] | {'node': names['林岚'], 'value': names['周宁'], 'at_event': names['三人见面']} ]}
    before = ops.graph_snapshot(), ops.property_records()
    result = ops.submit_extraction(source, next_draft)
    assert result['status'] == 'staged' and not result['valid']
    assert (ops.graph_snapshot(), ops.property_records()) == before
    assert next(r for r in ops.property_records() if r.get('kind') == 'property_definition' and r['id'] == '朋友')['cardinality'] == 'one'


def test_scalar_person_relationships_keep_per_counterpart_sequences(relation_project):
    ops, source, draft = relation_project
    for row in draft['properties']:
        row.pop('cardinality')
    result = ops.submit_extraction(source, draft)
    assert result['status'] == 'accepted', result.get('diagnostics')
    values = [r for r in ops.property_records() if r.get('kind') == 'property_change' and r['property_id'] == '朋友']
    assert len(values) == 2


def test_tool_creates_many_and_core_rejects_invalid_collection_atomically(relation_project):
    ops, source, draft = relation_project
    draft['properties'] = []
    assert ops.submit_extraction(source, draft)['status'] == 'accepted'
    names = {n['name']: n['id'] for n in ops.graph_snapshot()['nodes']}
    params = {'node_id': names['林岚'], 'property_id': '同行者', 'value': [names['许禾'], names['周宁']], 'value_type': 'node', 'cardinality': 'many', 'event_id': names['三人见面']}
    entry = layout(ops.project_info().path).entry
    call('record_property_change', params | {'op': 'set', 'expected_revision': ops.graph_project_info()['revision'], 'reason': '补录事实'}, project=entry)
    definition = next(r for r in ops.property_records() if r.get('kind') == 'property_definition' and r['id'] == '同行者')
    assert definition['cardinality'] == 'many'
    for value in ([names['许禾'], names['许禾']], ['missing-person'], names['许禾']):
        before = ops.property_records()
        result = call('record_property_change', params | {'op': 'set', 'expected_revision': ops.graph_project_info()['revision'], 'reason': '补录事实'} | {'value': value}, project=entry)
        assert result['valid'] is False, result
        assert ops.property_records() == before


def test_one_node_definition_rejects_collection(relation_project):
    ops, source, draft = relation_project
    draft['properties'] = [draft['properties'][0] | {'value': ['许禾', '周宁'], 'cardinality': 'one'}]
    before = ops.graph_snapshot(), ops.property_records()
    result = ops.submit_extraction(source, draft)
    assert result['status'] == 'staged'
    assert (ops.graph_snapshot(), ops.property_records()) == before
