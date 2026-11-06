"""Shared readers consume reduced current facts, never old values or intervals."""
import copy

from test_event_property_state import QUOTES, TEXT, change, commit, definition
from test_event_property_state import facts as facts

from narrative.browse_view import related_nodes
from narrative.graph_models import Node
from narrative.node_involvement import involvement


def node(sid, identifier, kind, name):
    quote = QUOTES[0]
    start = TEXT.index(quote)
    return Node.model_validate({'kind': 'node', 'id': identifier, 'node_type': kind, 'name': name, 'context_id': 'canon',
        'disclosure': {'source_id': sid, 'start': start}, 'evidence': {'source_id': sid, 'quote': quote},
        'evidence_spans': [{'source_id': sid, 'start': start, 'end': start+len(quote), 'quote': quote}]}).model_dump()


def test_check_project_uses_change_contract_without_interval_apis(facts):
    ops, sid = facts
    assert commit(ops, [definition(), change(sid, 0, 'set', '旧称呼'), change(sid, 1, 'set', '新称呼')])['status'] == 'accepted'
    checked = ops.check_project()
    assert checked['counts']['error'] == 0, checked['issues']
    assert checked['checked_records'] == len(ops.property_records())


def test_item_holder_transfer_unknown_and_clear_rejection_are_atomic(facts):
    ops, sid = facts
    holder = definition('持有者', value_type='either', semantic='item_holder')
    establish = change(sid, 0, 'set', 'P1', '持有者', node_id='I1')
    result = ops.apply_changes([node(sid, 'I1', 'item', '旅行袋')], [holder, establish], ops.graph_project_info()['revision'], '物品明确持有')
    assert result['status'] == 'accepted', result
    assert commit(ops, [change(sid, 1, 'set', 'P2', '持有者', node_id='I1')])['status'] == 'accepted'
    assert ops.property_state('I1', '持有者', 'E9')['state']['value'] == 'P1'
    assert ops.property_state('I1', '持有者')['state']['value'] == 'P2'
    assert commit(ops, [change(sid, 2, 'unknown', property_id='持有者', node_id='I1')])['status'] == 'accepted'
    assert ops.property_state('I1', '持有者')['state'] == {'kind': 'unknown'}
    assert ops.check_project()['counts']['error'] == 0
    before = copy.deepcopy((ops.graph_snapshot(), ops.property_records()))
    result = commit(ops, [change(sid, 3, 'clear', property_id='持有者', node_id='I1')])
    assert result['status'] == 'staged' and any(d['code'] == 'ITEM_HOLDER_REQUIRED' for d in result['diagnostics'])
    assert (ops.graph_snapshot(), ops.property_records()) == before


def test_location_collection_remove_is_obeyed_by_browse_and_involvement(facts):
    ops, sid = facts
    rows = [definition('地点', value_type='node', cardinality='many'),
            change(sid, 0, 'set', ['L1', 'L2'], '地点', node_id='E9'),
            change(sid, 1, 'remove', ['L1'], '地点', node_id='E9')]
    result = ops.apply_changes([node(sid, 'L1', 'location', '前厅'), node(sid, 'L2', 'location', '后厅')], rows, ops.graph_project_info()['revision'], '地点集合变更')
    assert result['status'] == 'accepted', result
    snapshot, records = ops.graph_snapshot(), ops.property_records()
    involved = involvement(snapshot, records, 'S1')
    assert {r['id'] for r in involved['locations']} == {'L2'}
    related = related_nodes(snapshot, 'S1', records)
    assert 'L2' in related and 'L1' not in related
    browsed = ops.browse_view(granularity='scene', cut_id='S1', node_type='location', related_only=True)
    assert {r['id'] for r in browsed['nodes']} == {'L2'}
    assert ops.check_project()['counts']['error'] == 0


def test_invalid_holder_definition_returns_diagnostic_without_mutation(facts):
    ops, sid = facts
    holder = definition('持有者', value_type='either', semantic='item_holder')
    rows = [holder, change(sid, 0, 'set', 'P1', '持有者', node_id='I1')]
    result = ops.apply_changes([node(sid, 'I1', 'item', '旅行袋')], rows, ops.graph_project_info()['revision'], '明确持有')
    assert result['status'] == 'accepted'
    before = copy.deepcopy((ops.graph_snapshot(), ops.property_records()))
    result = commit(ops, [holder | {'value_type': ['node']}])
    assert result['status'] == 'staged' and result['diagnostics']
    assert (ops.graph_snapshot(), ops.property_records()) == before
