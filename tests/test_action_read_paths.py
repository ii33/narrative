"""Read projections and material intervals share source-ordered action anchors."""
from __future__ import annotations

from pathlib import Path

from agent_fixtures import project as project
from test_granularity_actions import FIRST, SECOND, TEXT, action_batch

from narrative.node_material import MaterialStore


def install(project):
    sid, _, objects = action_batch(project)
    definitions = [{'kind': 'property_definition', 'id': '状态', 'value_type': 'string', 'cardinality': 'one'}]
    records = definitions + [
        {'kind': 'property_change', 'op': 'set', 'sequence': TEXT.index(quote), 'id': f'state{i}', 'node_id': 'P1', 'property_id': '状态',
         'value': state, 'event_id': identifier, 'disclosure': {'source_id': sid, 'start': TEXT.index(quote)},
         'evidence': {'source_id': sid, 'quote': quote}}
        for i, (identifier, quote, state) in enumerate([('A99', FIRST, '进入'), ('A1', SECOND, '停止')])]
    assert project.apply_changes(objects, records, project.graph_project_info()['revision'], reason='动作阅读回归')['status'] == 'accepted'


def test_summary_event_api_and_action_order_do_not_compare_type_number_sequences(project):
    install(project)
    assert project.property_event_state('P1', '状态', 'A99')['state']['value'] == '进入'
    assert project.property_event_state('P1', '状态', 'A1')['state']['value'] == '停止'
    assert project.property_node_summary('P1', cut='A99')['properties'][0]['value'] == '进入'
    assert project.property_node_summary('P1', cut='A1')['properties'][0]['value'] == '停止'
    assert project.graph_event_compare('A99', 'A1')['relation'] == 'before'
    view = project.browse_view(granularity='action', cut_id='A99')
    assert [row['id'] for row in view['steps']] == ['A99', 'A1']
    assert view['cut']['start'] == TEXT.index(FIRST) + len(FIRST) - 1
    assert [row['key'] for row in view['moments']['levels']] == ['storyline', 'big_event', 'scene', 'event', 'action']
    assert {'E9', 'A99', 'A1'} == {row['id'] for row in view['anchor_steps']}
    assert next(row for row in view['moments']['nodes'] if row['id'] == 'A99')['parent_id'] == 'E9'
    assert {row['id']: row['depth'] for row in view['timeline']} == {'S1': 0, 'E9': 1, 'A99': 2, 'A1': 2}


def test_material_interval_uses_actual_action_positions_and_reports_missing_coordinates(project):
    install(project)
    snapshot = project.graph_snapshot()
    store = MaterialStore(Path(project.project_info().path))
    card = store.add_card('P1', '进入时资料', valid_from='A99', valid_to='A1', snapshot=snapshot)
    assert [row['id'] for row in store.view('P1', cut='A99', snapshot=snapshot)['cards']] == [card['id']]
    assert not store.view('P1', cut='A1', snapshot=snapshot)['cards']
    assert not store.view('P1', cut='E9', snapshot=snapshot)['cards']
    before = store.path_for('P1').read_bytes()
    next(row for row in snapshot['nodes'] if row['id'] == 'A1').pop('disclosure')
    result = store.view('P1', cut='A99', snapshot=snapshot)
    assert result['diagnostics'][0]['code'] == 'MATERIAL_POSITION'
    assert result['unpositioned_cards'][0]['id'] == card['id']
    assert store.path_for('P1').read_bytes() == before
