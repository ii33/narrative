"""Prior event-change anchors support state without importing unselected plot."""
from __future__ import annotations

import copy
import json

import pytest

from narrative.adaptation_views import _View
from narrative.screenplay_spans import _input, generation_state_visible
from narrative.state_anchors import selection_anchors


def snapshot():
    source = {'nodes': [
        {'id': 'E0', 'node_type': 'event', 'name': '绝不能导入的此前剧情', 'sort_id': 10},
        {'id': 'E1', 'node_type': 'event', 'name': '唯一选中剧情', 'sort_id': 20, 'participants': ['P']},
        {'id': 'E2', 'node_type': 'event', 'name': '绝不能导入的后续剧情', 'sort_id': 30},
        {'id': 'P', 'node_type': 'person', 'name': '甲', 'sort_id': 50, 'point_id': 'E0'}]}
    return {'timeline_id': 'local-test', 'nodes': [copy.deepcopy(source['nodes'][1]), copy.deepcopy(source['nodes'][3])],
        'state_anchors': selection_anchors(source, {'E1'}, {'E0', 'E2'}),
        'properties': [
            {'kind': 'property_definition', 'id': 'clothes', 'value_type': 'string', 'cardinality': 'one'},
            {'kind': 'property_change', 'id': 'current', 'node_id': 'P', 'property_id': 'clothes',
             'value': '青衣', 'event_id': 'E0', 'op': 'set', 'sequence': 1},
            {'kind': 'property_change', 'id': 'past', 'node_id': 'P', 'property_id': 'clothes',
             'value': '过期衣服', 'event_id': 'E0', 'op': 'set', 'sequence': 0},
            {'kind': 'property_change', 'id': 'future', 'node_id': 'P', 'property_id': 'clothes',
             'value': '未来衣服', 'event_id': 'E2', 'op': 'set', 'sequence': 0}]}


def test_metadata_bounds_support_latest_and_explicit_cut_without_visible_plot():
    state = snapshot()
    view = _View(state)
    assert set(view.anchors) == {'E0', 'E2'}
    assert view.rank('E0') < view.rank('E1') < view.rank('E2')
    assert view.active('E0') and not view.visible('E0')
    assert view.visible('P', view.rank('E1'))
    assert [row['id'] for row in view.steps('event')] == ['E1']
    assert [row['id'] for row in view.timeline()] == ['E1']
    with pytest.raises(ValueError, match='节点不存在'):
        view.node('E0')
    with pytest.raises(ValueError, match='节点不存在'):
        view.cut('E2')
    current, past, future = view.records
    assert view.record_visible(current) and view.record_visible(current, view.rank('E1'))
    assert not view.record_visible(past) and not view.record_visible(future)
    assert view.record_key(current) == (view.rank('E0'),1)
    assert view.record_visible(current, view.rank('E0'))
    assert not view.record_visible(current, view.rank('E2'))


def test_generation_state_uses_metadata_but_never_returns_its_narrative():
    state = snapshot()
    current, past, future = state['properties'][1:]
    assert generation_state_visible(state, current, 'E1')
    assert not generation_state_visible(state, past, 'E1')
    assert not generation_state_visible(state, future, 'E1')
    projected = _input(state, ['E1'], ['E1'])
    text = json.dumps(projected, ensure_ascii=False)
    assert '唯一选中剧情' in text and '青衣' in text
    assert '绝不能导入' not in text and '过期衣服' not in text and '未来衣服' not in text
    assert 'state_anchors' not in text
    assert [row['id'] for row in projected['nodes']] == ['E1']
    assert [row['id'] for row in projected['entities']] == ['P']


def test_metadata_ranks_follow_local_moves_and_upgrade_to_real_imported_point():
    state = snapshot()
    state['nodes'][0]['sort_id'] = 100
    view = _View(state)
    assert view.rank('E0') < 100 < view.rank('E2')
    assert view.record_visible(view.records[0])
    assert generation_state_visible(state, view.records[0], 'E1')
    state['nodes'].append({'id': 'E2', 'node_type': 'event', 'name': '现在显式导入后续', 'sort_id': 150})
    upgraded = _View(state)
    assert 'E2' not in upgraded.anchors
    assert upgraded.rank('E2') == 150
    assert not upgraded.record_visible(upgraded.records[0])
    assert upgraded.record_visible(upgraded.records[0], upgraded.rank('E1'))
    assert generation_state_visible(state, upgraded.records[0], 'E1')
    assert not generation_state_visible(state, upgraded.records[0], 'E2')


def test_local_collection_projection_is_set_and_keeps_original_change_provenance():
    from narrative.adaptation_state import current_rows

    state=snapshot()
    state['properties']=[
        {'kind':'property_definition','id':'stock','value_type':'string','cardinality':'many'},
        {'kind':'property_change','id':'stock-set','node_id':'P','property_id':'stock','event_id':'E0','sequence':0,'op':'set','value':['纸','笔']},
        {'kind':'property_change','id':'stock-remove','node_id':'P','property_id':'stock','event_id':'E1','sequence':0,'op':'remove','value':['纸']},
        {'kind':'property_definition','id':'持有者','value_type':'either','cardinality':'one','semantic':'item_holder'},
        {'kind':'property_change','id':'holder-unknown','node_id':'P','property_id':'持有者','event_id':'E1','sequence':1,'op':'unknown'},
    ]
    before=copy.deepcopy(state)
    rows=current_rows(state)
    stock=next(row for row in rows if row['property_id']=='stock')
    assert stock['op']=='set' and stock['derived'] and stock['value']==['笔']
    assert [change['op'] for change in stock['provenance']]==['set','remove']
    unknown=next(row for row in rows if row['property_id']=='持有者')
    assert unknown['state_kind']=='unknown' and unknown['active_change_ids']==['holder-unknown']
    assert 'value' not in unknown
    assert state==before
