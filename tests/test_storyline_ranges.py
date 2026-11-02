"""Reading partitions are derived from containment and source coordinates."""
from copy import deepcopy

from agent_fixtures import project as project

from narrative.browse_view import moment_tree
from narrative.graph_event_order import event_lines
from narrative.order_ids import prefix_for
from narrative.storyline_ranges import analyze


def graph():
    def node(identifier, kind, children=(), start=None):
        return {'kind': 'node', 'id': identifier, 'name': identifier, 'node_type': kind,
                'context_id': 'canon', 'events': list(children),
                'disclosure': None if start is None else {'source_id': 'SRC1', 'start': start}}
    # Deliberately reverse IDs and insertion order. Container disclosures are absent.
    nodes = [node('T1', 'storyline', ['B1']), node('B1', 'big_event', ['S1']),
             node('S1', 'scene', ['E1']), node('E1', 'event', start=30),
             node('T99', 'storyline', ['B99', 'B50']), node('B50', 'big_event', ['S50']),
             node('S50', 'scene', ['E50']), node('E50', 'event', start=20),
             node('B99', 'big_event', ['S99']), node('S99', 'scene', ['E99']),
             node('E99', 'event', start=10)]
    nodes[0]['completion'] = {'source_id': 'SRC1', 'start': 40}
    nodes[4]['completion'] = {'source_id': 'SRC1', 'start': 30}
    order = {'kind': 'source_order', 'id': 'SO1', 'source_id': 'SRC1', 'context_id': 'canon', 'ordinal': 0}
    return {'revision': 1, 'nodes': nodes, 'objects': nodes + [order, {'kind': 'context', 'id': 'canon'}], 'source_orders': [order]}


def test_two_levels_are_adjacent_and_open_tail_is_not_source_eof():
    snapshot = graph()
    before = deepcopy(snapshot)
    spans, issues = analyze(snapshot)
    assert not issues
    rows = {row['id']: row for row in spans}
    assert rows['T99']['end'] == rows['T1']['start'] == {'source_id': 'SRC1', 'start': 30}
    assert rows['B99']['end'] == rows['B50']['start'] == {'source_id': 'SRC1', 'start': 20}
    assert rows['B50']['end'] == rows['T1']['start']
    assert rows['T1']['end'] == rows['B1']['end'] == {'source_id': 'SRC1', 'start': 40}
    assert [row['id'] for row in spans if row['node_type'] == 'storyline'] == ['T99', 'T1']
    assert snapshot == before
    assert prefix_for('big_event') == 'B'


def test_cut_preserves_future_boundary_without_future_name_or_members():
    snapshot = graph()
    tree = moment_tree(snapshot, 'E99')
    rows = {row['id']: row for row in tree['nodes']}
    assert 'T99' not in rows
    assert rows['B99']['end'] == {'source_id': 'SRC1', 'start': 20}
    assert not {'T1', 'B1', 'S1', 'E1', 'B50', 'S50', 'E50'} & rows.keys()
    assert all('provisional' not in row and 'final_name' not in row for row in rows.values())
    assert [row['id'] for row in moment_tree(snapshot)['nodes'] if row['level'] == 'storyline'] == ['T99', 'T1']
    lines = event_lines(snapshot, disclosure_cut={'source_id': 'SRC1', 'start': 10})
    assert lines['storylines'] == []
    assert lines['big_events'][0]['event_count'] == 1


def test_nested_scenes_inherit_owner_and_late_disclosure_does_not_hide_child():
    snapshot = graph()
    scene = next(row for row in snapshot['nodes'] if row['id'] == 'S99')
    scene['events'] = ['S98']
    scene['disclosure'] = {'source_id': 'SRC1', 'start': 15}
    nested = {'kind': 'node', 'id': 'S98', 'node_type': 'scene', 'context_id': 'canon', 'events': ['E99']}
    snapshot['nodes'].append(nested)
    snapshot['objects'].append(nested)
    spans, issues = analyze(snapshot)
    assert not issues
    assert next(row for row in spans if row['id'] == 'B99')['start']['start'] == 10


def test_closed_gap_is_error_open_gap_warning():
    snapshot = graph()
    snapshot['nodes'] = [node for node in snapshot['nodes'] if node['id'] != 'T1']
    snapshot['objects'] = [node for node in snapshot['objects'] if node['id'] != 'T1']
    for identifier, start in [('Sgap', 15), ('Sopen', 45)]:
        node = {'kind': 'node', 'id': identifier, 'node_type': 'scene', 'context_id': 'canon',
                'disclosure': {'source_id': 'SRC1', 'start': start}, 'events': []}
        snapshot['nodes'].append(node)
        snapshot['objects'].append(node)
    issues = analyze(snapshot)[1]
    assert any(row['object_id'] == 'Sgap' and row['severity'] == 'error' for row in issues)
    assert any(row['object_id'] == 'Sopen' and row['severity'] == 'warning' for row in issues)


def test_shared_and_interleaved_members_are_static_errors():
    snapshot = graph()
    next(row for row in snapshot['nodes'] if row['id'] == 'S99')['events'].append('E1')
    issues = analyze(snapshot)[1]
    assert any(row['code'] == 'STORYLINE_OVERLAP' for row in issues)
    assert any(row['code'] == 'STORYLINE_MEMBER_OWNER' for row in issues)


def test_skipped_major_story_level_is_rejected():
    snapshot = graph()
    next(row for row in snapshot['nodes'] if row['id'] == 'T99')['events'] = ['S99']
    issues = analyze(snapshot)[1]
    assert any(row['code'] == 'STORYLINE_CHILD_TYPE' for row in issues)


def test_unfinished_big_events_need_no_major_story_and_global_boundary_remains():
    snapshot = graph()
    snapshot['nodes'] = [node for node in snapshot['nodes'] if node['node_type'] != 'storyline']
    snapshot['objects'] = [node for node in snapshot['objects'] if node.get('node_type') != 'storyline']
    spans, issues = analyze(snapshot)
    assert not issues
    assert [row['id'] for row in spans] == ['B99', 'B50', 'B1']
    assert spans[0]['end'] == spans[1]['start']
    assert spans[1]['end'] == spans[2]['start']
    assert spans[-1]['end'] is None


def test_completed_story_requires_explicit_valid_end_and_does_not_reveal_summary_early():
    snapshot = graph()
    snapshot['nodes'][4]['disclosure'] = {'source_id': 'SRC1', 'start': 29}
    assert not analyze(snapshot)[1]
    snapshot['nodes'][4]['completion']['start'] = 20
    assert any(row['code'] == 'STORYLINE_COMPLETION' for row in analyze(snapshot)[1])
    snapshot['nodes'][4]['completion'] = None
    assert any(row['code'] == 'STORYLINE_COMPLETION' for row in analyze(snapshot)[1])


def test_progress_has_distinct_direct_child_semantics_and_cut():
    from narrative.property_store import PropertyStore
    snapshot = graph()
    records = [
        {'id': 'PV1', 'kind': 'property_value', 'node_id': 'S99',
         'property_id': 'big_event_progress', 'value': '第一阶段推进', 'event_id': 'E99',
         'disclosure': {'source_id': 'SRC1', 'start': 10}},
        {'id': 'PV2', 'kind': 'property_value', 'node_id': 'B50',
         'property_id': 'storyline_progress', 'value': '整部故事推进', 'event_id': 'E50',
         'disclosure': {'source_id': 'SRC1', 'start': 20}},
        {'id': 'PV3', 'kind': 'property_value', 'node_id': 'S1',
         'property_id': 'big_event_progress', 'value': '未来阶段', 'event_id': 'E1',
         'disclosure': {'source_id': 'SRC1', 'start': 30}},
    ]
    store = object.__new__(PropertyStore)
    assert store._container_progress('B99', snapshot, records, None)[0]['value'] == '第一阶段推进'
    assert store._container_progress('T99', snapshot, records, None)[0]['value'] == '整部故事推进'
    assert store._container_progress('T99', snapshot, records, (0, 10)) == []
    rows = {row['id']: row for row in moment_tree(snapshot, records=records)['nodes']}
    assert rows['T99']['summary'] == '整部故事推进'
    assert rows['B99']['summary'] == '第一阶段推进'
    cut = {row['id']: row for row in moment_tree(snapshot, 'E99', records)['nodes']}
    assert 'T99' not in cut and cut['B99']['summary'] == '第一阶段推进'


def test_major_story_completion_can_leave_gap_before_next_summary():
    snapshot = graph()
    next(node for node in snapshot['nodes'] if node['id'] == 'T99')['completion']['start'] = 25
    spans, issues = analyze(snapshot)
    assert not issues
    rows = {row['id']: row for row in spans}
    assert rows['T99']['end']['start'] == 25
    assert rows['B50']['end']['start'] == 30


def test_operations_rejects_closed_interval_gap_atomically(project):
    from narrative.graph_models import Node
    text = '甲入城。甲办事。甲离城。'
    sid = project.import_source('分段原文', text)
    def obj(identifier, kind, quote, children=()):
        return Node.model_validate({'kind': 'node', 'id': identifier, 'node_type': kind,
            'name': identifier, 'context_id': 'canon', 'events': list(children),
            'evidence': {'source_id': sid, 'quote': quote},
            'disclosure': {'source_id': sid, 'start': text.index(quote)}}).model_dump()
    objects = []
    for identifier, quote in [('99', '甲入城。'), ('50', '甲办事。'), ('1', '甲离城。')]:
        objects += [obj('S' + identifier, 'scene', quote, ['E' + identifier]),
                    obj('E' + identifier, 'event', quote)]
    objects += [obj('B99', 'big_event', '甲入城。', ['S99']),
                obj('B1', 'big_event', '甲离城。', ['S1'])]
    before, records = deepcopy(project.graph_snapshot()), deepcopy(project.property_records())
    result = project.apply_changes(objects, [], before['revision'], reason='验证闭合区间缺场景拒写')
    assert result['status'] == 'rejected'
    assert any(row['code'] == 'STORYLINE_MEMBERSHIP' for row in result['diagnostics'])
    assert project.graph_snapshot() == before and project.property_records() == records
    next(row for row in objects if row['id'] == 'B99')['events'].append('S50')
    assert project.apply_changes(objects, [], before['revision'], reason='完整归属后静态通过')['status'] == 'accepted'


def test_operations_accepts_big_event_disclosure_after_member_start(project):
    from narrative.graph_models import Node
    text = '甲在门口。甲进屋。甲离开。'
    sid = project.import_source('披露晚于起点原文', text)

    def obj(identifier, kind, evidence_quote, disclosure_quote, children=()):
        disclosure_start = text.index(disclosure_quote)
        return Node.model_validate({
            'kind': 'node', 'id': identifier, 'node_type': kind, 'name': identifier,
            'context_id': 'canon', 'events': list(children),
            'evidence': {'source_id': sid, 'quote': evidence_quote},
            'disclosure': {'source_id': sid, 'start': disclosure_start},
        }).model_dump()

    objects = [
        obj('E1', 'event', '甲在门口。', '甲在门口。'),
        obj('S1', 'scene', '甲在门口。', '甲在门口。', ['E1']),
        obj('E2', 'event', '甲进屋。', '甲进屋。'),
        obj('S2', 'scene', '甲进屋。', '甲进屋。', ['E2']),
        obj('B1', 'big_event', '甲进屋。', '甲离开。', ['S1', 'S2']),
    ]
    result = project.apply_changes(objects, [], project.graph_snapshot()['revision'],
                                   reason='披露点晚于成员起点仍按成员推导区间')
    assert result['status'] == 'accepted'
    assert not any(row['code'] == 'STORYLINE_DISCLOSURE' for row in result.get('diagnostics', []))
    spans, issues = analyze(project.graph_snapshot())
    assert not issues
    assert next(row for row in spans if row['id'] == 'B1')['start']['start'] == text.index('甲在门口。')


def test_membership_errors_remain_after_disclosure_warning_removal():
    snapshot = graph()
    next(node for node in snapshot['nodes'] if node['id'] == 'B99')['disclosure'] = {
        'source_id': 'SRC1', 'start': 35}
    # A scene in a closed interval that is not owned by its expected B remains invalid.
    next(node for node in snapshot['nodes'] if node['id'] == 'B99')['events'] = []
    issues = analyze(snapshot)[1]
    assert any(row['code'] == 'STORYLINE_MEMBERSHIP' and row['severity'] == 'error'
               for row in issues)


def test_select_completed_story_cuts_at_closing_end_not_last_event_start():
    from narrative.browse_view import browse_view, cut_for
    snapshot = graph()
    story = next(node for node in snapshot['nodes'] if node['id'] == 'T99')
    story['disclosure'] = {'source_id': 'SRC1', 'start': 27}
    cut = cut_for(snapshot, 'T99')
    assert cut['start'] == 29 and cut['event_id'] == 'E50'
    assert cut_for(snapshot, 'E50')['start'] == 20
    assert cut_for(snapshot, 'S50')['start'] == 20
    view = browse_view(snapshot, cut_id='T99', time_filter=True)
    assert 'T99' in {row['id'] for row in view['nodes']}
    assert not {'T1', 'B1', 'E1'} & {row['id'] for row in view['nodes']}
    moments = {row['id']: row for row in moment_tree(snapshot, 'T99')['nodes']}
    assert 'T99' in moments and 'E50' in moments and 'T1' not in moments
    lines = event_lines(snapshot, disclosure_cut=cut)
    assert [row['id'] for row in lines['storylines']] == ['T99']
    assert [row['id'] for row in lines['events']] == ['E99', 'E50']


def test_generic_story_without_summary_disclosure_is_visible_at_own_completed_cut():
    from narrative.browse_view import browse_view, cut_for
    snapshot = graph()
    cut = cut_for(snapshot, 'T99')
    assert cut['start'] == 29
    assert 'T99' in {row['id'] for row in browse_view(snapshot, cut_id='T99', time_filter=True)['nodes']}
    assert 'T99' in {row['id'] for row in moment_tree(snapshot, 'T99')['nodes']}
    assert [row['id'] for row in event_lines(snapshot, disclosure_cut=cut)['storylines']] == ['T99']


def test_completed_major_story_intervals_cannot_overlap():
    snapshot = graph()
    next(node for node in snapshot['nodes'] if node['id'] == 'T99')['completion']['start'] = 35
    assert any(row['code'] == 'STORYLINE_OVERLAP' and row['object_id'] == 'T99'
               for row in analyze(snapshot)[1])


def test_temporal_state_and_navigation_use_same_container_boundary():
    from narrative.browse_view import cut_for
    from narrative.temporal_state import boundary_at
    snapshot = graph()
    for identifier in ['T99', 'B99', 'E99', 'S99']:
        cut = cut_for(snapshot, identifier)
        assert boundary_at(snapshot, identifier) == {'source_id': cut['source_id'], 'start': cut['start']}


def test_empty_container_boundary_never_recurses():
    from narrative.browse_view import cut_for
    from narrative.temporal_state import boundary_at
    snapshot = graph()
    scene = next(node for node in snapshot['nodes'] if node['id'] == 'S99')
    scene['events'] = []
    scene['disclosure'] = {'source_id': 'SRC1', 'start': 10}
    big = next(node for node in snapshot['nodes'] if node['id'] == 'B99')
    assert cut_for(snapshot, 'B99') is None
    assert boundary_at(snapshot, 'B99') is None
    big['disclosure'] = {'source_id': 'SRC1', 'start': 10}
    assert boundary_at(snapshot, 'B99') == {'source_id': 'SRC1', 'start': 10}
    assert boundary_at(snapshot, 'S99') == {'source_id': 'SRC1', 'start': 10}
