"""Event facts are authoritative; no stored validity or revision replay exists."""
import copy
import sqlite3

import pytest

from narrative.graph_models import Node
from narrative.graph_store import GraphStore
from narrative.operations import Operations
from narrative.property_core import reduce_changes
from narrative.temporal_state import event_order, projected_changes

TEXT = '😀三人在会面。后来交换称呼。随后结束关系。最后情况未知。'
QUOTES = ['三人在会面。', '后来交换称呼。', '随后结束关系。', '最后情况未知。']
EVENTS = ['E9', 'E1', 'E6', 'E3']


@pytest.fixture
def facts(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    ops = Operations.create_graph_project(tmp_path / 'event-facts.nproj')
    sid = ops.import_source('事件状态', TEXT)
    def obj(identifier, kind, name, quote, **extra):
        start = TEXT.index(quote)
        return Node.model_validate({'kind': 'node', 'id': identifier, 'node_type': kind, 'name': name, 'context_id': 'canon',
            'disclosure': {'source_id': sid, 'start': start}, 'evidence': {'source_id': sid, 'quote': quote},
            'evidence_spans': [{'source_id': sid, 'start': start, 'end': start+len(quote), 'quote': quote}], **extra}).model_dump()
    nodes = [obj(f'P{i}', 'person', name, QUOTES[0]) for i, name in enumerate(['甲', '乙', '丙'], 1)]
    nodes.append(obj('S1', 'scene', '会面后的变化', QUOTES[0], events=EVENTS))
    nodes += [obj(identifier, 'event', quote, quote, participants=['P1', 'P2', 'P3']) for identifier, quote in zip(EVENTS, QUOTES, strict=True)]
    assert ops.apply_changes(nodes, [], ops.graph_project_info()['revision'], '事件事实fixture')['status'] == 'accepted'
    return ops, sid


def definition(name='称呼', **extra):
    return {'kind': 'property_definition', 'id': name, 'value_type': 'string', 'cardinality': 'one', **extra}


def change(sid, index, op, value=None, property_id='称呼', **extra):
    quote = QUOTES[index]
    row = {'kind': 'property_change', 'id': f'change-{index}-{property_id}', 'node_id': 'P1', 'property_id': property_id,
           'event_id': EVENTS[index], 'op': op, 'disclosure': {'source_id': sid, 'start': TEXT.index(quote)},
           'evidence': {'source_id': sid, 'quote': quote}, **extra}
    if op in {'set', 'add', 'remove'}:
        row['value'] = value
    return row


def commit(ops, rows):
    return ops.apply_changes([], rows, ops.graph_project_info()['revision'], '显式事件事实')


def test_event_order_set_clear_unknown_and_no_stored_validity(facts):
    ops, sid = facts
    rows = [change(sid, 3, 'unknown'), change(sid, 1, 'set', '乙称呼'), change(sid, 0, 'set', '甲称呼'), change(sid, 2, 'clear')]
    result = commit(ops, [definition(), *rows])
    assert result['status'] == 'accepted', result
    assert ops.property_state('P1', '称呼', 'E9')['state'] == {'kind': 'known', 'value': '甲称呼'}
    assert ops.property_state('P1', '称呼', 'E1')['state'] == {'kind': 'known', 'value': '乙称呼'}
    assert ops.property_state('P1', '称呼', 'E6')['state'] == {'kind': 'absent'}
    assert ops.property_state('P1', '称呼')['state'] == {'kind': 'unknown'}
    stored = ops.property_records()
    assert all(not {'valid_from', 'valid_to', 'applies_to'} & set(r) for r in stored)
    assert all(r['sequence'] == r['disclosure']['start'] for r in stored if r['kind'] == 'property_change')


def test_relation_counterparts_independent_and_projection_excludes_removed(facts):
    ops, sid = facts
    rows = [change(sid, 0, 'set', 'P2', '朋友', sequence=1),
            change(sid, 0, 'set', 'P3', '朋友', sequence=2, id='friend-c'),
            change(sid, 2, 'remove', 'P2', '朋友')]
    assert commit(ops, [definition('朋友', value_type='node'), *rows])['status'] == 'accepted'
    assert set(ops.property_state('P1', '朋友', 'E9')['state']['value']) == {'P2', 'P3'}
    state = ops.property_state('P1', '朋友')
    assert state['state'] == {'kind': 'known', 'value': ['P3']}
    projection = projected_changes(ops.graph_snapshot(), ops.property_records())
    assert [r['value'] for r in projection if r['property_id'] == '朋友'] == ['P3']
    assert all(r['derived'] for r in projection)


def test_many_collection_fold_and_unknown_baseline(facts):
    ops, sid = facts
    rows = [change(sid, 0, 'set', ['P2'], '同行者'), change(sid, 1, 'add', ['P3'], '同行者'), change(sid, 2, 'remove', ['P2'], '同行者')]
    assert commit(ops, [definition('同行者', cardinality='many', value_type='node'), *rows])['status'] == 'accepted'
    result = ops.property_state('P1', '同行者')
    assert result['state'] == {'kind': 'known', 'value': ['P3']}
    projection = projected_changes(ops.graph_snapshot(), ops.property_records())
    assert projection[0]['value'] == ['P3'] and len(projection[0]['active_change_ids']) == 3
    nodes = {n['id']: n for n in ops.graph_snapshot()['nodes']}
    partial = reduce_changes([dict(rows[1], sequence=1)], definition('同行者', cardinality='many', value_type='node'), nodes, event_order=event_order(ops.graph_snapshot()))
    assert partial['state'] == {'kind': 'unknown'}


def test_same_event_sequence_conflict_and_precise_cut(facts):
    ops, sid = facts
    rows = [change(sid, 0, 'set', 'A', sequence=1), change(sid, 0, 'set', 'B', sequence=2, id='second')]
    assert commit(ops, [definition(), *rows])['status'] == 'accepted'
    assert ops.property_state('P1', '称呼', {'event_id': 'E9', 'sequence': 1})['state']['value'] == 'A'
    assert ops.property_state('P1', '称呼', 'E9')['state']['value'] == 'B'
    before = copy.deepcopy(ops.property_records())
    conflict = change(sid, 0, 'set', 'C', sequence=2, id='conflicting')
    result = commit(ops, [conflict])
    assert result['status'] == 'staged' and any(r['code'] == 'STATE_CONFLICT' for r in result['diagnostics'])
    assert ops.property_records() == before


def test_local_axis_and_scope_are_independent(facts):
    ops, sid = facts
    rows = [change(sid, 0, 'set', '事实'), change(sid, 1, 'set', '认知', id='claim', scope='claim', holder_id='P2')]
    assert commit(ops, [definition(), *rows])['status'] == 'accepted'
    assert ops.property_state('P1', '称呼')['state']['value'] == '事实'
    assert ops.property_state('P1', '称呼', scope='claim', holder_id='P2')['state']['value'] == '认知'
    nodes = {n['id']: n for n in ops.graph_snapshot()['nodes']}
    world = [dict(rows[0], sequence=0), change(sid, 1, 'set', '本地后值', sequence=0)]
    result = reduce_changes(world, definition(), nodes, event_order={'E9': 20, 'E1': 10})
    assert result['state']['value'] == '事实'


@pytest.mark.parametrize('bad', [
    {'kind': 'property_value', 'value': 'A'}, {'valid_to': None}, {'sequence': -1}, {'sequence': True},
    {'event_id': 'missing'}, {'op': 'clear', 'value': 'forbidden'}, {'op': 'set', 'value': 'A', 'disclosure': {'source_id': 'missing', 'start': 0}}])
def test_invalid_or_old_shapes_reject_atomically(facts, bad):
    ops, sid = facts
    before = ops.graph_snapshot(), ops.property_records()
    if 'node_id' in bad or 'event_id' in bad:
        from narrative.content_errors import ContentGuardError
        with pytest.raises(ContentGuardError):
            commit(ops, [definition(), change(sid, 0, 'set', 'A') | bad])
    else:
        result = commit(ops, [definition(), change(sid, 0, 'set', 'A') | bad])
        assert result['status'] == 'staged'
    assert (ops.graph_snapshot(), ops.property_records()) == before


def test_old_database_version_rejected_without_mutation(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=10')
        db.execute('CREATE TABLE untouched(value TEXT)')
        db.execute("INSERT INTO untouched VALUES ('original')")
    before = path.read_bytes()
    with pytest.raises(ValueError, match='v11'):
        GraphStore(path)
    assert path.read_bytes() == before


def test_direct_correction_delete_and_suffix_rollback_recompute_state(facts):
    ops, sid = facts
    assert commit(ops, [definition(), change(sid, 0, 'set', 'A'), change(sid, 1, 'set', 'B'), change(sid, 3, 'set', 'C')])['status'] == 'accepted'
    record = next(r for r in ops.property_records() if r.get('event_id') == 'E1')
    ops.correct_property(record['id'], {'value': '修正B'}, '录入勘误', expected_revision=ops.graph_project_info()['revision'])
    corrected = next(r for r in ops.property_records() if r['id'] == record['id'])
    assert corrected['sequence'] == record['sequence'] and corrected['value'] == '修正B'
    ops.delete_property_records([next(r['id'] for r in ops.property_records() if r.get('event_id') == 'E3')], '删错误事实', expected_revision=ops.graph_project_info()['revision'])
    assert ops.property_state('P1', '称呼')['state']['value'] == '修正B'
    result = ops.rollback_to(sid, TEXT.index(QUOTES[0]), ops.graph_project_info()['revision'], '删除后续披露')
    assert result['valid'] and ops.property_state('P1', '称呼')['state']['value'] == 'A'
    assert not any(r.get('event_id') in {'E1', 'E6', 'E3'} for r in ops.property_records())
    assert 'reopened_record_ids' not in result


def test_node_cards_show_change_history_and_derived_state(facts):
    ops, sid = facts
    assert commit(ops, [definition(), change(sid, 0, 'set', 'A'), change(sid, 1, 'set', 'B')])['status'] == 'accepted'
    card = ops.properties.node_properties('P1')
    item = next(i for i in card['properties'] if i['property_id'] == '称呼')
    assert item['state'] == {'kind': 'known', 'value': 'B'}
    assert len(item['records']) == 2 and [r['is_current'] for r in item['records']] == [False, True]
    assert [r['op'] for r in item['records']] == ['set', 'set']
    assert all('valid_to' not in r for r in item['records'])


@pytest.mark.parametrize('bad', [{'node_id': []}, {'property_id': []}, {'event_id': []}, {'op': {}},
                                  {'context_id': []}, {'scope': []}, {'holder_id': [], 'scope': 'claim'},
                                  {'evidence_spans': [{'source_id': 'missing', 'start': 0, 'end': 1, 'quote': 'x'}]}])
def test_malformed_typed_references_are_diagnostics_not_partial_writes(facts, bad):
    ops, sid = facts
    before = ops.graph_snapshot(), ops.property_records()
    if 'node_id' in bad or 'event_id' in bad:
        from narrative.content_errors import ContentGuardError
        with pytest.raises(ContentGuardError):
            commit(ops, [definition(), change(sid, 0, 'set', 'A') | bad])
    else:
        result = commit(ops, [definition(), change(sid, 0, 'set', 'A') | bad])
        assert result['status'] == 'staged'
    assert (ops.graph_snapshot(), ops.property_records()) == before


def test_same_position_commuting_collection_edits_do_not_require_arbitrary_order(facts):
    ops, sid = facts
    rows = [change(sid, 0, 'set', ['P2'], '同行者'),
            change(sid, 1, 'add', ['P3'], '同行者', sequence=7),
            change(sid, 1, 'remove', ['P2'], '同行者', sequence=7, id='parallel-remove')]
    assert commit(ops, [definition('同行者', cardinality='many', value_type='node'), *rows])['status'] == 'accepted'
    assert ops.property_state('P1', '同行者')['state'] == {'kind': 'known', 'value': ['P3']}
    before = copy.deepcopy(ops.property_records())
    result = commit(ops, [change(sid, 1, 'remove', ['P3'], '同行者', sequence=7, id='incompatible-remove')])
    assert result['status'] == 'staged'
    assert ops.property_records() == before


@pytest.mark.parametrize('record', [{'kind': 'property_value'}, {'kind': 'property_definition', 'value_type': 'string', 'cardinality': 'one'}])
def test_missing_id_on_non_allocated_or_obsolete_record_is_rejected_without_crash(facts, record):
    ops, _sid = facts
    before = ops.graph_snapshot(), ops.property_records()
    result = commit(ops, [record])
    assert result['status'] == 'staged' and result['diagnostics']
    assert (ops.graph_snapshot(), ops.property_records()) == before
